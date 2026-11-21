"""Inbound WhatsApp voice agent: one human-sounding assistant, many use cases.

Flow: the call connects -> the agent says a short hello -> it chats like a person (confirms the business name,
"हाँ जी, बताइए") -> as soon as the caller says what they need, the LLM calls `understand_request`, which asks
Sarvam Prism (routing_core) for the use case(s) -> the matched use case's prompt is appended to the agent's
instructions -> it handles the task; a second request waits in a priority queue.

Stack: Deepgram nova-3 (fixed Hindi, as in the ECS agent) STT, Sarvam bulbul:v4-flash TTS, Cerebras gpt-oss LLM.
"""
import asyncio
import json
import logging
import os
import random
import re
import time
from dataclasses import dataclass, field
from pathlib import Path

import aiohttp
from dotenv import load_dotenv
from livekit import api as lk_api
from livekit.agents import (
    Agent,
    AgentServer,
    AgentSession,
    APIConnectOptions,
    AudioConfig,
    BackgroundAudioPlayer,
    BuiltinAudioClip,
    JobContext,
    JobProcess,
    RunContext,
    cli,
    function_tool,
    room_io,
)
from livekit.agents.voice.agent_session import SessionConnectOptions
from livekit.plugins import cerebras, deepgram, noise_cancellation, sarvam, silero

import routing_core as rc
from speech_filter import SpeechCleaner

load_dotenv()
logger = logging.getLogger("vriddhi-inbound")
logger.setLevel(logging.INFO)  # the worker leaves app loggers at WARNING

AGENT_NAME = os.getenv("AGENT_NAME", "Vriddhi-voice-bot")
USECASES_FILE = Path(os.getenv("USECASES_FILE", Path(__file__).parent / "usecases.json"))
TTS_SPEAKER = os.getenv("SARVAM_SPEAKER", "simran_hi_conversation")  # bulbul:v4-flash Hindi, used when a use case sets no voice
CLOSING = "ठीक है, धन्यवाद। नमस्ते!"
TTS_PACE = float(os.getenv("TTS_PACE", "1.15"))  # 1.0 sounded drawn out on the phone (~2 words/s); v4-flash allows 0.5-2.0
STT_LANGUAGE = os.getenv("STT_LANGUAGE", "hi")  # same as ECS STT_LANGUAGE_MAP["hindi"]
# Deepgram keyterm prompting (nova-3, works with "hi"): words it mishears on calls. The business name is added per call.
STT_KEYTERMS = [k.strip() for k in os.getenv(
    "STT_KEYTERMS", "Dolo,Paracetamol,Crocin,Calpol,Azithromycin,Amoxicillin,Pantoprazole,Cetirizine,strip,pickup"
).split(",") if k.strip()]
LLM_MAX_TOKENS = int(os.getenv("LLM_MAX_TOKENS", "1024"))
FALLBACK_LINE = "माफ़ कीजिए, एक बार फिर बताइएगा?"
DIGIT_WORDS = ["शून्य", "एक", "दो", "तीन", "चार", "पाँच", "छह", "सात", "आठ", "नौ"]
# what a caller says before getting to the point ("जी," then a pause): never a request, so no filler and no Prism call
FRAGMENT_WORDS = {"जी", "हाँ", "हां", "हा", "हेलो", "हैलो", "हलो", "नमस्ते", "अच्छा", "हम्म", "सुनिए", "ओके", "भैया", "मैडम", "सर",
                  "ji", "haan", "han", "hello", "hi", "ok", "okay", "achha", "namaste", "sir", "madam"}


def is_fragment(text: str) -> bool:
    words = re.findall(r"[^\s।?!.,;:]+", text.lower())
    return not words or (len(words) <= 2 and all(w in FRAGMENT_WORDS for w in words))

# Same low-latency tuning as the Algotalks ECS agent (algotalks_agent_ec2): open fast, close after 0.3 s of silence.
VAD_OPTS = dict(
    activation_threshold=0.3, deactivation_threshold=0.4, min_silence_duration=0.3,
    prefix_padding_duration=0.3, sample_rate=16000,
)
TURN_HANDLING = {
    # 0.3 s (ECS) cut callers off at every comma pause on WhatsApp ("जी," | "मुझे दवाई..."); 0.5 s lets them finish
    "endpointing": {"min_delay": float(os.getenv("ENDPOINTING_MIN_DELAY", "0.5")), "max_delay": 2.5},
    "interruption": {"mode": "adaptive", "min_duration": 0.6, "min_words": 2},
    "turn_detection": "stt",  # Deepgram's end-of-speech is faster than VAD alone
}

