"""Meta WhatsApp webhook receiver (Lambda Function URL / API Gateway).

GET  -> Meta's one-time verification handshake.
POST -> signed call events, routed to LiveKit's WhatsApp connector:
  connect + BUSINESS_INITIATED  (SDP answer to our outbound dial) -> connect_whatsapp_call
  connect + USER_INITIATED      (SDP offer, inbound call)         -> accept_whatsapp_call + agent dispatch
  terminate                                                       -> disconnect_whatsapp_call
"""
import asyncio
import base64
import hashlib
import hmac
import json
import os

from call_params import AGENT_NAME, build_call_params, build_metadata, safe_room_name, tag_room_with_call_id

VERIFY_TOKEN = os.getenv("WHATSAPP_VERIFY_TOKEN", "")
APP_SECRET = os.getenv("WHATSAPP_APP_SECRET", "")  # Meta app secret, signs every POST
WA_ACCESS_TOKEN = os.getenv("WHATSAPP_ACCESS_TOKEN", "")
WA_API_VERSION = os.getenv("WHATSAPP_API_VERSION", "26.0")
INBOUND_CONTEXT_ID = os.getenv("WHATSAPP_INBOUND_CONTEXT_ID", "")
# optional {"<phone_number_id>": "<contextId>"} when several business numbers share this Lambda
INBOUND_CONTEXT_BY_NUMBER = json.loads(os.getenv("WHATSAPP_INBOUND_CONTEXT_BY_NUMBER", "{}"))


def _resp(code: int, body: str = "") -> dict:
    return {"statusCode": code, "body": body}


def _valid_signature(raw: bytes, header: str) -> bool:
    expected = "sha256=" + hmac.new(APP_SECRET.encode(), raw, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, header or "")


def _sdp(call: dict) -> str:
    # Meta nests the SDP under "session" (some payload versions use "connect")
    return (call.get("session") or call.get("connect") or {}).get("sdp", "")


def _inbound_params(value: dict, call: dict) -> dict:
    phone_number_id = value.get("metadata", {}).get("phone_number_id", "")
    context_id = INBOUND_CONTEXT_BY_NUMBER.get(phone_number_id) or INBOUND_CONTEXT_ID
    if not context_id:
        raise ValueError("no inbound context configured (WHATSAPP_INBOUND_CONTEXT_ID)")
    caller = call.get("from", "")
    contact = (value.get("contacts") or [{}])[0]
    return build_call_params({
        "name": contact.get("profile", {}).get("name") or caller,
        "phoneNumber": caller,
        "contextId": context_id,
        "info": f"Inbound WhatsApp call from {caller}.",
        "channel": "whatsapp",
    })


async def route_call(lkapi, lk, value: dict, call: dict) -> None:
    """lk = namespace with api, RoomAgentDispatch, SessionDescription (injected so tests can fake it)."""
    api = lk.api
    event, call_id = call.get("event"), call.get("id")

    if event == "connect" and call.get("direction") == "BUSINESS_INITIATED":
        # must be immediate: the callee's phone is already ringing
        await lkapi.connector.connect_whatsapp_call(
            api.ConnectWhatsAppCallRequest(
                whatsapp_call_id=call_id, sdp=lk.SessionDescription(type="answer", sdp=_sdp(call))
            )
        )
    elif event == "connect":
        params = _inbound_params(value, call)
        res = await lkapi.connector.accept_whatsapp_call(
            api.AcceptWhatsAppCallRequest(
                whatsapp_phone_number_id=value["metadata"]["phone_number_id"],
                whatsapp_api_key=WA_ACCESS_TOKEN,
                whatsapp_cloud_api_version=WA_API_VERSION,
                whatsapp_call_id=call_id,
                sdp=lk.SessionDescription(type="offer", sdp=_sdp(call)),
                room_name=safe_room_name("wa-in", params["phone_number"]),
                agents=[lk.RoomAgentDispatch(agent_name=AGENT_NAME, metadata=build_metadata(params))],
            )
        )
        await tag_room_with_call_id(lkapi, api, res.room_name, call_id)
    elif event == "terminate":
        # fires for every hangup, including ours; a second disconnect just errors and is logged
        await lkapi.connector.disconnect_whatsapp_call(
            api.DisconnectWhatsAppCallRequest(
                whatsapp_call_id=call_id,
                disconnect_reason=api.DisconnectWhatsAppCallRequest.USER_INITIATED,
            )
        )


async def process(payload: dict) -> None:
    from types import SimpleNamespace

    from livekit import api
    from livekit.protocol.agent_dispatch import RoomAgentDispatch
    from livekit.protocol.rtc import SessionDescription

    lk = SimpleNamespace(api=api, RoomAgentDispatch=RoomAgentDispatch, SessionDescription=SessionDescription)
    lkapi = api.LiveKitAPI()
    try:
        for entry in payload.get("entry", []):
            for change in entry.get("changes", []):
                if change.get("field") != "calls":
                    continue
                value = change.get("value", {})
                for call in value.get("calls", []):
                    try:
                        await route_call(lkapi, lk, value, call)
                    except Exception as e:  # one bad call must not stop the rest or trigger Meta retries
                        print(f"WHATSAPP_CALL_ERROR | id={call.get('id')} event={call.get('event')} | {e}")
    finally:
        await lkapi.aclose()


def handler(event, context):
    method = (event.get("requestContext", {}).get("http", {}).get("method")
              or event.get("httpMethod", "")).upper()

    if method == "GET":
        q = event.get("queryStringParameters") or {}
        if q.get("hub.mode") == "subscribe" and VERIFY_TOKEN and q.get("hub.verify_token") == VERIFY_TOKEN:
            return _resp(200, q.get("hub.challenge", ""))
        return _resp(403, "forbidden")

    if method == "POST":
        raw = event.get("body") or ""
        raw = base64.b64decode(raw) if event.get("isBase64Encoded") else raw.encode()
        headers = {k.lower(): v for k, v in (event.get("headers") or {}).items()}
        if not APP_SECRET or not _valid_signature(raw, headers.get("x-hub-signature-256")):
            return _resp(401, "bad signature")
        payload = json.loads(raw or b"{}")
        print("WHATSAPP_EVENT", json.dumps(payload))
        asyncio.run(process(payload))
        return _resp(200, "ok")  # always 200 once authenticated, otherwise Meta retries

    return _resp(405, "method not allowed")
