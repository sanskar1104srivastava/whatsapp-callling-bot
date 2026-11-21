# Vriddhi-voice-bot: plan for inbound routing, outbound calling and latency

Status of the repo today: the replica, `background_noise`, the dispatcher and webhook receiver Lambdas, and WhatsApp (inbound + outbound + agent hangup) are built and tested with fakes only (see `TODO.md`). This plan covers what comes next.

## 1. Goals

| | Inbound | Outbound |
|---|---|---|
| Who starts the call | Caller dials the vendor's single number (SIP) or WhatsApp number | Backend POSTs to the dispatcher Lambda |
| Context chosen by | The caller's first answer (intent routing) | The request (`contextId`), as today |
| Intent classification | **Yes** | **No, never** |
| Channels | SIP, WhatsApp | SIP, WhatsApp |

One agent (`Vriddhi-voice-bot`) serves both. The dispatch metadata says which mode it is in. Outbound never touches the routing code.

## 2. Architecture

```
OUTBOUND  backend -> dispatcher Lambda -> LiveKit dispatch (mode=outbound, full context in metadata)
                                       -> agent runs the context directly (what exists today)

INBOUND   SIP:      caller -> trunk -> ONE generic dispatch rule -> agent (mode=inbound)
          WhatsApp: Meta -> webhook receiver Lambda -> accept_whatsapp_call -> agent (mode=inbound)

          agent start: dialed number -> VendorRoutes -> vendor + its contexts
          router greets "how may I help?" -> caller answers -> classify -> specialist agent for that context
```

### Inbound entry (confirmed in LiveKit docs)
- SIP caller number is `sip.phoneNumber` and the dialed number is `sip.trunkPhoneNumber` on the SIP participant. Note `sip.phoneNumber` is missing if the dispatch rule sets `HidePhoneNumber`.
- The agent reads them from `session.room_io.linked_participant` after `session.start()`.
- WhatsApp: the receiver already has `metadata.phone_number_id`; it looks up the vendor and puts `{mode: "inbound", vendor_id}` in the dispatch metadata. For SIP the agent does the lookup itself, so SIP needs only one dispatch rule for all numbers (no per-number rules).
- SIP room names include the caller's number (PII in logs). Prefer a random room-name prefix on the rule.

### Router and specialists
- `RouterAgent` plays the vendor's `purposeGreeting` while it loads the vendor's contexts in parallel, so the data is ready when the caller answers.
- On the first final user turn, `RouterAgent.on_user_turn_completed` calls `classify()` and swaps in the chosen specialist with `session.update_agent(...)`. This method exists in the pinned `livekit-agents==1.5.16` and blocks new user turns during the swap. Chat history is copied across, so the specialist answers the same utterance.
- The specialist is an ordinary `SurveyAssistant`-style agent built from the context (prompt, KB, voice). Per-agent TTS/STT is supported by LiveKit if contexts differ in language or gender.
- Shared facts (name, phone, details already collected) live in `session.userdata` (a dataclass) so the caller is never asked twice after a swap.
- Vendors write only their own context prompt. We append a short footer giving each specialist two tools: `reroute(reason)` ("out of scope") and `intent_done()`.

## 3. The classifier

`classify(text, contexts) -> [{context_id, probability, quote}]`, one function with swappable backends, chosen by env (`CLASSIFIER=llm|jev`).

| Backend | How | Notes |
|---|---|---|
| **LLM (default)** | Separate Cerebras `gpt-oss-120b` call with a strict JSON schema (constrained decoding) returning the intents and a quote from the caller's words for each | Cerebras advises not combining `tools` and `response_format` in one call, so this is a separate call, not the router's tool call. Latency unmeasured. |
| **Jev (TypeSafe)** | `POST https://api.typesafe.ai/v1/systemone` with `choice` (up to 255 options, probabilities sum to ~1) and `noul` (independent yes/no probability) questions in one batch | Hosted, $0.042/M input tokens, output free, about 150 ms typical per third-party posts. Hindi/Hinglish support is not documented anywhere I found. |
| Laya (self-hosted) | Open weights, 33-40 ms on a T4 GPU | Needs a GPU (Fargate has none), about 20 options max, weak without fine-tuning. Not recommended now. |

Jev and Laya are "decision models": they return typed probabilities, not text. Sources on them are blogs and vendor pages; treat the numbers as indicative and run our own test (section 8, phase 4).

### From probabilities to a queue (never "queue all contexts")
A `choice` distribution sums to about 1: the scores compete, they are not five intents.
1. **Single-intent check:** top score >= `T_high` and lead over second >= `margin` -> route, queue nothing.
2. **Multi-intent check** (only if 1 is not decisive): independent per-context yes/no scores; keep those >= `T_multi` and (LLM backend) with a supporting quote; cap at `maxIntentsPerCall` (default 2); order by vendor `priority`.
3. **Ambiguous:** nothing clears the thresholds -> one clarifying question, then the vendor's default context.
4. **More than one survived:** confirm in one line ("I can help with the order and the appointment, right?").
5. Only what the caller said is queued. Anything forgotten comes up at "anything else?".
- Thresholds, `maxIntentsPerCall` and the backend are per vendor in `VendorRoutes`, tuned on real calls (these models can be overconfident). Hard limits (cap, dedupe, declines) are enforced in code.

