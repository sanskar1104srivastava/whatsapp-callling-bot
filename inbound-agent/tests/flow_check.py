"""Live conversation check (real Cerebras + real Prism, no audio). Needs SARVAM_API_KEY and CEREBRAS_API_KEY in env.
Run: uv run python tests/flow_check.py"""
import asyncio, logging, sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
logging.basicConfig(level=logging.WARNING)
logging.getLogger("vriddhi-inbound").setLevel(logging.INFO)
import aiohttp
from livekit.agents import AgentSession
from livekit.plugins import cerebras
import inbound_agent as ia
import os
VARIANT = os.getenv("VARIANT", "")
if VARIANT == "nofiller": ia.pick_filler = lambda st: ""
if VARIANT == "noupdate":
    async def _noop(self, ins): return None
    ia.Assistant.update_instructions = _noop

TURNS = ["हेलो", "मुझे कुछ medicines order करनी है", "Paracetamol कर दीजिए पांच पत्ता", "हां छह सौ पचास mg कर दीजिए", "Pickup", "Ok, नहीं, thank you"]

async def main():
    usecases, settings = ia.load_usecases()
    ctx = SimpleNamespace(room=SimpleNamespace(metadata="{}", name="t"), job=SimpleNamespace(metadata="{}"), shutdown=lambda *a: None,
                          add_shutdown_callback=lambda *a: None, delete_room=lambda *a: asyncio.sleep(0))
    async with aiohttp.ClientSession() as http:
        state = ia.CallState(ctx=ctx, usecases=usecases, settings=settings, http=http,
                             session_tts_key=ia.tts_key(ia.default_usecase(usecases, settings)))
        async def say(*a, **k): print("AGENT (opening):", a[0]); return None
        async with AgentSession(llm=ia.make_llm()) as sess:
            sess.say = say  # the filler (context.session.say) lands here too
            await sess.start(ia.Assistant(state))
            for t in TURNS:
                print("\nCALLER:", t)
                r = await sess.run(user_input=t)
                for ev in r.events:
                    if ev.type == "message": print("  AGENT:", (ev.item.text_content or "")[:160])
                    elif ev.type == "function_call": print("  TOOL CALL:", ev.item.name, ev.item.arguments[:120])
                    elif ev.type == "agent_handoff": print("  HANDOFF")
                    elif ev.type == "function_call_output": print("  TOOL RESULT:", str(ev.item.output)[:140])
            print("\nstate: current =", (state.current or {}).get("usecaseId"), "| queue =", state.queue)
            print("instructions now contain task prompt:", "CURRENT TASK" in sess.current_agent.instructions)

asyncio.run(main())
