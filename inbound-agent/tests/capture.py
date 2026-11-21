import asyncio, json, logging, os, sys
from pathlib import Path
from types import SimpleNamespace
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import aiohttp
from livekit.agents import AgentSession
import inbound_agent as ia

async def main():
    usecases, settings = ia.load_usecases()
    ctx = SimpleNamespace(room=SimpleNamespace(metadata="{}", name="t"), job=SimpleNamespace(metadata="{}"), shutdown=lambda *a: None, add_shutdown_callback=lambda *a: None)
    llm = ia.make_llm()
    calls = []
    orig = llm._client.chat.completions.create
    async def spy(**kw):
        calls.append({k: (v if k not in ("messages", "tools") else None) for k, v in kw.items()} | {"n_messages": len(kw.get("messages", [])), "tools": [t["function"]["name"] for t in kw.get("tools", [])], "last": kw["messages"][-2:]})
        stream = await orig(**kw)
        async def wrap():
            parts = []
            async for ch in stream:
                if ch.choices:
                    d = ch.choices[0].delta
                    parts.append({"content": d.content, "tool_calls": bool(d.tool_calls), "finish": ch.choices[0].finish_reason})
            calls[-1]["chunks"] = parts
            for ch in stream_replay(parts): yield ch
        # re-iterate is not possible: return a fresh async generator that yields the originally received chunks
        return stream
    llm._client.chat.completions.create = spy
    async with aiohttp.ClientSession() as http:
        state = ia.CallState(ctx=ctx, usecases=usecases, settings=settings, http=http, session_tts_key=ia.tts_key(ia.default_usecase(usecases, settings)))
        async def say(*a, **k): return None
        async with AgentSession(llm=llm) as sess:
            sess.say = say
            await sess.start(ia.Assistant(state))
            await sess.run(user_input="हेलो")
            await sess.run(user_input="मेरा ऑर्डर अभी तक नहीं आया है")
    for i, c in enumerate(calls):
        print(f"--- LLM call {i+1}: {({k: v for k, v in c.items() if k not in ('last','chunks')})}")
        for m in c["last"]:
            print("    msg:", m["role"], str(m.get("content") or m.get("tool_calls"))[:150].replace("\n", " "))
asyncio.run(main())
