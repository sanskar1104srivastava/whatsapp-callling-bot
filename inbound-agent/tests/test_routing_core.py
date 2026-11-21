"""Run from inbound-agent/: uv run python tests/test_routing_core.py"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import routing_core as rc  # noqa: E402

S = {"purposeGreeting": "नमस्ते! मैं इनमें मदद कर सकती हूँ: {options}। बताइए?", "T_high": 0.9, "margin": 0.3, "T_multi": 0.7, "maxIntentsPerCall": 2}


def uc(i, prio, **kw):
    return {"usecaseId": i, "name": i, "intentDescription": f"wants {i}", "examples": [], "priority": prio,
            "enabled": True, "channels": ["voice", "chat"], **kw}


U = [uc("order", 2, spokenName="ऑर्डर देना"), uc("appointment", 3), uc("complaint", 1), uc("faq", 4), uc("chatonly", 5, channels=["chat"]), uc("off", 6, enabled=False)]


def sc(choice=None, yes=None):
    return {"choice": {"order": 0, "appointment": 0, "complaint": 0, "faq": 0, "unclear": 0, "out_of_scope": 0, **(choice or {})},
            "yes": {"order": 0, "appointment": 0, "complaint": 0, "faq": 0, **(yes or {})}}


# --- opening lists top 3 enabled voice use cases, by priority
o = rc.build_opening(S, U)
assert "complaint, ऑर्डर देना और appointment" in o and "faq" not in o and "chatonly" not in o, o
assert rc.build_opening({**S, "purposeGreeting": "हेलो।"}, U) == "हेलो।"  # no placeholder: used as written

# --- decide
assert rc.decide(sc({"order": 0.95, "faq": 0.03}, {"order": 0.9}), U, S).queue == ["order"]
d = rc.decide(sc({"complaint": 0.85, "order": 0.1}, {"complaint": 0.95, "order": 0.9}), U, S)
assert d.queue == ["complaint", "order"]  # both yes; complaint has priority 1
d = rc.decide(sc({"complaint": 0.93, "order": 0.02}, {"complaint": 0.9, "order": 0.95}), U, S)
assert d.queue == ["complaint", "order"]  # choice picks complaint, independent yes still catches order
assert rc.decide(sc({"order": 0.5, "faq": 0.4}, {"order": 0.5, "faq": 0.6}), U, S).kind == "unclear"
assert rc.decide(sc({"order": 0.95}, {}), U, S).queue == ["order"]  # decisive choice alone
assert rc.decide(sc({"order": 0.6, "faq": 0.35}, {}), U, S).kind == "unclear"  # lead < margin
three = rc.decide(sc({}, {"order": 0.9, "appointment": 0.8, "complaint": 0.75}), U, S)
assert three.queue == ["order", "appointment"]  # cap 2 keeps the strongest (order .9, appt .8), then priority order
assert rc.decide(sc({"unclear": 0.9}, {}), U, S).kind == "unclear"
assert rc.decide(sc({"out_of_scope": 0.9}, {}), U, S).kind == "out_of_scope"  # clear request we do not handle
assert rc.decide(sc({"out_of_scope": 0.4, "unclear": 0.5}, {}), U, S).kind == "unclear"  # vague wins: ask again
assert rc.decide(sc({"out_of_scope": 0.45, "order": 0.4}, {}), U, S).kind == "unclear"  # not confident it is out of scope
assert rc.decide(sc({"out_of_scope": 0.9}, {"order": 0.8}), U, S).queue == ["order"]  # a real match still wins
assert rc.decide({"choice": {}, "yes": {"chatonly": 0.99, "off": 0.99}}, U, S).kind == "unclear"  # not voice / disabled

# --- request shape
req = rc.build_request("मुझे ऑर्डर देना है", U)
assert set(req["questions"]) == {"main", "yes_complaint", "yes_order", "yes_appointment", "yes_faq"}
assert rc.UNCLEAR in req["questions"]["main"]["criteria"] and rc.OUT_OF_SCOPE in req["questions"]["main"]["criteria"] and "chatonly" not in req["questions"]["main"]["criteria"]

# --- parsing
good = {"answers": {"main": {"probabilities": {"order": 0.9, "unclear": 0.1}}, **{f"yes_{i}": {"noul": 0.5} for i in ["order", "appointment", "complaint", "faq"]}}}
assert rc.parse_scores(good, U)["yes"]["order"] == 0.5
for bad in [{}, {"answers": {}}, {"answers": {"main": {}}}, {"answers": {"main": {"probabilities": {"a": "x"}}}}]:
    try:
        rc.parse_scores(bad, U)
        raise AssertionError("should fail")
    except rc.PrismError:
        pass


# --- classify: retry once on 503, then fail cleanly (fake aiohttp session)
class Resp:
    def __init__(self, status, data): self.status, self._d = status, data
    async def __aenter__(self): return self
    async def __aexit__(self, *a): return False
    async def json(self): return self._d


class Http:
    def __init__(self, *resps): self.resps, self.calls = list(resps), 0
    def post(self, *a, **k): self.calls += 1; return self.resps.pop(0)


async def run():
    h = Http(Resp(503, {}), Resp(200, good))
    assert (await rc.classify(h, "x", U, "k"))["choice"]["order"] == 0.9 and h.calls == 2
    h = Http(Resp(503, {}), Resp(503, {}))
    try:
        await rc.classify(h, "x", U, "k")
        raise AssertionError("should fail")
    except rc.PrismError:
        assert h.calls == 2
    h = Http(Resp(403, {}))
    try:
        await rc.classify(h, "x", U, "k")
        raise AssertionError("should fail")
    except rc.PrismError:
        assert h.calls == 1  # no retry on 4xx


asyncio.run(run())
print("ok")
