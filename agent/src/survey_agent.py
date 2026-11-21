import logging
import sys
import os
import time
from datetime import datetime
from dotenv import load_dotenv
from livekit.agents import (
    AudioConfig,
    BackgroundAudioPlayer,
    BuiltinAudioClip,
    Agent,
    AgentServer,
    AgentSession,
    JobContext,
    cli,
    room_io,
    function_tool,
    RunContext,
)
from livekit import api as lk_api
from livekit.plugins import noise_cancellation
from ai_clients import create_sarvam_tts, create_stt, create_vad, create_cerebras_llm
import asyncio
from tools import _perform_search
import json
import aiohttp
from survey import SurveyManager
from context_manager import download_context, cleanup_context
import random
import watchtower
import boto3

# ================== CONFIG ==================
LOG_FORMAT = "%(asctime)s | %(levelname)s | %(name)s | %(message)s"
LOG_GROUP = os.getenv("CW_LOG_GROUP", "/vriddhi/agent")
AWS_REGION = os.getenv("AWS_REGION", "ap-south-1")

AGENT_NAME = "Vriddhi-voice-bot"  # must match dispatcher/app.py
TRANSCRIPT_URL = os.getenv("TRANSCRIPT_URL", "")

# background_noise metadata value -> built-in clip; anything else (or missing) = none
NOISE_CLIPS = {"office": BuiltinAudioClip.OFFICE_AMBIENCE}
DEFAULT_NOISE_VOLUME = 0.4

LANGUAGE_MAP = {
    "english": "en-IN",
    "hindi": "hi-IN",
    "malayalam": "ml-IN",
}

VOICE_MAP = {
    "female": "priya",
    "male": "shubh",
}


STT_LANGUAGE_MAP = {
    "english": "en-IN",
    "hindi": "hi",
    "malayalam": "ml",
}

LOG_DIR = "./logs"
os.makedirs(LOG_DIR, exist_ok=True)

# ================== ROOT LOGGER ==================
logging.basicConfig(
    level=logging.INFO,
    format=LOG_FORMAT,
    stream=sys.stdout,
    force=True,
)

sys.stdout.reconfigure(line_buffering=True)

# shared cw client (IMPORTANT - same as before)
_cw_client = boto3.client("logs", region_name=AWS_REGION)

# ================== LOGGER ==================
def create_call_logger(phone_number: str, session_id: str) -> logging.Logger:
    ts = datetime.utcnow().strftime("%Y-%m-%d_%H-%M-%S-%f")
    safe_phone = phone_number.replace("+", "").replace(" ", "")
    short_session = session_id[:8]

    filename = f"call_{safe_phone}_{short_session}_{ts}.log"
    path = os.path.join(LOG_DIR, filename)

    logger = logging.getLogger(f"call_{short_session}")
    logger.setLevel(logging.INFO)
    logger.propagate = False

    if logger.handlers:
        return logger

    formatter = logging.Formatter(LOG_FORMAT)

    # ✅ FILE HANDLER (your current)
    fh = logging.FileHandler(path, encoding="utf-8")
    fh.setFormatter(formatter)

    # ✅ CONSOLE HANDLER
    sh = logging.StreamHandler(sys.stdout)
    sh.setFormatter(formatter)

    # ✅ CLOUDWATCH HANDLER (RESTORED EXACT BEHAVIOR)
    cw_handler = watchtower.CloudWatchLogHandler(
        log_group=LOG_GROUP,
        log_stream_name=f"phone-{phone_number}",
        boto3_client=_cw_client,
        create_log_group=True,
    )
    cw_handler.setFormatter(formatter)

    logger.addHandler(fh)
    logger.addHandler(sh)
    logger.addHandler(cw_handler)

    return logger


def close_logger(logger: logging.Logger) -> None:
    for handler in logger.handlers[:]:
        handler.flush()
        handler.close()
        logger.removeHandler(handler)


def _json_for_log(data) -> str:
    return json.dumps(data, ensure_ascii=False, default=str)


def log_session_history(session: AgentSession, logger: logging.Logger, label: str) -> None:
    try:
        raw_data = session.history.to_dict(exclude_function_call=False)
        items = raw_data.get("items", [])
        logger.info("CONVERSATION_HISTORY_START | label=%s | items=%d", label, len(items))
        for index, item in enumerate(items):
            logger.info(
                "CONVERSATION_HISTORY_ITEM | label=%s | index=%d | data=%s",
                label,
                index,
                _json_for_log(item),
            )
        logger.info("CONVERSATION_HISTORY_END | label=%s", label)
    except Exception as exc:
        logger.exception("CONVERSATION_HISTORY_LOG_FAILED | label=%s | error=%s", label, exc)


