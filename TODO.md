# Vriddhi-voice-bot: status and open items

## Done
- [x] Phase 1: replica of Algotalks ECS code, agent name `Vriddhi-voice-bot`, STT = Deepgram nova-3
- [x] Two Lambdas scaffolded: `lambdas/dispatcher` (outbound) and `lambdas/webhook_receiver` (Meta verify + signature check, routing not wired)

## Next (one at a time)
- [x] Phase 2a: `backgroundNoise` (`none`/`office`) + `backgroundNoiseVolume` in /call body; default none. Agent side untested until a live call
- [x] Phase 2b: WhatsApp built (`channel: "whatsapp"` outbound in dispatcher; inbound/connect/hangup in webhook receiver). Routing tested with fakes only (`python tests/test_lambdas.py`)
- [ ] Phase 3: latency pass (measure first, then change one thing at a time)

## You need to provide / set up
- [ ] LiveKit **Cloud** project: URL, API key, secret (WhatsApp connector is Cloud-only)
- [ ] API keys: Deepgram (STT), Sarvam (TTS), Cerebras (LLM)
- [ ] SIP: outbound trunk in LiveKit + Plivo/Twilio number -> `SIP_OUTBOUND_TRUNK_ID`
      (only for phone calls; skip if WhatsApp-only). Inbound: dispatch rule + `LIVEKIT_INBOUND_SIP_URI_TEMPLATE`
- [ ] Meta: WhatsApp Business number, phone number ID, permanent Cloud API access token,
      Meta app with webhook subscribed to `calls`, verify token, call hours (inbound), outbound permission
- [ ] Public HTTPS URL for the webhook receiver Lambda (Function URL or API Gateway) = Meta callback URL; also give the dispatcher Lambda an API Gateway/SQS trigger
- [ ] AWS: account id, ECR repo `vriddhi/agent`, ECS cluster/service, IAM role `vriddhi-ecs-task-role`,
      SSM params under `/vriddhi/*`, DynamoDB table `ContextIvr` (+ S3 context bucket)
- [ ] ECS service autoscaling policy (not in the Algotalks code; configure in AWS)
- [ ] Decide `TRANSCRIPT_URL` (where end-of-call transcripts go)

## Risks to check
- Deepgram nova-3 Malayalam (`ml`) support is unverified; Algotalks used Sarvam STT for it. Test before launch.
- Never commit real keys (Algotalks `.env` has live ones; rotate them).

## Verify on the first real WhatsApp call (built from docs, untested live)
- [x] Meta payload shape confirmed in Meta docs: SDP at `calls[].session.sdp`, `direction` USER_INITIATED / BUSINESS_INITIATED
- [x] Agent hangup: `end_session` sends disconnect(BUSINESS_INITIATED) using `whatsapp_call_id` from room metadata (set by dispatcher/receiver). Meta then still sends `terminate`; the receiver's duplicate disconnect errors and is logged as WHATSAPP_CALL_ERROR (expected, harmless)
- [ ] Confirm on a real call: `WHATSAPP_HANGUP_SENT` appears in agent logs and the user's phone hangs up
- [ ] Agent needs `WHATSAPP_ACCESS_TOKEN` (SSM `/vriddhi/WHATSAPP_ACCESS_TOKEN`, already in taskdef secrets)
- [ ] Meta: business-initiated calls need prior user call permission (error 138006), 10,000 calls/24h per number
- [ ] Inbound `accept` uses wait_until_answered=False so the webhook returns fast; confirm the greeting isn't clipped
- [ ] Lambda cold start on `connect` (outbound): Meta needs the SDP answer back immediately. Use provisioned concurrency on the receiver if calls drop silent
- [ ] Build both Lambda images from the repo root: `docker build -f lambdas/<name>/Dockerfile .`
- [ ] Set receiver env: WHATSAPP_VERIFY_TOKEN, WHATSAPP_APP_SECRET, WHATSAPP_ACCESS_TOKEN, WHATSAPP_INBOUND_CONTEXT_ID, LIVEKIT_*
- [ ] Set dispatcher env: LIVEKIT_*, WHATSAPP_PHONE_NUMBER_ID, WHATSAPP_ACCESS_TOKEN, (SIP_OUTBOUND_TRUNK_ID for SIP)
- [ ] Both Lambdas need DynamoDB read on `ContextIvr` (IAM)
