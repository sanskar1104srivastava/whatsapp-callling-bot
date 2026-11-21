"""Outbound dispatcher Lambda. Triggers: API Gateway (single or list body) or SQS.

Body per contact: name, phoneNumber, contextId, [info], [channel: sip|whatsapp],
[backgroundNoise: none|office], [backgroundNoiseVolume].
"""
import asyncio
import json
import os

from livekit import api
from livekit.protocol.agent_dispatch import RoomAgentDispatch

from call_params import AGENT_NAME, build_call_params, build_metadata, safe_room_name, tag_room_with_call_id

SIP_OUTBOUND_TRUNK_ID = os.getenv("SIP_OUTBOUND_TRUNK_ID")  # only needed for channel=sip
WA_PHONE_NUMBER_ID = os.getenv("WHATSAPP_PHONE_NUMBER_ID")
WA_ACCESS_TOKEN = os.getenv("WHATSAPP_ACCESS_TOKEN")
WA_API_VERSION = os.getenv("WHATSAPP_API_VERSION", "26.0")

INTER_CALL_DELAY = 0.6


async def initiate_sip_call(lkapi, params: dict) -> str:
    if not SIP_OUTBOUND_TRUNK_ID:
        raise RuntimeError("SIP_OUTBOUND_TRUNK_ID not set")
    room_name = safe_room_name("ivr", params["name"])
    await lkapi.agent_dispatch.create_dispatch(
        api.CreateAgentDispatchRequest(
            agent_name=AGENT_NAME, room=room_name, metadata=build_metadata(params)
        )
    )
    await lkapi.sip.create_sip_participant(
        api.CreateSIPParticipantRequest(
            room_name=room_name,
            sip_trunk_id=SIP_OUTBOUND_TRUNK_ID,
            sip_call_to=params["phone_number"],
            participant_identity="phone_user",
            hide_phone_number=False,
        )
    )
    return room_name


async def initiate_whatsapp_call(lkapi, params: dict) -> str:
    if not (WA_PHONE_NUMBER_ID and WA_ACCESS_TOKEN):
        raise RuntimeError("WHATSAPP_PHONE_NUMBER_ID / WHATSAPP_ACCESS_TOKEN not set")
    # The agent joins now; Meta's SDP answer arrives later at the webhook receiver,
    # which completes the call with connect_whatsapp_call.
    res = await lkapi.connector.dial_whatsapp_call(
        api.DialWhatsAppCallRequest(
            whatsapp_phone_number_id=WA_PHONE_NUMBER_ID,
            whatsapp_to_phone_number=params["phone_number"].lstrip("+").replace(" ", ""),
            whatsapp_cloud_api_version=WA_API_VERSION,
            whatsapp_api_key=WA_ACCESS_TOKEN,
            room_name=safe_room_name("wa", params["name"]),
            agents=[RoomAgentDispatch(agent_name=AGENT_NAME, metadata=build_metadata(params))],
        )
    )
    await tag_room_with_call_id(lkapi, api, res.room_name, res.whatsapp_call_id)
    print(f"WhatsApp dial started | call_id={res.whatsapp_call_id} | room={res.room_name}")
    return res.room_name


async def poll_dialing_status(lkapi, room_name: str, phone_number: str) -> bool:
    """SIP only: True once a participant shows a dialing/ringing/active status."""
    for _ in range(20):
        await asyncio.sleep(0.5)
        try:
            participants = await lkapi.room.list_participants(api.ListParticipantsRequest(room=room_name))
            for p in participants.participants:
                if p.attributes.get("sip.callStatus", "") in ("dialing", "active", "automation", "ringing"):
                    print(f"Dialing confirmed | phone={phone_number}")
                    return True
        except Exception as e:
            print(f"Poll error for {phone_number}: {e}")
    print(f"Dialing not confirmed within 10s | phone={phone_number}")
    return False


async def process_with_rate_limit(call_params_list: list) -> set:
    """Returns the phone numbers that failed to initiate or confirm."""
    failed, polls = set(), []
    lkapi = api.LiveKitAPI()
    try:
        for i, params in enumerate(call_params_list):
            phone = params["phone_number"]
            try:
                if params["channel"] == "whatsapp":
                    await initiate_whatsapp_call(lkapi, params)
                else:
                    room = await initiate_sip_call(lkapi, params)
                    polls.append((phone, asyncio.create_task(poll_dialing_status(lkapi, room, phone))))
                print(f"Initiated {params['channel']}: {phone} ({i + 1}/{len(call_params_list)})")
            except Exception as e:
                print(f"Initiation failed for {phone}: {e}")
                failed.add(phone)
            if i < len(call_params_list) - 1:
                await asyncio.sleep(INTER_CALL_DELAY)

        results = await asyncio.gather(*[t for _, t in polls], return_exceptions=True)
        for (phone, _), ok in zip(polls, results):
            if ok is not True:
                failed.add(phone)
    finally:
        await lkapi.aclose()
    return failed


def handler(event, context):
    # SQS: partial-batch failures are retried
    if "Records" in event:
        params_list, ids = [], {}
        for rec in event["Records"]:
            try:
                p = build_call_params(json.loads(rec["body"]))
                params_list.append(p)
                ids[p["phone_number"]] = rec["messageId"]
            except Exception as e:
                print(f"Validation failed: {e}")
        failed = asyncio.run(process_with_rate_limit(params_list))
        return {"batchItemFailures": [{"itemIdentifier": ids[p]} for p in failed if p in ids]}

    # API Gateway
    if "body" in event:
        raw = event.get("body") or "{}"
        body = json.loads(raw) if isinstance(raw, str) else raw
        try:
            params_list = [build_call_params(b) for b in (body if isinstance(body, list) else [body])]
        except ValueError as e:
            return {"statusCode": 400, "body": json.dumps({"error": str(e)})}
        failed = asyncio.run(process_with_rate_limit(params_list))
        if failed:
            return {"statusCode": 207, "body": json.dumps({"status": "partial_success", "failed": sorted(failed)})}
        return {"statusCode": 200, "body": json.dumps({"status": "calls triggered"})}

    return {"statusCode": 400, "body": json.dumps({"error": "Unrecognised event"})}
