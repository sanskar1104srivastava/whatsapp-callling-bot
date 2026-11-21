# Vriddhi inbound: WhatsApp chat + WhatsApp call + use-case dashboard

Builds on `plan.md` (voice router, classifier, queue, latency). This doc adds the **chat path** and the **dashboard**, and ties them to the same routing core.

## 0. Repo layout (built to drop into Rekon-AI later)

Rekon-AI already has portals shaped `<name>/{backend,frontend}`, FastAPI routers (`backend/app/routers`), pages grouped per feature (`pages/whatsapp-automation/Wa*.jsx`) and sidebar link arrays in `Layout.jsx`. New code copies those shapes, so integration is copy-folder + one `include_router` + one sidebar array.

```
ai-calling/
  backend/app/ai_calling/     FastAPI router + store + models. Merge: copy to firm-portal/backend/app/ai_calling, add app.include_router(router)
  backend/dev_main.py         local-only runner (not copied)
  frontend/src/pages/ai-calling/   Ac*.jsx pages (AcDashboard, AcUseCases, AcUseCaseForm, AcRouting, AcConversations...). Merge: copy to firm-portal/frontend/src/pages/ai-calling, add routes + AI_CALLING_LINKS in Layout.jsx
  frontend/src/dev/           local shell only (Layout, main, copy of Rekon index.css vars), not copied
routing-core/                 pure Python: prism client, classify, priority queue. Rekon equivalent: shared/
agent/  lambdas/              existing voice agent + webhook Lambdas (unchanged; rename to voice-agent / whatsapp-call-lambdas on request, Docker paths depend on the current names)
```