### Queue and priority
- Queue lives in `session.userdata`, sorted by the vendor's `priority` (lower first), ties by order mentioned.
- The agent states the plan ("I'll help with X first, then Y"); `intent_done()` pops the next intent with a short bridging line.
- A new intent mid-call is inserted by priority. It interrupts the current one only if the context has `canPreempt`.
- A caller who declines ("just the first one") clears the queue.

### Intent change mid-call
- **Stage 1 (build first):** the specialist's `reroute(reason)` tool, driven by the appended footer. No extra latency.
- **Stage 2 (only if Stage 1 misses cases):** run `classify()` as a non-blocking background task on each final user turn; switch only if another context wins with a clear margin on two consecutive turns. This prevents flapping.
- Every swap logs: from, to, scores, turn number.

## 4. Outbound (unchanged behavior, no classification)
- Dispatcher keeps `contextId` required. `channel` = `sip|whatsapp`, `backgroundNoise` = `none|office`.
- Agent mode `outbound` skips the router entirely and runs the context as today.
- Open item that already exists: the dispatcher still has no `/inbound` SIP handler (the old FastAPI had a Plivo one). With the generic dispatch rule above it is not needed.

## 5. Data model

| Table / item | New fields |
|---|---|
| `ContextIvr` (existing) | `intentName`, `intentDescription`, `examples[]`, `priority` (int, lower first), `canPreempt` (bool) |
| `VendorRoutes` (new) | `numbers[]` (E.164 + WhatsApp `phone_number_id`), `vendorId`, `purposeGreeting`, `defaultContextId`, `contextIds[]`, `classifier`, `T_high`, `margin`, `T_multi`, `maxIntentsPerCall`, `maxRoutingTurns` |

- Dispatch metadata for inbound carries only `{mode, channel, vendor_id}`. Full prompts of every context in metadata would be too large and stale; the agent loads contexts from DynamoDB at call start (needs IAM read and the shared `db_mapper` back in the agent image).
- Vendor admin UI for intent fields and priorities is out of scope here; assume a script/console edit first.

## 6. Speech and language

- **STT:** Deepgram nova-3 as you asked.
  - **Gap:** Malayalam is not in Deepgram's nova-3 or nova-2 language list. Hindi, Tamil, Telugu, Kannada, Marathi, Bengali, Gujarati, Punjabi, Urdu, Assamese are.
  - **Plan:** `create_stt()` picks Deepgram where supported and Sarvam STT (already in the replica) for Malayalam.
- **Hinglish:** Deepgram's code-switching page does not name Hindi or Hinglish, and recommends `endpointing=100` ms for code-switching. Must be tested on real recordings before choosing `language=multi` vs `hi` for the inbound router (which cannot know the language in advance).
- **TTS:** Sarvam (bulbul:v3), as in the replica.
- **LLM:** Cerebras `gpt-oss-120b` (reasoning `medium` today; test `low`).

## 7. Latency plan (applies to both modes)

Measure first. LiveKit already provides per-turn numbers: `ChatMessage.metrics.e2e_latency` (user stops speaking -> agent starts), plus `EOUMetrics.end_of_utterance_delay`, `LLMMetrics.ttft`, `TTSMetrics.ttfb` keyed by `speech_id`. Log them per turn to CloudWatch, then change one thing at a time:

1. Remove the random 0.2-1.5 s stagger before `session.start`; replace the per-call S3 context download with a DynamoDB/cache fetch overlapped with the greeting.
2. `turn_handling`: `endpointing.min_delay` about 0.3-0.5 s, `preemptive_generation` on (`preemptive_tts` optional).
3. Cerebras reasoning `medium` -> `low`; tune Silero VAD (`min_silence_duration`).
4. Replace the per-call synchronous CloudWatch handler and full KB-response logging with non-blocking logging.
5. Prewarm in `setup_fnc`, set `num_idle_processes`.
6. Make `BVCTelephony` / `BVC` a toggle and measure its CPU cost per call.
7. Inbound only: budget for the routing hop (classify + swap) and measure it separately.
- A `noul`/`choice` classifier call in parallel with nothing else is on the critical path of the first inbound turn only; later turns are unaffected.

## 8. Phases and what "done" means

