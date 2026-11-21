"""Run: python tests/test_router.py"""
import asyncio
import base64
import hashlib
import hmac
import importlib.util
import json
import os
from pathlib import Path

os.environ["WHATSAPP_VERIFY_TOKEN"] = "vt"
os.environ["WHATSAPP_APP_SECRET"] = "secret"
os.environ["WHATSAPP_PHONE_NUMBER_ID"] = "PN1"
os.environ["WHATSAPP_ACCESS_TOKEN"] = "tok"
spec = importlib.util.spec_from_file_location("router", Path(__file__).parents[1] / "lambdas/router/handler.py")
r = importlib.util.module_from_spec(spec)
spec.loader.exec_module(r)


# --- fake LiveKit: records what the router sends
from types import SimpleNamespace as NS  # noqa: E402

SENT = []


class FakeConnector:
    async def accept_whatsapp_call(self, req):
        SENT.append(("accept", req))
        return NS(room_name="")  # the real response gave a room name that did not exist; never rely on it

    async def disconnect_whatsapp_call(self, req):
        SENT.append(("disconnect", req))


class FakeRoom:
    async def update_room_metadata(self, req):
        SENT.append(("room_meta", req))


class _Req:
    def __init__(self, **k): self.__dict__.update(k)


class DisconnectReq(_Req):
    USER_INITIATED = "USER"


FAKE_API = NS(AcceptWhatsAppCallRequest=_Req, UpdateRoomMetadataRequest=_Req, DisconnectWhatsAppCallRequest=DisconnectReq)


class FakeLK:
    def __init__(self):
        self.api, self.RoomAgentDispatch, self.SessionDescription = FAKE_API, _Req, _Req
        self.lkapi = NS(connector=FakeConnector(), room=FakeRoom(), aclose=self._close)
        self.closed = False

    async def _close(self):
        self.closed = True


LKS = []


def fake_livekit():
    LKS.append(FakeLK())
    return LKS[-1]


r._livekit = fake_livekit
run = asyncio.run


def sig(raw: bytes, secret="secret") -> str:
    return "sha256=" + hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()


def post(payload, signature="auto", v2=False, b64=False, raw=None):
    raw = raw if raw is not None else json.dumps(payload).encode()
    h = {"X-Hub-Signature-256": sig(raw) if signature == "auto" else signature}
    ev = {"body": base64.b64encode(raw).decode() if b64 else raw.decode(), "isBase64Encoded": b64, "headers": h}
    ev.update({"requestContext": {"http": {"method": "POST"}}} if v2 else {"httpMethod": "POST"})
    return r.handler(ev, None)


def wrap(field, **value):
    return {"object": "whatsapp_business_account", "entry": [{"changes": [{"field": field, "value": value}]}]}


# --- verification
q = {"hub.mode": "subscribe", "hub.verify_token": "vt", "hub.challenge": "123"}
assert r.handler({"httpMethod": "GET", "queryStringParameters": q}, None) == r._resp(200, "123")
assert r.handler({"httpMethod": "GET", "queryStringParameters": {**q, "hub.verify_token": "x"}}, None)["statusCode"] == 403
assert r.handler({"httpMethod": "GET", "queryStringParameters": None}, None)["statusCode"] == 403
assert r.handler({"requestContext": {"http": {"method": "GET"}}, "queryStringParameters": q}, None)["statusCode"] == 200

# --- auth
call = wrap("calls", metadata={"phone_number_id": "1"}, calls=[{"id": "c1", "event": "connect"}])
assert post(call)["statusCode"] == 200
assert post(call, v2=True, b64=True)["statusCode"] == 200
assert post(call, signature="sha256=bad")["statusCode"] == 401
assert post(call, signature="")["statusCode"] == 401
r.APP_SECRET = ""
assert post(call)["statusCode"] == 401  # fails closed when secret unset
r.APP_SECRET = "secret"