# Sound names from the dashboard (same list as the AlgoTalks context form) -> LiveKit built-in clips.
SOUND_CLIPS = {
    "office_ambience": BuiltinAudioClip.OFFICE_AMBIENCE, "city_ambience": BuiltinAudioClip.CITY_AMBIENCE,
    "crowded_room": BuiltinAudioClip.CROWDED_ROOM, "forest_ambience": BuiltinAudioClip.FOREST_AMBIENCE,
    "keyboard_typing": BuiltinAudioClip.KEYBOARD_TYPING, "keyboard_typing2": BuiltinAudioClip.KEYBOARD_TYPING2,
    "hold_music": BuiltinAudioClip.HOLD_MUSIC,
}
AMBIENT_VOLUME, TOOL_SOUND_VOLUME = 0.12, 0.5  # ECS uses 0.25 / 0.6; on WhatsApp the background was too loud


@dataclass
class CallState:
    ctx: JobContext
    usecases: list[dict]
    settings: dict
    http: aiohttp.ClientSession
    queue: list[str] = field(default_factory=list)  # waiting use case ids, priority order
    current: dict | None = None  # the use case being handled right now
    routing_turns: int = 0
    session_tts_key: tuple = ("", "hi")  # (voice, language) of the session-wide TTS
    sounds_started: bool = False
    last_filler: str = ""
    submitted: set = field(default_factory=set)  # use case ids saved with submit_request in this call
    end_warned: bool = False  # end_call was refused once because the current request was not saved yet

    def unsaved(self) -> bool:
        return self.current is not None and self.current["usecaseId"] not in self.submitted


def _by_id(state: CallState, uid: str) -> dict:
    return next(u for u in state.usecases if u["usecaseId"] == uid)


def _spoken(uc: dict) -> str:
    return uc.get("spokenName") or uc["name"]


def load_usecases() -> tuple[list[dict], dict]:
    data = json.loads(USECASES_FILE.read_text(encoding="utf-8"))
    return data["usecases"], data["settings"]


def default_usecase(usecases: list[dict], settings: dict) -> dict:
    return next((u for u in usecases if u["usecaseId"] == settings.get("defaultUsecaseId")), usecases[0])


def scope_names(state: "CallState") -> str:
    """The enabled voice use cases as the agent says them, e.g. "शिकायत, दवाई का ऑर्डर और अपॉइंटमेंट"."""
    names = [_spoken(u) for u in rc.enabled_voice(state.usecases)]
    return names[0] if len(names) == 1 else ", ".join(names[:-1]) + " और " + names[-1] if names else ""


def tts_key(uc: dict) -> tuple:
    return (uc.get("voice") or TTS_SPEAKER, uc.get("language") or "hi")


def make_llm() -> cerebras.LLM:
    """Same client settings as the ECS agent. msgpack off and one tool call at a time matter: with the plugin
    defaults the model leaked raw tool-call JSON and its own reasoning into the spoken reply. Temperature is
    lower than ECS (0.7) for steadier, shorter replies."""
    return cerebras.LLM(
        model="gpt-oss-120b", temperature=0.5, tool_choice="auto", parallel_tool_calls=False,
        reasoning_effort="low", gzip_compression=True, msgpack_encoding=False,
        # includes reasoning tokens. A normal turn uses well under 300; a runaway thinking loop stalled a real call
        # for 10 s (first token, then nothing until the timeout). Capped, it ends in well under a second instead.
        max_completion_tokens=LLM_MAX_TOKENS,
    )


def make_tts(uc: dict) -> sarvam.TTS:
    voice, lang = tts_key(uc)
    return sarvam.TTS(
        target_language_code="en-IN" if lang == "en" else "hi-IN", model="bulbul:v4-flash",
        speaker=voice, pace=TTS_PACE, speech_sample_rate=24000,
    )


# --- prompt ---------------------------------------------------------------------------------------------

