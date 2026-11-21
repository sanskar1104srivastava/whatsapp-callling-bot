"""Shared by the dispatcher and the webhook receiver: turn a request into agent params + metadata."""
import json
import time

from db_mapper.contextIvr import get_context_by_id

AGENT_NAME = "Vriddhi-voice-bot"  # must match agent/src/survey_agent.py
NOISE_OPTIONS = {"none", "office"}  # keep in sync with agent/src/survey_agent.py
CHANNELS = {"sip", "whatsapp"}


def safe_room_name(prefix: str, name: str) -> str:
    safe = "".join(c if c.isalnum() else "-" for c in str(name).strip().lower()).strip("-")
    return f"{prefix}-{safe or 'caller'}-{int(time.time())}"


def build_call_params(body: dict) -> dict:
    name = body.get("name")
    phone_number = body.get("phoneNumber")
    context_id = body.get("contextId")
    info = body.get("info")

    if not name or not str(name).strip():
        raise ValueError("Missing or empty name")
    if not phone_number or not str(phone_number).strip():
        raise ValueError("Missing or empty phoneNumber")
    if not context_id:
        raise ValueError("contextId is required")
    if info is not None and not isinstance(info, str):
        raise ValueError("info must be a string")

    channel = str(body.get("channel") or "sip").strip().lower()
    if channel not in CHANNELS:
        raise ValueError(f"channel must be one of {sorted(CHANNELS)}")

    context_data = get_context_by_id(context_id)
    if not context_data:
        raise ValueError("Invalid or inactive contextId")

    greeting_template = context_data.get("greeting")
    try:
        greeting_text = greeting_template.format(name=name) if greeting_template else None
    except Exception:
        greeting_text = greeting_template

    instructions = (context_data.get("prompt") or "").strip()
    if info and info.strip():
        instructions += (
            "\n\n"
            "Refer to the following information strictly as background context.\n"
            "Do not ask questions unrelated to this information.\n\n"
            f"Context note: {info.strip()}"
        )

    noise = str(body.get("backgroundNoise") or "none").strip().lower()
    if noise not in NOISE_OPTIONS:
        print(f"Unknown backgroundNoise {noise!r}, using none")
        noise = "none"
    try:
        noise_volume = (
            float(body["backgroundNoiseVolume"]) if body.get("backgroundNoiseVolume") is not None else None
        )
    except (TypeError, ValueError):
        noise_volume = None

    return {
        "name": name,
        "phone_number": phone_number,
        "instructions": instructions,
        "knowledge_base_id": context_data.get("knowledgeBaseId"),
        "greeting": greeting_text,
        "context_id": context_id,
        "language": context_data.get("language"),
        "gender": context_data.get("gender"),
        "closing": context_data.get("closing"),
        "background_noise": noise,
        "background_noise_volume": noise_volume,
        "channel": channel,
    }


def build_metadata(params: dict) -> str:
    """JSON the agent reads from ctx.job.metadata."""
    keys = ("instructions", "phone_number", "name", "greeting", "context_id", "language",
            "gender", "closing", "background_noise", "channel")
    data = {k: params[k] for k in keys}
    if params.get("knowledge_base_id"):
        data["knowledge_base_id"] = params["knowledge_base_id"]
    if params.get("background_noise_volume") is not None:
        data["background_noise_volume"] = params["background_noise_volume"]
    return json.dumps(data)


async def tag_room_with_call_id(lkapi, api, room_name: str, whatsapp_call_id: str) -> None:
    """Room metadata carries Meta's call id so the agent can send the business-initiated terminate
    (the id doesn't exist yet when outbound metadata is built). Best effort."""
    try:
        await lkapi.room.update_room_metadata(
            api.UpdateRoomMetadataRequest(room=room_name, metadata=json.dumps({"whatsapp_call_id": whatsapp_call_id}))
        )
    except Exception as e:
        print(f"Could not tag room {room_name} with whatsapp_call_id: {e}")