def _conversation_text(item) -> str:
    text = getattr(item, "text_content", None)
    if text:
        return text.strip()

    content = getattr(item, "content", None)
    if isinstance(content, list):
        parts = [part.strip() for part in content if isinstance(part, str) and part.strip()]
        return "\n".join(parts)

    return ""


def attach_conversation_logging(session: AgentSession, logger: logging.Logger) -> None:
    logged_user_texts: set[str] = set()

    @session.on("user_input_transcribed")
    def _log_user_question(event) -> None:
        if not getattr(event, "is_final", False):
            return

        text = (getattr(event, "transcript", "") or "").strip()
        if not text:
            return

        logged_user_texts.add(text)
        logger.info("CONVERSATION_USER_QUESTION | text=%s", text)

    @session.on("conversation_item_added")
    def _log_conversation_item(event) -> None:
        item = getattr(event, "item", None)
        role = getattr(item, "role", None)
        text = _conversation_text(item)

        if not text:
            return

        if role == "assistant":
            logger.info("CONVERSATION_LLM_RESPONSE | text=%s", text)
        elif role == "user" and text not in logged_user_texts:
            logged_user_texts.add(text)
            logger.info("CONVERSATION_USER_QUESTION | text=%s", text)


async def hangup_whatsapp(ctx: JobContext, logger) -> None:
    """Tell Meta we ended the call (needed for correct billing; deleting the room alone isn't enough).
    No-op for SIP calls: they have no whatsapp_call_id in the room metadata."""
    try:
        call_id = json.loads(ctx.room.metadata or "{}").get("whatsapp_call_id")
    except ValueError:
        call_id = None
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


def build_agent_instructions(instructions: str) -> str:
    return (
        "Critical operating rules:\n"
        "- For every flight availability, timing, provider, price, route, date, or reschedule question, "
        "call search_knowledge_base before answering.\n"
        "- Use only facts returned by search_knowledge_base. If the search returns no exact matching flight "
        "or the tool fails, say you cannot verify an available flight right now. Do not invent flight details.\n"
        "- Ask for only one missing booking detail at a time.\n"
        "- Never speak JSON, tool arguments, function names, or raw tool results to the caller.\n"
        "- When the caller says goodbye, thank you, or asks to end the call, call end_session. "
        "Do not say or print {\"reason\": ...}.\n\n"
        f"{instructions}"
    )

# ================== LOAD ENV ==================
load_dotenv(".env")