def persona(state: CallState) -> str:
    """Who the agent is and how it talks: a person on the phone, not a bot."""
    d = default_usecase(state.usecases, state.settings)
    company = state.settings.get("companyName") or "Rekon Sales"
    if (d.get("language") or "hi") == "en":
        speak, who = "Speak natural, casual spoken English", ""
    else:
        speak = (
            "Speak natural, casual spoken Hindi, the way an Indian office or sales person really talks. Common English "
            "words (order, appointment, ok, sir, problem) are fine, as people mix them, BUT write every single word in "
            "Devanagari so the voice pronounces it right: English and brand words too (paracetamol -> पैरासिटामोल, "
            "azithromycin -> एज़िथ्रोमाइसिन, Dolo -> डोलो, strip -> स्ट्रिप, mg -> एमजी, pickup -> पिकअप, ok -> ओके). "
            "Write numbers as Hindi words (650 -> छह सौ पचास, 2 -> दो). Never use Latin letters or digits"
        )
        who = (
            "You are a woman: refer to yourself in the feminine (करती हूँ, समझ गई, बता दूँगी). "
            if (d.get("gender") or "female") == "female"
            else "You are a man: refer to yourself in the masculine (करता हूँ, समझ गया, बता दूँगा). "
        )
    return (
        f"You are a real person answering the phone at {company}. This is a WhatsApp voice call, so talk the way "
        "people talk on the phone, not like a bot.\n\n"
        "How you sound:\n"
        f"- {speak}.\n"
        "- Keep every reply short: usually one short sentence, two at most. Never give lists or long explanations.\n"
        "- Sound human: use a small natural acknowledgement now and then (हाँ जी, जी, हम्म, अच्छा, ठीक है, जी बिल्कुल), "
        "but never string several together and do not start every reply with the same one.\n"
        f"- {who}Ask one question at a time. Do not repeat the caller's words back to them in full.\n"
        f"- If the caller only says hello, say hello back in a word and wait. If they ask whether this is {company}, "
        "confirm with a yes and ask them to go ahead (हाँ जी, बताइए), and stop there. "
        "Do not give a speech about yourself, do not list what you can do, and do not say you are an AI or an assistant "
        "unless they ask you directly.\n"
        "- Give exactly ONE reply per turn, then stop and wait. Never write what the caller might say next and never "
        "carry the conversation on by yourself. If you did not catch something, just ask them to repeat.\n"
        "- Before you look something up, the system already says a short filler for you, so do not add one yourself.\n"
        "- Output ONLY the exact words to be spoken aloud. Never write stage directions, notes, text in brackets, "
        "\"waiting for the caller\", ellipsis runs or emojis: everything you write is read out by a voice.\n"
        "- Never read out JSON, ids, tool names or tool results. Never invent prices, stock, timings or availability.\n\n"
        f"What you can help with on this call: {scope_names(state)}. Nothing else.\n"
        "- If the caller wants anything else, do not end the call: say sorry politely, say in a few words what you can "
        "help with, and ask if they need any of it.\n"
        "- Only end the call when the caller says goodbye or clearly asks to end it. When they are done (no, nothing "
        "else, thank you, bye), call end_call straight away: it says the goodbye for you, so do not say one yourself.\n\n"
        "How you find out what the caller needs:\n"
        "- Greetings, small talk and questions about who you are (is this the right number or business) are NOT a need: just answer them, do not call the tool. The moment they tell you what they want "
        "or what their problem is, call understand_request with their words in their own language, before you answer "
        "the substance.\n"
        "- Whenever the caller brings up a NEW need, even in the middle of another task or as an add-on (\"और मुझे ... भी चाहिए\", "
        "\"एक और बात\"), call understand_request again with just that new need. Do not wait for the current task to finish.\n"
        "- When it returns, carry on naturally from what it says. Never mention the tool."
    )


def compose(state: CallState, uc: dict | None) -> str:
    """Base persona, plus the matched use case's prompt appended once the caller's need is known."""
    base = persona(state)
    if uc is None:
        return base
    slots = ", ".join(uc.get("slots") or []) or "none"
    waiting = [_spoken(_by_id(state, i)) for i in state.queue]
    return (
        f"{base}\n\n--- CURRENT TASK: {uc['name']} ---\n{uc['prompt']}\n"
        f"Details to collect, one at a time and only if the caller has not already said them: {slots}.\n"
        "When you have the details you MUST call submit_request with a short summary: nothing is saved otherwise. "
        "Never say a reference number yourself: only the one submit_request returns, read out exactly as it gives it. "
        "When this task is complete, call finish_task. If the caller wants to hang up, call end_call."
        + (f"\nAfter this task you will also take care of: {', '.join(waiting)}." if waiting else "")
    )