Rules: no auth or tenancy code inside `ai-calling/` (Rekon's `get_session`/token dependency gets injected at merge); the store sits behind one small interface so JSON file now, DynamoDB later; no new frontend dependencies beyond Rekon's (react, router, axios, lucide, @dnd-kit).

## 1. Big picture

```
                         Meta WhatsApp Cloud API
                  (one webhook: fields = messages, calls)
                                  |
                       webhook_receiver Lambda
              verify signature, dedupe, look up vendor by phone_number_id
                 |                                      |
          messages (chat)                         calls (voice)
                 |                                      |
        SQS FIFO (group = wa_id)              accept_whatsapp_call -> LiveKit
                 |                                      |
        ECS: chat-bot service                  ECS: LiveKit agent (exists)
        (LLM replies, tools)                   Deepgram nova-3 STT, Sarvam bulbul TTS,
                 |                             Cerebras gpt-oss LLM, Hindi
                 +------------ shared routing core ------+
                        classify() -> priority queue -> use-case runner
                        (Sarvam Prism = decision model)
                                  |
                   DynamoDB: Vendors, UseCases, Sessions, Conversations, Customers
                                  |
                      Dashboard API (API GW + Lambda) <- React dashboard
```

Why SQS between Lambda and ECS: Meta expects a 200 within seconds and retries otherwise. The Lambda acks fast; ECS processes at its own pace. FIFO group per customer keeps messages in order. The voice branch is unchanged from `plan.md` (the agent already exists).

## 2. Routing core (shared by chat and voice)

One Python package, imported by both ECS services. Reuses the design in `plan.md` section 3.

1. First turn: bot asks "what do you need?" (vendor's `purposeGreeting`).
2. `classify(text, usecases)` makes **one** call to Prism (`POST https://api.sarvam.ai/v2/systemone`, model `sarvam-prism`, header `api-subscription-key`) with `state` = the customer's words and a batch of questions:
   - one `choice` question, `criteria` = {usecaseId: intentDescription}. Probabilities sum to 1, so this answers "which is the main intent?".
   - one `noul` (yes/no) question per use case, "Does the customer also want <intent>?". Each is an independent P(yes), so this answers "are there several?". Prism has no multi-label type; per-use-case `noul` is my inference from the docs, test it.
   - Output tokens are 0 and the state is billed once per request, so the extra questions are cheap (₹4 per 1M input tokens).
   - Returns `[{usecase_id, p_choice, p_yes}]`. Hindi and Hinglish are supported, but Sarvam's own numbers show weaker confidence on Indian languages (38% of answers clear 0.9 vs 68% in English), so thresholds must be tuned on Hindi samples, not copied from the docs' ~0.9 advice.
   - Prism is non-streaming, can vary slightly between identical calls, and returns 429/502/503 under load: retry with backoff, fall back to the default use case.
   - Backend stays swappable (`CLASSIFIER=prism|llm`), the Cerebras JSON classifier from `plan.md` is the fallback.
3. Thresholds decide single / multi / ambiguous (as in `plan.md`). Never queue everything.
4. Queue is sorted by the **priority the vendor set in the dashboard** (lower first). Stored in `Sessions` (DynamoDB) for chat, `session.userdata` for voice.
5. Runner executes the head use case with its own prompt/tools/slots. `done` pops the next one with a bridging line. `reroute` handles mid-call change. `canPreempt` decides if a new intent can interrupt.
6. Every decision is logged (scores, chosen, queued, turn) so the dashboard can show a "routing trace".

Chat vs voice differences only: chat state lives in DynamoDB (stateless ECS tasks, 24h WhatsApp window = session TTL); voice state lives in the agent process.

## 3. Use case model (what the user configures)

Extends `ContextIvr`. This is the main thing the UI edits.

| Field | Notes |
|---|---|
| `usecaseId`, `name`, `type` | e.g. `order`, `appointment`, `faq`, `complaint`, `custom` |
| `intentDescription`, `examples[]` | what Prism matches against |
| `prompt`, `greeting`, `closing`, `language`, `gender` | same as Algotalks context (already exists) |
| `knowledgeBaseId` | RAG, as in Algotalks |
| `slots[]` | fields to collect (name, address, item, qty...) |
| `tools[]` | actions: create order webhook, book slot, send template (HTTP endpoint + auth) |
| `channels` | `chat`, `voice`, or both |
| `priority`, `canPreempt`, `enabled` | queue ordering |

`type=order` ships as a built-in template (slots + create-order tool) so "taking order" works without custom wiring. Others start as prompt + KB + optional tools.

## 4. Voice stack (as requested)

- Inbound only, Hindi, via LiveKit Cloud WhatsApp connector (already built in this repo).
- STT Deepgram nova-3 multi, TTS Sarvam **`bulbul:v4-flash`**, LLM **Cerebras gpt-oss only** (no other LLM in the voice path), Prism for decisioning.
- bulbul v4 flash: same request shape as v3, so it is a one-line model change. Speaker IDs differ from v3 (v3's `shubh` is invalid; omit `speaker` for the default or pick from the v4 catalog). Streaming works over WebSocket `wss://api.sarvam.ai/text-to-speech/ws?model=bulbul:v4-flash` or HTTP `/text-to-speech/stream`. Sarvam's older WebSocket reference page still lists only v2/v3, so check the LiveKit Sarvam plugin version accepts `v4-flash` (the agent pins `livekit-agents==1.5.16`).
- **Dummy calling for now:** the voice path runs against a fake/test caller and sample data, no real customer data or real Meta numbers. Real WhatsApp wiring comes after the UI and routing core work.
- Note: `plan.md` flagged Deepgram code-switching/Hinglish as untested and Malayalam as unsupported. Hindi-only inbound avoids the Malayalam issue; run the Hinglish test before launch.

## 5. Dashboard

**Theme: Rekon-AI** (`Rekon-AI/firm-portal/frontend`): React 19 + Vite + plain JSX, CSS variables (`--green #15803d`, `--green-dark`, `--green-light`, Inter, light surface, `--shadow-*`), `lucide-react` icons, `react-router-dom`, `Layout.jsx` sidebar shell, `ProtectedRoute`, `LoadingOverlay`, WhatsApp-OTP login. `@dnd-kit` is already a dependency there, so priority drag-to-reorder is free. Copy the shell, do not add Tailwind.

**Functions: from AlgoTalks dashboard, inbound-only subset**

| Page | Source in Algotalks | Notes |
|---|---|---|
| ~~Login~~ | AuthPages | **dropped**: UI is local only, no auth, no tenancy |
| Overview | Dashboard/Home | calls + chats today, avg duration, top use cases, routing accuracy, missed |
| **Use cases** (list, add, edit) | ContextListTable, AddContextForm | the core page; add priority drag list, enable toggle, channel badges, test-in-browser |
| **Routing settings** | new | greeting, default use case, thresholds, max intents per session, preempt rules |
| Conversations | CallHistory | one list for chats and calls; transcript, recording, routing trace, queue shown per session |
| Live | LiveCalls | active calls and chats, listen-in later |
| Customers | CallerListTable | profile by phone, history across chat and voice |
| Callbacks | CallbacksTable | keep only if a use case schedules them |
| WhatsApp connection | Rekon WhatsAppSetup | numbers, `phone_number_id`, webhook status |

**Dropped (outbound / not needed now):** Events, campaigns, event users, manual dialer, human calling, web agents, admin leads, the template-demo pages (alerts, avatars, badges, charts samples). Outbound can come back later as its own section.

Language: JSX like Rekon (Algotalks is TSX). If you want TS, say so; the theme copy is the same either way.

**Local-only UI (decided):** no login, no tenancy, one implicit vendor. Simplest version: the dashboard runs with `vite dev` and talks to a small local FastAPI that stores use cases and routing settings in a JSON/SQLite file, with seeded dummy conversations and customers (no real user data). The data shapes in section 6 stay the same so swapping to DynamoDB + API Gateway later only changes the storage layer. Skip `ProtectedRoute`, the OTP screen and the Customers page's real data (show seeded samples).

## 6. Data tables (DynamoDB)

`Vendors` (numbers, greeting, thresholds), `UseCases` (section 3), `Sessions` (chat state, queue, TTL), `Conversations` (messages/turns, channel, routing trace, recording/transcript URL), `Customers` (phone, name, summary).

## 7. Phases

| # | Phase | Done when |
|---|---|---|
| 1 | Data model + `UseCases`/`Vendors` tables, dashboard API CRUD | Use case created via API, read by the agent |
| 2 | Local dashboard (Rekon theme, no auth) + local FastAPI + Use cases + Routing settings pages, seeded dummy data | Use cases and priority edited in the UI on localhost |
| 3 | Routing core with Prism `classify()` + queue, tested offline on sample utterances | Accuracy table, thresholds chosen |
| 4 | Chat path: receiver extension, SQS, ECS chat-bot, order use case end to end | A WhatsApp message "I want to order X" completes an order |
| 5 | Voice path wired to the same core (`plan.md` phases 3 and 5) | Inbound call routes and runs two queued use cases |
| 6 | Conversations, Live, Customers, Overview pages | Dashboard shows real traffic |
| 7 | Latency pass (`plan.md` section 7), autoscaling, alarms | Targets met |

Phase 3 comes before 4 and 5 so both channels share one tested core.

## 8. Things I could not verify, please confirm

Resolved: Prism is real and documented (sections 2 and sources below); use `bulbul:v4-flash`; voice LLM is Cerebras only; UI is local with no login or tenancy; calling is dummy for now.

Still open:
1. **Chat LLM**: Cerebras gpt-oss too (confirmed: Cerebras for chat too, Hindi/Hinglish text supported, mocked order tool), or Bedrock like the Rekon bot?
2. **Chat language**: Hindi / Hinglish text too? Affects Prism examples.
3. **Tools**: do use cases call vendor HTTP endpoints (order creation), or write only to our own store? For now: a mocked order tool.
4. Dashboard folder: new top-level `dashboard/` (+ `dashboard-api/` local FastAPI) in this repo? (Assumed yes.)
5. Does Prism's per-use-case `noul` batching detect two intents reliably in Hindi? Only a test on sample utterances answers this (phase 3).

## 9. Sources (Sarvam)
- [Decide (Sarvam Prism) API](https://docs.sarvam.ai/api-reference/decisions/systemone), [Prism model page](https://docs.sarvam.ai/api/token-factory/models/sarvam-prism), [Prism blog](https://www.sarvam.ai/blogs/sarvam-prism), [open weights](https://huggingface.co/sarvamai/prism) (needs a 120 GB GPU, so use the hosted API)
- [bulbul v4 flash best-practice guide](https://docs.sarvam.ai/api/api-guides-tutorials/text-to-speech/best-practice-guide-for-bulbul-v-4-flash), [TTS streaming](https://docs.sarvam.ai/api/api-guides-tutorials/text-to-speech/streaming-api)
- Not verified: Sarvam's latency claim (44 ms median on a B200) is vendor-reported and is not the hosted-API latency; measure it. A third-party page says Malayalam/Odia still need v3.