# ================== AGENT ==================
class SurveyAssistant(Agent):
    def __init__(
        self,
        instructions: str,
        knowledge_base_id: str,
        job_context: JobContext,
        survey_manager: SurveyManager,
        logger,
        closing: str
    ) -> None:
        super().__init__(
            instructions=build_agent_instructions(instructions),
        )
        self.knowledge_base_id = knowledge_base_id
        self.job_context = job_context
        self.survey_manager = survey_manager
        self.logger = logger
        self.closing = closing or "Thank you for calling. Goodbye."

    @function_tool(
        description=(
            "Search the knowledge base for flight availability, route, date, timing, "
            "provider, price, or booking policy. Use this before answering any flight fact."
        )
    )
    async def search_knowledge_base(self, context: RunContext, query: str) -> str:
        started = time.monotonic()
        self.logger.info(
            "TOOL_CALL_START | name=search_knowledge_base | query=%s | kb_id_present=%s",
            query,
            bool(self.knowledge_base_id),
        )

        try:
            result = await _perform_search(query, self.knowledge_base_id, log=self.logger)
        except Exception as exc:
            elapsed_ms = int((time.monotonic() - started) * 1000)
            self.logger.exception(
                "TOOL_CALL_FAILED | name=search_knowledge_base | elapsed_ms=%d | query=%s | error=%s",
                elapsed_ms,
                query,
                exc,
            )
            return (
                "Knowledge-base search failed. Do not invent flight details. "
                "Tell the caller you cannot verify an available flight right now."
            )

        if not result:
            elapsed_ms = int((time.monotonic() - started) * 1000)
            self.logger.warning(
                "TOOL_CALL_EMPTY | name=search_knowledge_base | elapsed_ms=%d | query=%s",
                elapsed_ms,
                query,
            )
            return (
                "No matching knowledge-base result was found. Do not invent flight details. "
                "Tell the caller no verified matching flight is available."
            )

        elapsed_ms = int((time.monotonic() - started) * 1000)
        self.logger.info(
            "TOOL_CALL_RESULT | name=search_knowledge_base | elapsed_ms=%d | chars=%d | result=%s",
            elapsed_ms,
            len(result),
            result,
        )
        return result

    @function_tool(description="Get the next survey or booking question that should be asked.")
    async def get_next_survey_question(self, context: RunContext, trigger: str = "start") -> str:
        self.logger.info("TOOL_CALL_START | name=get_next_survey_question | trigger=%s", trigger)
        try:
            result = self.survey_manager.get_next_question_text()
            self.logger.info("TOOL_CALL_RESULT | name=get_next_survey_question | result=%s", result)
            return result
        except Exception as exc:
            self.logger.exception("TOOL_CALL_FAILED | name=get_next_survey_question | error=%s", exc)
            return "I am unable to get the next question right now."

    @function_tool(description="Submit the caller's answer to the current survey or booking question.")
    async def submit_survey_answer(self, context: RunContext, user_response: str) -> str:
        self.logger.info("TOOL_CALL_START | name=submit_survey_answer | response=%s", user_response)
        try:
            result = self.survey_manager.submit_answer(user_response)
            self.logger.info("TOOL_CALL_RESULT | name=submit_survey_answer | result=%s", result)
            return result
        except Exception as exc:
            self.logger.exception("TOOL_CALL_FAILED | name=submit_survey_answer | error=%s", exc)
            return "I am unable to save that answer right now."

    @function_tool(description="End the phone call after the caller is done or asks to disconnect.")
    async def end_session(self, context: RunContext, reason: str):
        room_name = self.job_context.room.name if self.job_context and self.job_context.room else "unknown"
        self.logger.info("TOOL_CALL_START | name=end_session | reason=%s | room=%s", reason, room_name)

        try:
            await context.wait_for_playout()
            speech = self.session.say(
                self.closing,
                allow_interruptions=False,
                add_to_chat_ctx=True,
            )
            await speech.wait_for_playout()
            log_session_history(self.session, self.logger, "before_end_session_delete_room")

            if self.job_context:
                await hangup_whatsapp(self.job_context, self.logger)

            if self.job_context and self.job_context.room:
                delete_task = self.job_context.delete_room(room_name)
                await asyncio.wait_for(delete_task, timeout=8.0)
                self.logger.info("ROOM_DELETE_COMPLETED | room=%s", room_name)

            self.session.shutdown(drain=False)
            if self.job_context:
                self.job_context.shutdown(reason or "Call completed")

            self.logger.info("TOOL_CALL_RESULT | name=end_session | result=ended")
            return "Call ended."
        except Exception as exc:
            self.logger.exception(
                "TOOL_CALL_FAILED | name=end_session | reason=%s | room=%s | error=%s",
                reason,
                room_name,
                exc,
            )
            return "I tried to end the call, but the disconnect failed."

# ================== SERVER ==================
server = AgentServer(
    load_threshold=0.95,
    shutdown_process_timeout=30,
    job_memory_warn_mb=800,
    job_memory_limit_mb=1000
)

# ================== HELPERS ==================
async def wait_for_human(ctx: JobContext, logger, timeout=30):
    logger.info("Waiting for human participant to join...")
    
    if len(ctx.room.remote_participants) > 0:
        return True

    for _ in range(timeout * 10):
        if len(ctx.room.remote_participants) > 0:
            logger.info("Human participant detected.")
            return True
        await asyncio.sleep(0.1)

    logger.warning("No participant detected before timeout.")
    return False