FILLERS = {
    "female": ["हम्म...", "जी, एक सेकंड।", "अच्छा, देखती हूँ।", "हाँ जी, एक मिनट।"],
    "male": ["हम्म...", "जी, एक सेकंड।", "अच्छा, देखता हूँ।", "हाँ जी, एक मिनट।"],
    "en": ["Hmm, one sec.", "Okay, let me see.", "Sure, one moment."],
}


def pick_filler(state: CallState) -> str:
    d = default_usecase(state.usecases, state.settings)
    pool = FILLERS["en"] if (d.get("language") or "hi") == "en" else FILLERS["male" if d.get("gender") == "male" else "female"]
    choice = random.choice([f for f in pool if f != state.last_filler] or pool)  # never the same one twice in a row
    state.last_filler = choice
    return choice


# --- call plumbing --------------------------------------------------------------------------------------

async def apply_sounds(state: CallState, uc: dict, session: AgentSession) -> None:
    """Background / tool sound for the call: set once when the call connects (from the default or first use case), not swapped mid-call."""
    if state.sounds_started:
        return
    state.sounds_started = True
    bg, tool = SOUND_CLIPS.get(uc.get("backgroundSound") or ""), SOUND_CLIPS.get(uc.get("toolSound") or "")
    if not (bg or tool):
        return
    try:
        player = BackgroundAudioPlayer(
            ambient_sound=AudioConfig(bg, volume=AMBIENT_VOLUME) if bg else None,
            thinking_sound=AudioConfig(tool, volume=TOOL_SOUND_VOLUME) if tool else None,
        )
        await player.start(room=state.ctx.room, agent_session=session)
        state.ctx.add_shutdown_callback(player.aclose)
        logger.info("SOUNDS | usecase=%s background=%s tool=%s", uc["usecaseId"], uc.get("backgroundSound"), uc.get("toolSound"))
    except Exception as exc:
        logger.error("SOUNDS_FAILED | %s", exc)  # a missing clip must never break the call


async def hangup_whatsapp(ctx: JobContext) -> None:
    """Tell Meta the call ended (billing); deleting the room alone is not enough. No-op for non-WhatsApp rooms."""
    call_id = None
    for raw in (ctx.room.metadata, ctx.job.metadata):  # room metadata first, dispatch metadata as fallback
        try:
            call_id = call_id or json.loads(raw or "{}").get("whatsapp_call_id")
        except ValueError:
            pass
    token = os.getenv("WHATSAPP_ACCESS_TOKEN", "")
    if not call_id:
        return
    if not token:
        logger.error("WHATSAPP_HANGUP_SKIPPED | WHATSAPP_ACCESS_TOKEN not set | call_id=%s", call_id)
        return
    try:
        await ctx.api.connector.disconnect_whatsapp_call(
            lk_api.DisconnectWhatsAppCallRequest(
                whatsapp_call_id=call_id,
                whatsapp_api_key=token,
                disconnect_reason=lk_api.DisconnectWhatsAppCallRequest.BUSINESS_INITIATED,
            )
        )
        logger.info("WHATSAPP_HANGUP_SENT | call_id=%s", call_id)
    except Exception as exc:
        logger.error("WHATSAPP_HANGUP_FAILED | call_id=%s | error=%s", call_id, exc)


REF_RE = re.compile(r"रेफ़?रेंस|रेफ़\s*\d|संदर्भ संख्या|reference", re.I)
SAVE_NUDGE = (
    "You have not saved the caller's request yet. Call submit_request now with the details they gave, then read out "
    "the reference number it returns. Never make a reference number up."
)
LEAKED_GOODBYE_RE = re.compile(r'"reason"\s*:|\bgood ?bye\b|\bbye\b|have a (nice|good|great) day', re.I)
GOODBYE_RE = re.compile(r"अलविदा|दिन शुभ|शुभ दिन|शुभ रहे|बाय|फिर मिलेंगे|ध्यान रखिए|ख़्याल रखिए|ख्याल रखिए")