| # | Phase | Done when |
|---|---|---|
| 1 | Latency baseline: per-turn metrics logging, no behavior change | A call shows e2e latency per turn in logs |
| 2 | Data model + shared `db_mapper` in agent + `VendorRoutes` lookup by dialed number / `phone_number_id` | Dialed number resolves to vendor + contexts in a test |
| 3 | Router + specialist swap + LLM classifier (single intent), SIP dispatch rule + receiver changes | A test inbound call routes by first answer |
| 4 | Classifier bake-off on real utterances (LLM vs Jev; Hindi/Hinglish); set thresholds | Accuracy and latency table, thresholds chosen |
| 5 | Multi-intent queue, `reroute`, `intent_done`, priority/preempt | Two-intent call runs both in priority order |
| 6 | Latency pass (section 7) | Target set from baseline met, or gap explained |
| 7 | Stage 2 background classifier, only if reroute misses | Decided from call logs |

Phase 1 goes first because every later change needs a number to compare against.

## 9. What I need from you

**Accounts and keys**
- LiveKit Cloud project (URL, API key, secret). The WhatsApp connector is Cloud-only.
- Deepgram, Sarvam, Cerebras API keys.
- Optional for the bake-off: a TypeSafe (Jev) API key (or Runware / Vercel AI Gateway access).
- Meta: WhatsApp Business number, `phone_number_id`, permanent access token, app secret, verify token, call hours enabled (inbound), user call permission (outbound).
- SIP: provider (Plivo/Twilio/other), inbound DID(s), a LiveKit inbound trunk, and one dispatch rule; outbound trunk ID.
- AWS: account id, region, ECR repo, ECS cluster/service, IAM roles, SSM parameters under `/vriddhi/`, DynamoDB `ContextIvr` (+ new `VendorRoutes`).

**Decisions**
1. Languages vendors' callers use (Hindi, Hinglish, Malayalam, English?). This decides STT and whether Jev is even a candidate.
2. Priority semantics (lower runs first?) and whether per-context `canPreempt` is enough.
3. Default `maxIntentsPerCall` (proposed 2; configurable to 3).
4. One vendor, several numbers -> one route: allowed? (assumed yes.)
5. Is there a Malayalam-capable need on inbound? If yes, the router cannot rely on Deepgram alone.

**Data**
- 30-50 real (or realistic) first utterances per vendor with the correct context, ideally some Hindi/Hinglish and some multi-intent. Needed for the bake-off in phase 4. Recordings or transcripts both work.

## 10. Risks

| Risk | Mitigation |
|---|---|
| Hinglish/Hindi STT or Jev accuracy unknown | Phase 4 test on real utterances before committing |
| Malayalam not supported by Deepgram | Sarvam STT for `ml` |
| Classifier adds latency to the first inbound turn | Measure in phase 1/3; embedding or Jev as fallback backends |
| Overconfident probabilities queue wrong intents | Thresholds per vendor, quotes required (LLM), caps in code |
| Agent swap mid-turn drops the first utterance | Spike in phase 3; chat history copy + `update_agent` blocks new turns during the swap |
| Meta/LiveKit WhatsApp specifics (payload shape confirmed; hangup, cold start unverified) | First real call; provisioned concurrency on the receiver if outbound `connect` is late |
| Provider docs are third-party blogs for Jev/Laya | Treat numbers as indicative; our own benchmark decides |

## 11. Sources
- LiveKit: [agent handoffs](https://docs.livekit.io/agents/logic/agents-handoffs.md), [workflows](https://docs.livekit.io/agents/logic/workflows.md), [SIP participant attributes](https://docs.livekit.io/reference/telephony/sip-participant.md), [SIP dispatch rules](https://docs.livekit.io/telephony/accepting-calls/dispatch-rule.md), [metrics](https://docs.livekit.io/deploy/observability/data.md), [turn tuning](https://docs.livekit.io/agents/logic/turns/tuning.md), [WhatsApp connector](https://docs.livekit.io/telephony/connectors/whatsapp.md), [background audio](https://docs.livekit.io/agents/multimodality/audio/background-audio.md)
- Meta WhatsApp Calling: [user-initiated](https://developers.facebook.com/docs/whatsapp/cloud-api/calling/user-initiated-calls), [business-initiated](https://developers.facebook.com/docs/whatsapp/cloud-api/calling/business-initiated-calls)
- Deepgram: [languages](https://developers.deepgram.com/docs/models-languages-overview), [code-switching](https://developers.deepgram.com/docs/multilingual-code-switching)
- Cerebras: [structured outputs](https://inference-docs.cerebras.ai/capabilities/structured-outputs)
- Decision models: [TypeSafe API](https://docs.typesafe.ai/api), [Jev vs Laya](https://dev.to/jamilxt/jev-vs-laya-the-same-ai-idea-one-closed-and-one-open-3c6e), [Runware](https://runware.ai/blog/jev-laya-and-the-emerging-role-of-decision-models), [Cisco](https://blogs.cisco.com/ai/jev-decision-models-on-trial), [Vercel AI Gateway](https://vercel.com/changelog/laya-decision-model-now-available-on-ai-gateway-free-through-october-31)
- Verified in installed `livekit-agents==1.5.16` source: `AgentSession.update_agent`, `AgentSession.userdata`, `AgentTask`, `RoomIO.linked_participant`, `ChatMessage.metrics.e2e_latency`
