"""vriddhi-voice-calling-router: the Meta WhatsApp webhook callback behind API Gateway (REST).

GET  /webhook  Meta's one-time verification handshake (hub.* query params).
POST /webhook  Signed events. Every event is classified by kind and sent to its route:
                 calls    -> call route
                 messages -> chat route
                 statuses -> ignored (delivery receipts)

Rules that matter:
- Signature (X-Hub-Signature-256) is checked on the RAW body before anything is parsed. Fails closed
  if the app secret is not configured.
- After authentication the answer is always 200, even if one event fails, otherwise Meta retries the
  whole batch. Bad shapes are skipped, not raised.
- No phone numbers or message text are logged (PII). Only ids and kinds.
- Works with REST (v1) and HTTP (v2) proxy event shapes.

Calls (inbound only): a USER_INITIATED `connect` is accepted into LiveKit with the voice agent dispatched;
`terminate` closes the LiveKit side. Messages are still log-only (chat bot not built yet).
"""
import asyncio
import base64
import binascii
import hashlib
import hmac
import json
import os
import uuid

VERIFY_TOKEN = os.getenv("WHATSAPP_VERIFY_TOKEN", "")
APP_SECRET = os.getenv("WHATSAPP_APP_SECRET", "")  # Meta app secret, signs every POST
WA_ACCESS_TOKEN = os.getenv("WHATSAPP_ACCESS_TOKEN", "")
WA_API_VERSION = os.getenv("WHATSAPP_API_VERSION", "23.0")
OUR_PHONE_NUMBER_ID = os.getenv("WHATSAPP_PHONE_NUMBER_ID", "")  # only handle calls to this number
AGENT_NAME = os.getenv("AGENT_NAME", "Vriddhi-voice-bot")
MAX_BODY_BYTES = 1_000_000  # Meta payloads are small; API Gateway allows up to 10 MB


def _resp(code: int, body: str = "") -> dict:
    return {"statusCode": code, "headers": {"Content-Type": "text/plain"}, "body": body}


def _log(tag: str, **fields) -> None:
    print(f"{tag} | " + " ".join(f"{k}={v}" for k, v in fields.items()))


def _method(event: dict) -> str:
    return (event.get("requestContext", {}).get("http", {}).get("method") or event.get("httpMethod") or "").upper()


def _raw_body(event: dict) -> bytes:
    body = event.get("body") or ""
    if event.get("isBase64Encoded"):
        return base64.b64decode(body, validate=True)
    return body.encode() if isinstance(body, str) else bytes(body)


def _valid_signature(raw: bytes, header: str) -> bool:
    if not APP_SECRET or not header:
        return False
    expected = "sha256=" + hmac.new(APP_SECRET.encode(), raw, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, header)


# --- routes -------------------------------------------------------------------------------------

def _livekit():
    """Real LiveKit client + message types. Replaced in tests."""
    from types import SimpleNamespace

    from livekit import api
    from livekit.protocol.agent_dispatch import RoomAgentDispatch
    from livekit.protocol.rtc import SessionDescription

    return SimpleNamespace(
        lkapi=api.LiveKitAPI(), api=api, RoomAgentDispatch=RoomAgentDispatch, SessionDescription=SessionDescription
    )


def _sdp(call: dict) -> str:
    # Meta nests the SDP under "session" (some payload versions use "connect")
    return (call.get("session") or call.get("connect") or {}).get("sdp", "")