def is_goodbye(text: str) -> bool:
    """A closing line with no question in it: the conversation is over."""
    t = text.strip()
    return bool(t) and "?" not in t and (bool(GOODBYE_RE.search(t)) or t.rstrip("।!. ").endswith(("धन्यवाद", "नमस्ते")))


async def end_call(agent: Agent, state: CallState, reason: str, closing: str = "", say: bool = True) -> str:
    """Say goodbye, hang up WhatsApp, close the room."""
    try:
        if say:
            await agent.session.say(closing or CLOSING, allow_interruptions=False)
        await hangup_whatsapp(state.ctx)
        await asyncio.wait_for(state.ctx.delete_room(), timeout=8)
    except Exception as exc:
        logger.error("END_CALL_FAILED | reason=%s | error=%s", reason, exc)
    finally:
        agent.session.shutdown(drain=False)
        state.ctx.shutdown(reason or "call completed")
    return "Call ended."


# --- the agent ------------------------------------------------------------------------------------------

class Assistant(Agent):
    """One agent for the whole call. A matched use case's prompt is appended to its instructions in place; a new
    Assistant is only created when that use case speaks in a different voice (TTS is fixed per agent)."""

    def __init__(self, state: CallState, usecase: dict | None = None, chat_ctx=None, announce: str = ""):
        kw = {}
        self.voice_key = state.session_tts_key
        if usecase and tts_key(usecase) != state.session_tts_key:
            kw["tts"], self.voice_key = make_tts(usecase), tts_key(usecase)
        if chat_ctx is not None:
            kw["chat_ctx"] = chat_ctx
        super().__init__(instructions=compose(state, usecase), **kw)
        self.state, self.usecase, self.announce = state, usecase, announce

    async def llm_node(self, chat_ctx, tools, model_settings):
        """Clean the reply before it is spoken or stored (see speech_filter): the model sometimes appends repeats,
        English notes or bracketed junk after a good first sentence, mostly right after a tool call."""
        # a reference number while nothing is saved yet was invented (a real call heard "रेफ़12345"): never say it
        cleaner = SpeechCleaner(reject=lambda s: self.state.unsaved() and bool(REF_RE.search(s)))
        spoke = acted = False
        said, raw = [], []
        try:
            async for chunk in Agent.default.llm_node(self, chat_ctx, tools, model_settings):
                if isinstance(chunk, str):
                    raw.append(chunk)
                    if out := cleaner.feed(chunk):
                        spoke = True
                        said.append(out)
                        yield out
                elif chunk.delta is not None and chunk.delta.content:
                    raw.append(chunk.delta.content)
                    chunk.delta.content = cleaner.feed(chunk.delta.content) or None
                    spoke = spoke or bool(chunk.delta.content)
                    said.append(chunk.delta.content or "")
                    yield chunk
                else:
                    acted = acted or bool(chunk.delta and chunk.delta.tool_calls)
                    yield chunk
        except Exception as exc:  # e.g. the Cerebras stream stalled and timed out: say something rather than go silent
            logger.error("LLM_FAILED | %s", exc)
        if tail := cleaner.flush():
            spoke = True
            said.append(tail)
            yield tail
        can_hang_up = self.state.current is not None and (not self.state.unsaved() or self.state.end_warned)
        leaked_goodbye = not spoke and not acted and bool(LEAKED_GOODBYE_RE.search("".join(raw)))
        if (cleaner.rejected or leaked_goodbye) and self.state.unsaved() and not self.state.end_warned:
            # it tried to give a made-up reference number, or to hang up, with the request not saved: make it save
            self.state.end_warned = True
            logger.warning("SAVE_NUDGE | usecase=%s | rejected_ref=%s leaked_goodbye=%s", self.state.current["usecaseId"], cleaner.rejected, leaked_goodbye)
            if not spoke:
                yield "जी, एक सेकंड।"
            asyncio.create_task(self.session.generate_reply(instructions=SAVE_NUDGE))
        elif leaked_goodbye and can_hang_up:
            # everything was filtered, but it was a goodbye: English ("Thanks, goodbye.") and/or end_call typed out
            # as text ({"reason": ...}) instead of called. Say the Hindi closing and hang up rather than "please repeat".
            logger.info("GOODBYE_RECOVERED | raw=%r", "".join(raw)[:200])
            yield (self.state.current or {}).get("closing") or CLOSING
            asyncio.create_task(self._hang_up_after_goodbye())
        elif not spoke and not acted:  # nothing to say and no tool called (empty, cut off at the token cap, or failed)
            logger.warning("LLM_EMPTY_REPLY | using fallback line | raw=%r", "".join(raw)[:300])
            yield FALLBACK_LINE
        elif not acted and self.state.current is not None and is_goodbye("".join(said)) and (
            not self.state.unsaved() or self.state.end_warned  # never hang up on an order that was not saved
        ):
            # only once a task has started: "नमस्ते" as a greeting hung up a real call within 3 seconds
            # the model says goodbye but often forgets end_call, leaving the caller on a silent line: hang up for it
            logger.info("GOODBYE_DETECTED | hanging up after it is spoken")
            asyncio.create_task(self._hang_up_after_goodbye())

    async def _hang_up_after_goodbye(self) -> None:
        await asyncio.sleep(0.3)  # let the goodbye speech get scheduled
        if (speech := self.session.current_speech) is not None:
            await speech.wait_for_playout()
        await asyncio.sleep(0.4)
        await end_call(self, self.state, "caller done", say=False)

    async def on_enter(self) -> None:
        st = self.state
        if self.usecase is None:  # start of the call: just a short hello, then listen
            await self.session.say(rc.build_opening(st.settings, st.usecases), allow_interruptions=True)
            return
        st.current = self.usecase  # entered through a voice change
        asyncio.create_task(apply_sounds(st, self.usecase, self.session))
        await self.session.generate_reply(instructions=self.announce or "Carry on with the task naturally.")

    async def _enter(self, uc: dict, plan: list[str] | None = None, moving_on: bool = False):
        """Switch to a use case. Returns a hint string for the LLM, or a new Assistant if the voice changes."""
        st = self.state
        st.current = uc
        hint = f"Task identified: {uc['name']}. Carry on naturally and do not mention any routing."
        if moving_on:
            hint = f"The caller's next need is {uc['name']}. Say briefly and naturally that you will do that now, then begin."
        elif plan and len(plan) > 1:
            names = " और ".join(_spoken(_by_id(st, i)) for i in plan)
            hint += f" They need more than one thing ({names}). Say briefly that you will take care of all of it, starting with this one."
        if uc.get("greeting"):
            hint += f' Begin with exactly this line: "{uc["greeting"]}".'
        logger.info("PROMPT_APPENDED | usecase=%s waiting=%s prompt_chars=%d", uc["usecaseId"], st.queue, len(uc.get("prompt") or ""))
        if tts_key(uc) != self.voice_key:
            return Assistant(st, uc, chat_ctx=self.chat_ctx, announce=hint)  # new voice: the prompt goes in via the new agent
        await self.update_instructions(compose(st, uc))  # same voice: persona + this use case's prompt, in place
        asyncio.create_task(apply_sounds(st, uc, self.session))
        return hint

    @function_tool(
        description=(
            "Call this as soon as the caller says what they want or what their problem is (not for plain greetings or "
            "small talk). Pass their words in their own language. Returns what to do next."
        )
    )
    async def understand_request(self, context: RunContext, request: str):
        st = self.state
        if is_fragment(request):  # "जी," before a pause: the caller is still talking
            logger.info("ROUTE_SKIPPED | fragment=%s", request)
            return "That was not a request yet, the caller is still talking. Reply with at most one word (जी) and wait."
        st.routing_turns += 1
        filler = pick_filler(st)  # said while Prism and the next LLM call run, like a person saying "hmm, let me see"
        try:
            if filler:
                context.session.say(filler, allow_interruptions=False, add_to_chat_ctx=False)
                logger.info("FILLER | %s", filler)
        except Exception as exc:
            logger.warning("FILLER_FAILED | %s", exc)
        result = await self._route_request(request)
        note = " You already said a short filler to the caller: do not say another one, go straight to the point."
        if isinstance(result, str):
            return result + note
        result.announce += note  # voice change: the new Assistant speaks next
        return result

    async def _route_request(self, request: str):
        st = self.state
        t0 = time.perf_counter()
        try:
            scores = await rc.classify(st.http, request, st.usecases, os.environ["SARVAM_API_KEY"])
            decision = rc.decide(scores, st.usecases, st.settings)
            logger.info("ROUTE | kind=%s queue=%s turn=%d prism_ms=%d scores=%s", decision.kind, decision.queue, st.routing_turns, (time.perf_counter() - t0) * 1000, json.dumps(scores))
        except (rc.PrismError, KeyError) as exc:
            logger.error("ROUTE_FAILED | %s", exc)
            decision = rc.Decision("unclear")

        scope = scope_names(st)
        if decision.kind == "route":
            st.queue, st.routing_turns = decision.queue[1:], 0
            return await self._enter(_by_id(st, decision.queue[0]), plan=decision.queue)
        if decision.kind == "out_of_scope":
            logger.info("ROUTE_OUT_OF_SCOPE | request=%s", request)
            return (
                "This is not something you handle. Do NOT end the call. In at most two short sentences: politely say sorry, "
                f"you cannot help with that here; then say you can help with {scope}, and ask if they need any of these."
            )
        if st.routing_turns >= st.settings["maxRoutingTurns"]:
            did = st.settings.get("defaultUsecaseId")
            if did and any(u["usecaseId"] == did for u in st.usecases):
                logger.info("ROUTE_DEFAULT | usecase=%s", did)
                st.queue = []
                return await self._enter(_by_id(st, did))
            return f"Still not clear. Do NOT end the call. In one short sentence say you can help with {scope}, and ask which one they need."
        return "It is not clear yet what they need. Ask one short, natural question to find out. Do not list options."

    @function_tool(description=(
        "Save the caller's order, complaint or appointment once all details are collected. This is the ONLY way it gets "
        "recorded and the only source of a reference number: never make one up. The summary is read by staff, not spoken: "
        "write it in plain English with ordinary digits, exactly what the caller said (छह सौ पचास = 650, दो पत्ती = 2 strips), "
        "e.g. 'Paracetamol 650 mg x 5 strips; pickup'."
    ))
    async def submit_request(self, context: RunContext, summary: str) -> str:
        ref = str(random.randint(10000, 99999))  # mock: no vendor backend wired yet
        spoken = ", ".join(DIGIT_WORDS[int(d)] for d in ref)  # "R E Q - 1 1 1 7 8" in Latin was read out badly
        logger.info("SUBMIT_REQUEST | usecase=%s ref=%s summary=%s", (self.state.current or {}).get("usecaseId"), ref, summary)
        if self.state.current is not None:
            self.state.submitted.add(self.state.current["usecaseId"])
        return (
            f'Saved. Tell the caller their reference number, written exactly like this: "{spoken}". '
            "If they have already said they need nothing else, call end_call right after saying it."
        )

    @function_tool(description="Call when the current task is complete. Moves to the next waiting task if there is one.")
    async def finish_task(self, context: RunContext):
        st = self.state
        logger.info("TASK_DONE | usecase=%s next=%s", (st.current or {}).get("usecaseId"), st.queue[:1])
        if st.queue:
            return await self._enter(_by_id(st, st.queue.pop(0)), moving_on=True)
        return (
            "Nothing else is waiting. Ask in a few words if they need anything else. If they ask for something, call "
            "understand_request. If not, call end_call (it says the goodbye, do not say one yourself)."
        )

    @function_tool(description="End the phone call. ONLY when the caller says goodbye or clearly asks to hang up, never because a request is out of scope.")
    async def end_call(self, context: RunContext, reason: str) -> str:
        st = self.state
        if st.unsaved() and not st.end_warned:  # a real call hung up with the whole order collected but never saved
            st.end_warned = True
            logger.warning("END_CALL_BLOCKED | usecase=%s not saved yet", st.current["usecaseId"])
            return (
                f"Not saved yet: the caller's {st.current['name']} has not been recorded. If they gave the details and did "
                "not cancel, call submit_request now, tell them the reference number, then call end_call. "
                "If they cancelled, call end_call again."
            )
        return await end_call(self, st, reason, (st.current or {}).get("closing", ""))