# ================== MAIN ==================
@server.rtc_session(agent_name=AGENT_NAME)
async def my_agent(ctx: JobContext):

    ctx.log_context_fields = {
        "room": ctx.room.name,
    }

    await ctx.connect()

    metadata_str = ctx.job.metadata
    try:
        metadata = json.loads(metadata_str) if metadata_str else {}
    except json.JSONDecodeError:
        metadata = {}
        print("Failed to parse metadata JSON")

    language_raw = metadata.get("language")
    gender_raw = metadata.get("gender")

    if not language_raw:
        raise ValueError("Missing required metadata field: language")

    if not gender_raw:
        raise ValueError("Missing required metadata field: gender")

    language_raw = language_raw.lower()
    gender_raw = gender_raw.lower()

    if language_raw not in LANGUAGE_MAP:
        raise ValueError(f"Unsupported language: {language_raw}")

    language = LANGUAGE_MAP[language_raw]
    stt_language = STT_LANGUAGE_MAP[language_raw]

    if gender_raw not in VOICE_MAP:
        raise ValueError(f"Unsupported gender: {gender_raw}")

    speaker = VOICE_MAP[gender_raw]

    greeting = metadata.get("greeting", "Hello, I am here to conduct a survey.")
    instructions = metadata.get("instructions", "You are a helpful assistant.")
    knowledge_base_id = metadata.get("knowledge_base_id", "")
    phone_number = metadata.get("phone_number", "Unknown")
    context_id = metadata.get("context_id", "")
    closing = metadata.get("closing")
    session_id = ctx.job.id
    channel = str(metadata.get("channel") or "sip").lower()  # whatsapp = WebRTC audio, not 8k telephony
    noise_clip = NOISE_CLIPS.get(str(metadata.get("background_noise") or "none").strip().lower())
    try:
        noise_volume = float(metadata.get("background_noise_volume", DEFAULT_NOISE_VOLUME))
    except (TypeError, ValueError):
        noise_volume = DEFAULT_NOISE_VOLUME

    # ✅ Logger created FIRST so service failures are tied to this call in CloudWatch
    logger = create_call_logger(phone_number, session_id)
    logger.info(f"CALL_STARTED | phone={phone_number} | room={ctx.room.name}")
    logger.info("CALL_METADATA | data=%s", _json_for_log(metadata))
    logger.info("AGENT_INSTRUCTIONS | data=%s", instructions)
    logger.info(
        "CALL_LANGUAGE_CONFIG | raw=%s | tts_language=%s | stt_language=%s | voice=%s",
        language_raw,
        language,
        stt_language,
        speaker,
    )
    logger.info("KB_CONFIG | knowledge_base_id_present=%s", bool(knowledge_base_id))

    # ✅ Logger passed into every service so failures log to per-phone CloudWatch stream
    stt = create_stt(language=stt_language, logger=logger)

    tts = create_sarvam_tts(
        language=language,
        voice=speaker,
        logger=logger
    )

    llm = create_cerebras_llm(logger=logger)
    logger.info("LLM provider selected: cerebras")
    vad = create_vad(logger=logger)

    session = AgentSession(
        stt=stt,
        llm=llm,
        tts=tts,
        vad=vad,
    )
    attach_conversation_logging(session, logger)

    call_status = "failed"

    logger.info(f"Downloading context | context_id={context_id}")
    kb_path = await download_context(context_id, session_id)
    survey_manager = SurveyManager(kb_path)
    
    def cleanup():
        logger.info(f"Cleaning up context file | path={kb_path}")
        cleanup_context(kb_path)
        
    stagger_delay = random.uniform(0.2, 1.5)
    logger.info(f"Staggering TTS init | delay={stagger_delay:.2f}s")
    await asyncio.sleep(stagger_delay)

    await session.start(
        agent=SurveyAssistant(
            instructions,
            knowledge_base_id,
            ctx,
            survey_manager,
            logger,
            closing
        ),
        room=ctx.room,
        room_options=room_io.RoomOptions(
            audio_input=room_io.AudioInputOptions(
                noise_cancellation=(
                    noise_cancellation.BVC() if channel == "whatsapp" else noise_cancellation.BVCTelephony()
                )
            ),
        ),
    )

    background_audio = None
    if noise_clip:
        background_audio = BackgroundAudioPlayer(
            ambient_sound=AudioConfig(noise_clip, volume=noise_volume)
        )
        await background_audio.start(room=ctx.room, agent_session=session)
        logger.info("BACKGROUND_NOISE | clip=%s | volume=%.2f", noise_clip, noise_volume)

    human_joined = await wait_for_human(ctx, logger)

    if not human_joined:
        logger.warning("CALL_FAILED | reason=no participant joined")
    else:
        call_status = "success"
        logger.info("CALL_CONNECTED")
        await session.say(greeting)

    

    async def send_transcript():
        if background_audio:
            await background_audio.aclose()
        log_session_history(session, logger, "shutdown")
        cleanup()

        if call_status == "success":
            raw_data = session.history.to_dict(exclude_function_call=True)
            items = raw_data.get("items", [])
        else:
            items = []

        timestamp = str(datetime.today())

        payload = {
            "phone_number": phone_number,
            "status": call_status,
            "transcript": items,
            "date": timestamp,
            "contextId": context_id
        }

        url = TRANSCRIPT_URL
        if not url:
            logger.warning("TRANSCRIPT_URL not set, skipping transcript upload")
            close_logger(logger)
            return
        try:
            async with aiohttp.ClientSession() as http_session:
                async with http_session.post(url, json=payload, timeout=5) as response:
                    logger.info(f"Transcript saved | status={response.status}")
        except Exception as e:
            logger.error(f"Failed to send transcript | error={e}")

        # ✅ ADD THIS LINE
        close_logger(logger)

    ctx.add_shutdown_callback(send_transcript)


if __name__ == "__main__":
    cli.run_app(server)