async def route_call(value: dict, call: dict) -> None:
    call_id, event = call.get("id"), call.get("event")
    phone_number_id = value.get("metadata", {}).get("phone_number_id", "")
    _log("ROUTE_CALL", id=call_id, event=event, direction=call.get("direction"), phone_number_id=phone_number_id)

    if OUR_PHONE_NUMBER_ID and phone_number_id != OUR_PHONE_NUMBER_ID:
        return  # another number on the same Meta app
    if not call_id or event not in ("connect", "terminate"):
        return

    lk = _livekit()
    try:
        if event == "connect":
            if call.get("direction") != "USER_INITIATED":
                return  # outbound is not built; never accept a call we did not expect
            sdp = _sdp(call)
            if not sdp:
                _log("CALL_SKIPPED", id=call_id, reason="no SDP offer")
                return
            room_name = f"wa-in-{uuid.uuid4().hex[:10]}"  # random: no caller number in room names/logs
            metadata = json.dumps({"mode": "inbound", "channel": "whatsapp", "whatsapp_call_id": call_id})
            await lk.lkapi.connector.accept_whatsapp_call(
                lk.api.AcceptWhatsAppCallRequest(
                    whatsapp_phone_number_id=phone_number_id,
                    whatsapp_api_key=WA_ACCESS_TOKEN,
                    whatsapp_cloud_api_version=WA_API_VERSION,
                    whatsapp_call_id=call_id,
                    sdp=lk.SessionDescription(type="offer", sdp=sdp),
                    room_name=room_name,
                    agents=[lk.RoomAgentDispatch(agent_name=AGENT_NAME, metadata=metadata)],
                )
            )
            # the agent reads Meta's call id from ROOM metadata to hang up on its side
            await lk.lkapi.room.update_room_metadata(
                lk.api.UpdateRoomMetadataRequest(room=room_name, metadata=json.dumps({"whatsapp_call_id": call_id}))
            )
            _log("CALL_ACCEPTED", id=call_id, room=room_name)
        else:  # terminate fires for every hangup, including the agent's own; a second disconnect just errors
            await lk.lkapi.connector.disconnect_whatsapp_call(
                lk.api.DisconnectWhatsAppCallRequest(
                    whatsapp_call_id=call_id,
                    disconnect_reason=lk.api.DisconnectWhatsAppCallRequest.USER_INITIATED,
                )
            )
    finally:
        await lk.lkapi.aclose()


def route_message(value: dict, msg: dict) -> None:
    _log("ROUTE_MESSAGE", id=msg.get("id"), type=msg.get("type"),
         phone_number_id=value.get("metadata", {}).get("phone_number_id"))


ROUTES = {"calls": ("calls", route_call), "messages": ("messages", route_message)}


def _dicts(x) -> list:
    """Meta fields are lists of objects; tolerate anything else."""
    return [i for i in x if isinstance(i, dict)] if isinstance(x, list) else []


async def process(payload: dict) -> dict:
    """Route every event in the payload. Returns counts (used by tests and logs)."""
    counts = {"routed": 0, "failed": 0, "ignored": 0}
    if payload.get("object") != "whatsapp_business_account":
        counts["ignored"] += 1
        return counts
    for entry in _dicts(payload.get("entry")):
        for change in _dicts(entry.get("changes")):
            field, value = change.get("field"), change.get("value")
            value = value if isinstance(value, dict) else {}
            if field not in ROUTES:
                counts["ignored"] += 1
                continue
            key, fn = ROUTES[field]
            for item in _dicts(value.get(key)):
                try:
                    res = fn(value, item)
                    if asyncio.iscoroutine(res):
                        await res
                    counts["routed"] += 1
                except Exception as e:  # one bad event must not stop the rest or trigger Meta retries
                    counts["failed"] += 1
                    _log("ROUTE_ERROR", field=field, id=item.get("id"), error=repr(e))
            if field == "messages":
                counts["ignored"] += len(_dicts(value.get("statuses")))
    return counts


def handler(event, context):
    method = _method(event)

    if method == "GET":
        q = event.get("queryStringParameters") or {}
        token = q.get("hub.verify_token") or ""
        if q.get("hub.mode") == "subscribe" and VERIFY_TOKEN and hmac.compare_digest(token, VERIFY_TOKEN):
            return _resp(200, q.get("hub.challenge", ""))
        return _resp(403, "forbidden")

    if method == "POST":
        try:
            raw = _raw_body(event)
        except (binascii.Error, ValueError):
            return _resp(400, "bad body")
        if len(raw) > MAX_BODY_BYTES:
            return _resp(413, "too large")
        headers = {k.lower(): v for k, v in (event.get("headers") or {}).items()}
        if not _valid_signature(raw, headers.get("x-hub-signature-256")):
            return _resp(401, "bad signature")
        try:
            payload = json.loads(raw or b"{}")
        except ValueError:
            return _resp(400, "bad json")
        if not isinstance(payload, dict):
            return _resp(400, "bad json")
        _log("WEBHOOK", **asyncio.run(process(payload)))
        return _resp(200, "ok")

    return _resp(405, "method not allowed")