server = AgentServer()


def prewarm(proc: JobProcess) -> None:
    proc.userdata["vad"] = silero.VAD.load(**VAD_OPTS)


server.setup_fnc = prewarm


@server.rtc_session(agent_name=AGENT_NAME)
async def entrypoint(ctx: JobContext) -> None:
    ctx.log_context_fields = {"room": ctx.room.name}
    await ctx.connect()
    usecases, settings = load_usecases()
    channel = "sip"
    try:
        channel = str(json.loads(ctx.job.metadata or "{}").get("channel") or "sip").lower()
    except ValueError:
        pass

    # the caller must be in the room before we speak, otherwise the opening is clipped
    try:
        await asyncio.wait_for(ctx.wait_for_participant(), timeout=30)
    except asyncio.TimeoutError:
        logger.warning("CALL_FAILED | no participant joined | room=%s", ctx.room.name)
        ctx.shutdown("no participant")
        return

    async with aiohttp.ClientSession() as http:
        default_uc = default_usecase(usecases, settings)  # its voice speaks the whole call unless a use case sets its own
        state = CallState(ctx=ctx, usecases=usecases, settings=settings, http=http, session_tts_key=tts_key(default_uc))
        asyncio.create_task(rc.warm_up(http))
        session = AgentSession(
            # Fixed language like the ECS agent (hindi -> "hi"). "multi" guessed Indonesian for a Hindi caller.
            # utterance_end_ms: on a WhatsApp line Deepgram can send the final text but never its end-of-speech signal,
            # and with turn_detection "stt" the turn then never ends (a real call waited 10 s). UtteranceEnd is word
            # timing based, so it still fires on a noisy line, at most ~1 s after the last word.
            stt=deepgram.STT(
                model="nova-3", language=STT_LANGUAGE, interim_results=True, punctuate=True, utterance_end_ms=1000,
                keyterm=[settings.get("companyName") or "Rekon Sales", *STT_KEYTERMS],
            ),
            llm=make_llm(),
            tts=make_tts(default_uc),
            vad=ctx.proc.userdata["vad"],
            max_tool_steps=3,
            turn_handling=TURN_HANDLING,
            # shorter than the 10 s defaults so a stalled stream is retried or given up on quickly
            conn_options=SessionConnectOptions(
                llm_conn_options=APIConnectOptions(max_retry=1, retry_interval=0.1, timeout=5.0),
                tts_conn_options=APIConnectOptions(max_retry=2, retry_interval=0.1, timeout=5.0),
            ),
        )
        done = asyncio.Event()

        async def _release() -> None:  # keep `http` open for the whole call
            done.set()

        ctx.add_shutdown_callback(_release)

        # diagnostics for real calls: what STT hears and when each side speaks (temporary, it is call content)
        last_interim = [0.0]

        @session.on("user_input_transcribed")
        def _log_transcript(ev) -> None:
            if ev.is_final and ev.transcript.strip():
                logger.info("CALLER_SAID | %s", ev.transcript.strip())
            elif not ev.is_final and time.monotonic() - last_interim[0] > 0.7:  # interims arrive every ~100 ms
                last_interim[0] = time.monotonic()
                logger.info("CALLER_INTERIM | %s", ev.transcript.strip())

        @session.on("user_state_changed")
        def _log_user_state(ev) -> None:
            logger.info("USER_STATE | %s -> %s", ev.old_state, ev.new_state)

        @session.on("agent_state_changed")
        def _log_agent_state(ev) -> None:
            logger.info("AGENT_STATE | %s -> %s", ev.old_state, ev.new_state)

        @session.on("conversation_item_added")
        def _log_item(ev) -> None:
            item = ev.item
            if getattr(item, "role", None) == "assistant":
                logger.info("AGENT_SAID | %s", (getattr(item, "text_content", "") or "")[:200])
                if getattr(item, "metrics", None):
                    logger.info("LATENCY | %s", json.dumps(dict(item.metrics), default=str))

        await session.start(
            agent=Assistant(state),
            room=ctx.room,
            room_options=room_io.RoomOptions(
                audio_input=room_io.AudioInputOptions(
                    noise_cancellation=noise_cancellation.BVC() if channel == "whatsapp" else noise_cancellation.BVCTelephony()
                )
            ),
        )
        asyncio.create_task(apply_sounds(state, default_uc, session))  # background from the first second, not after routing
        await done.wait()


if __name__ == "__main__":
    cli.run_app(server)
