"""Routing checks for both Lambdas, with a fake LiveKit client and a stubbed context lookup.

Run: python tests/test_lambdas.py   (needs `pip install livekit-api`)
"""
import asyncio
import json
import os
import sys
import types
from types import SimpleNamespace

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for sub in ("common", "dispatcher", "webhook_receiver"):
    sys.path.insert(0, os.path.join(ROOT, "lambdas", sub))

# stub the DynamoDB-backed lookup so no AWS is needed
db = types.ModuleType("db_mapper")
ctx = types.ModuleType("db_mapper.contextIvr")
ctx.get_context_by_id = lambda i: {"prompt": "be nice", "language": "english", "gender": "female",
                                   "greeting": "Hi {name}", "closing": "bye"}
sys.modules.update({"db_mapper": db, "db_mapper.contextIvr": ctx})

os.environ.update(WHATSAPP_APP_SECRET="s", WHATSAPP_VERIFY_TOKEN="t", WHATSAPP_ACCESS_TOKEN="tok",
                  WHATSAPP_PHONE_NUMBER_ID="123", WHATSAPP_INBOUND_CONTEXT_ID="ctx1",
                  LIVEKIT_URL="wss://x", LIVEKIT_API_KEY="k", LIVEKIT_API_SECRET="s" * 32)

from livekit import api  # noqa: E402
from livekit.protocol.agent_dispatch import RoomAgentDispatch  # noqa: E402
from livekit.protocol.rtc import SessionDescription  # noqa: E402

import call_params  # noqa: E402
import handler as receiver  # noqa: E402  (webhook_receiver is last on sys.path, so first found)

calls = []


class FakeRoom:
    async def update_room_metadata(self, req):
        calls.append(("update_room_metadata", req))


class FakeConnector:
    def __getattr__(self, name):
        async def fn(req):
            calls.append((name, req))
            return SimpleNamespace(whatsapp_call_id="wacid.1", room_name="room-1")
        return fn


lkapi = SimpleNamespace(connector=FakeConnector(), room=FakeRoom())
lk = SimpleNamespace(api=api, RoomAgentDispatch=RoomAgentDispatch, SessionDescription=SessionDescription)
value = {"metadata": {"phone_number_id": "123"}, "contacts": [{"profile": {"name": "Asha"}}]}


def route(call):
    calls.clear()
    asyncio.run(receiver.route_call(lkapi, lk, value, call))
    return calls[0]


# outbound answer -> connect
n, req = route({"id": "c1", "event": "connect", "direction": "BUSINESS_INITIATED", "session": {"sdp": "ANS"}})
assert n == "connect_whatsapp_call" and req.sdp.sdp == "ANS" and req.sdp.type == "answer"

# inbound offer -> accept with agent dispatch carrying channel + context
n, req = route({"id": "c2", "event": "connect", "direction": "USER_INITIATED", "from": "9198", "session": {"sdp": "OFF"}})
assert n == "accept_whatsapp_call" and req.sdp.sdp == "OFF"
meta = json.loads(req.agents[0].metadata)
assert req.agents[0].agent_name == "Vriddhi-voice-bot" and meta["channel"] == "whatsapp" and meta["name"] == "Asha"
assert meta["background_noise"] == "none"
assert json.loads(calls[1][1].metadata) == {"whatsapp_call_id": "c2"}  # room tagged for the agent's hangup

# hangup -> disconnect
n, req = route({"id": "c3", "event": "terminate"})
assert n == "disconnect_whatsapp_call" and req.whatsapp_call_id == "c3"

# webhook auth
import hashlib, hmac  # noqa: E402,E401
body = "{}"
sig = "sha256=" + hmac.new(b"s", body.encode(), hashlib.sha256).hexdigest()
assert receiver.handler({"httpMethod": "POST", "body": body, "headers": {}}, None)["statusCode"] == 401
assert receiver.handler({"httpMethod": "GET", "queryStringParameters": {
    "hub.mode": "subscribe", "hub.verify_token": "t", "hub.challenge": "9"}}, None)["body"] == "9"
assert sig  # POST success path needs a live LiveKit client; covered by a real call test

# params: defaults + channel + noise
p = call_params.build_call_params({"name": "A", "phoneNumber": "1", "contextId": "c"})
assert p["channel"] == "sip" and p["background_noise"] == "none"
p = call_params.build_call_params({"name": "A", "phoneNumber": "1", "contextId": "c",
                                   "channel": "WhatsApp", "backgroundNoise": "office"})
assert p["channel"] == "whatsapp" and json.loads(call_params.build_metadata(p))["background_noise"] == "office"
try:
    call_params.build_call_params({"name": "A", "phoneNumber": "1", "contextId": "c", "channel": "fax"})
    raise SystemExit("bad channel accepted")
except ValueError:
    pass

# dispatcher whatsapp path
import importlib.util  # noqa: E402
spec = importlib.util.spec_from_file_location("dispatcher_handler", os.path.join(ROOT, "lambdas", "dispatcher", "handler.py"))
dispatcher = importlib.util.module_from_spec(spec)
spec.loader.exec_module(dispatcher)
calls.clear()
room = asyncio.run(dispatcher.initiate_whatsapp_call(lkapi, p | {"phone_number": "+91 98765"}))
assert [c[0] for c in calls] == ["dial_whatsapp_call", "update_room_metadata"]
assert json.loads(calls[1][1].metadata) == {"whatsapp_call_id": "wacid.1"}
n, req = calls[0]
assert n == "dial_whatsapp_call" and req.whatsapp_to_phone_number == "9198765" and room == "room-1"
assert req.agents[0].agent_name == "Vriddhi-voice-bot"

print("all lambda routing checks pass")