# --- bad input after valid signature
assert post(None, raw=b"not json")["statusCode"] == 400
assert post(None, raw=b"[1,2]")["statusCode"] == 400
assert r.handler({"httpMethod": "POST", "body": "!!", "isBase64Encoded": True, "headers": {}}, None)["statusCode"] == 400
assert post(None, raw=b"x" * (r.MAX_BODY_BYTES + 1))["statusCode"] == 413
assert r.handler({"httpMethod": "DELETE"}, None)["statusCode"] == 405

# --- routing + edge shapes
assert run(r.process(call)) == {"routed": 1, "failed": 0, "ignored": 0}
msgs = wrap("messages", messages=[{"id": "m1", "type": "text"}, "junk", {"id": "m2"}], statuses=[{"id": "s1"}])
assert run(r.process(msgs)) == {"routed": 2, "failed": 0, "ignored": 1}
assert run(r.process(wrap("account_update"))) == {"routed": 0, "failed": 0, "ignored": 1}
assert run(r.process({"object": "page"})) == {"routed": 0, "failed": 0, "ignored": 1}
assert run(r.process({"object": "whatsapp_business_account", "entry": "x"})) == {"routed": 0, "failed": 0, "ignored": 0}
assert run(r.process({"object": "whatsapp_business_account", "entry": [{"changes": [{"field": "calls", "value": None}]}]}))["routed"] == 0

# --- inbound call is accepted into LiveKit with the agent dispatched
offer = lambda **k: wrap("calls", metadata={"phone_number_id": "PN1"}, calls=[{"id": "wacid.1", "event": "connect", "direction": "USER_INITIATED", "from": "919999999999", "session": {"sdp": "v=0 offer"}, **k}])
SENT.clear(); LKS.clear()
assert run(r.process(offer())) == {"routed": 1, "failed": 0, "ignored": 0}
kinds = [k for k, _ in SENT]
assert kinds == ["accept", "room_meta"], kinds
acc = SENT[0][1]
assert acc.whatsapp_call_id == "wacid.1" and acc.whatsapp_phone_number_id == "PN1" and acc.whatsapp_api_key == "tok"
assert acc.sdp.sdp == "v=0 offer" and acc.sdp.type == "offer"
assert acc.room_name.startswith("wa-in-") and "9999" not in acc.room_name  # no caller number in the room name
disp = acc.agents[0]
assert disp.agent_name == "Vriddhi-voice-bot" and json.loads(disp.metadata) == {"mode": "inbound", "channel": "whatsapp", "whatsapp_call_id": "wacid.1"}
assert "9999" not in disp.metadata  # no caller number to the agent either
assert json.loads(SENT[1][1].metadata) == {"whatsapp_call_id": "wacid.1"} and SENT[1][1].room == acc.room_name
assert LKS[-1].closed  # client always closed

# --- things we must NOT accept
for bad in [offer(direction="BUSINESS_INITIATED"), offer(session={}), offer(id=""), offer(event="ringing")]:
    SENT.clear()
    run(r.process(bad))
    assert SENT == [], bad
SENT.clear()
other = wrap("calls", metadata={"phone_number_id": "OTHER"}, calls=[{"id": "c", "event": "connect", "direction": "USER_INITIATED", "session": {"sdp": "x"}}])
run(r.process(other))
assert SENT == []  # a different number on the same Meta app

# --- terminate closes the LiveKit side
SENT.clear()
run(r.process(wrap("calls", metadata={"phone_number_id": "PN1"}, calls=[{"id": "wacid.1", "event": "terminate"}])))
assert [k for k, _ in SENT] == ["disconnect"] and SENT[0][1].whatsapp_call_id == "wacid.1"

# --- a LiveKit failure never breaks the 200 to Meta, and the client is still closed
async def boom_accept(req): raise RuntimeError("livekit down")
FakeConnector.accept_whatsapp_call = lambda self, req: boom_accept(req)
LKS.clear()
res = run(r.process(offer()))
assert res["failed"] == 1 and LKS[-1].closed
assert post(offer())["statusCode"] == 200



def boom(*a):
    raise RuntimeError("x")


r.ROUTES["messages"] = ("messages", boom)
assert run(r.process(msgs))["failed"] == 2  # a failing route never raises
assert post(msgs)["statusCode"] == 200  # and Meta still gets 200

print("ok")
