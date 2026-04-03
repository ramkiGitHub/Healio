# Healio — Conversational Health OS
## High-Level Architecture Document

**Version:** 0.1 (MVP)
**Date:** April 3, 2026
**Status:** Draft — Pending Review

---

## 1. Executive Summary

Healio is a Conversational Health OS designed for Indian clinics facing staff shortages. It acts as an AI-powered medical assistant that handles patient queries, performs basic symptom triage, books appointments, detects emergencies, and routes critical cases to a human clinician — all through familiar messaging channels (WhatsApp / Telegram).

The MVP targets clinics in Bengaluru and Tamil Nadu with a SaaS model (₹500–2000/month per user), validating the core loop: **patient message → AI triage → action or escalation**.

---

## 2. Goals & Non-Goals

### MVP Goals
- Accept patient messages over Telegram (primary) and WhatsApp (placeholder)
- Detect emergency keywords and trigger human-in-loop doctor alerts
- Maintain multi-turn conversation memory per patient session
- Look up patient profiles from a mock EHR store
- Support appointment scheduling via mock/placeholder calendar integration
- Extract medical entities (symptoms, conditions) using BioBERT NLP
- Trace all interactions for debugging and compliance using LangSmith

### Non-Goals (MVP)
- Real EHR / HL7 FHIR integration (post-MVP)
- ISO 13485 / HIPAA compliance (post-MVP)
- Clinic admin dashboard / web UI (post-MVP)
- Billing / subscription management (post-MVP)
- Voice interface (post-MVP)
- Multi-language support beyond English (post-MVP)

---

## 3. System Architecture Overview

```
┌─────────────────────────────────────────────────────────────────────┐
│                        PATIENT (End User)                           │
│               Telegram App  │  WhatsApp App [PLACEHOLDER]           │
└───────────────────┬─────────┴──────────────┬────────────────────────┘
                    │                         │
                    ▼                         ▼
┌─────────────────────────────────────────────────────────────────────┐
│                    CHANNEL LAYER (FastAPI)                           │
│                                                                     │
│  ┌──────────────────────┐    ┌─────────────────────────────────┐   │
│  │  Telegram Webhook    │    │  WhatsApp Webhook [PLACEHOLDER]  │   │
│  │  /webhook/telegram   │    │  /webhook/whatsapp               │   │
│  └──────────┬───────────┘    └────────────────┬────────────────┘   │
│             └──────────────┬──────────────────┘                    │
│                            ▼                                        │
│              ┌─────────────────────────┐                            │
│              │   Message Normalizer    │                            │
│              │  (Channel-agnostic DTO) │                            │
│              └────────────┬────────────┘                           │
└───────────────────────────┼─────────────────────────────────────────┘
                            │
                            ▼
┌─────────────────────────────────────────────────────────────────────┐
│                   AI ORCHESTRATION LAYER (LangGraph)                │
│                                                                     │
│   ┌─────────────────────────────────────────────────────────────┐   │
│   │                    GRAPH STATE                              │   │
│   │  { session_id, patient_id, messages[], patient_profile,    │   │
│   │    intent, severity, appointment_context, flags{} }        │   │
│   └─────────────────────────────────────────────────────────────┘   │
│                            │                                        │
│                            ▼                                        │
│   ┌────────────────────────────────────────────────────────────┐    │
│   │                   ROUTER NODE                              │    │
│   │   Classifies intent:                                       │    │
│   │   emergency | appointment | query | profile | chitchat     │    │
│   └──────┬──────────┬──────────┬──────────┬───────────────────┘    │
│          │          │          │          │                         │
│          ▼          ▼          ▼          ▼                         │
│   ┌──────────┐ ┌─────────┐ ┌───────┐ ┌───────────────┐            │
│   │Emergency │ │Schedule │ │General│ │Patient Profile│            │
│   │  Node    │ │  Node   │ │Q&A    │ │Lookup Node    │            │
│   │          │ │         │ │ Node  │ │               │            │
│   └────┬─────┘ └────┬────┘ └───┬───┘ └───────┬───────┘            │
│        │             │          │              │                    │
│        ▼             ▼          ▼              ▼                    │
│   ┌─────────┐  ┌──────────┐ ┌──────────┐ ┌──────────────────┐     │
│   │ Alert   │  │ Calendar │ │  OpenAI  │ │  Mock EHR Tool   │     │
│   │ Tool    │  │  Tool    │ │  GPT-4o  │ │                  │     │
│   │(HITL)   │  │[PLACEHOLDER]│  -mini  │ │                  │     │
│   └─────────┘  └──────────┘ └──────────┘ └──────────────────┘     │
│                         │                                           │
│                         ▼                                           │
│              ┌──────────────────────────┐                           │
│              │   Response Formatter     │                           │
│              │  (channel-aware output)  │                           │
│              └──────────────────────────┘                           │
└─────────────────────────────────────────────────────────────────────┘
                            │
          ┌─────────────────┼─────────────────┐
          ▼                 ▼                 ▼
┌──────────────────┐ ┌───────────────┐ ┌──────────────┐
│  MEMORY LAYER    │ │  NLP LAYER    │ │  OBS. LAYER  │
│                  │ │               │ │              │
│  SQLite          │ │  HuggingFace  │ │  LangSmith   │
│  (Checkpointer)  │ │  BioBERT NER  │ │  Tracing     │
│  → PostgreSQL    │ │  (symptoms,   │ │              │
│     (post-MVP)   │ │  conditions,  │ │              │
│                  │ │  medications) │ │              │
└──────────────────┘ └───────────────┘ └──────────────┘
```

---

## 4. Component Descriptions

### 4.1 Channel Layer
Exposes HTTP webhook endpoints via **FastAPI**. Each channel (Telegram, WhatsApp) has its own handler that validates incoming payloads, extracts message text and sender identity, and normalises them into a common `IncomingMessage` DTO before passing to the orchestration layer.

| Component | Technology | Status |
|---|---|---|
| Telegram Webhook | `python-telegram-bot` / Telegram Bot API | Active (MVP) |
| WhatsApp Webhook | Twilio / Meta Cloud API | **PLACEHOLDER** |
| Message Normalizer | Pydantic DTO | Active (MVP) |

**WhatsApp Placeholder Note:**
The `/webhook/whatsapp` endpoint will be scaffolded but will return a `501 Not Implemented` response until credentials are configured. Configuration will require:
- `WHATSAPP_PROVIDER` env var (`twilio` | `meta`)
- `WHATSAPP_ACCOUNT_SID` / `WHATSAPP_AUTH_TOKEN` (Twilio) **or**
- `WHATSAPP_ACCESS_TOKEN` / `WHATSAPP_PHONE_NUMBER_ID` (Meta Cloud API)

---

### 4.2 AI Orchestration Layer (LangGraph)

The core of Healio. A stateful directed graph where each node performs a discrete action and conditional edges route execution based on intent and severity.

#### Graph State Schema
```python
class HealioState(TypedDict):
    session_id: str            # Unique per patient session
    patient_id: str            # Derived from channel sender ID
    messages: list[BaseMessage]  # Full conversation history
    patient_profile: dict      # Fetched from EHR tool
    intent: str                # Classified by Router Node
    severity: str              # "emergency" | "urgent" | "routine"
    appointment_context: dict  # Date, time, clinic preferences
    flags: dict                # e.g., { "human_loop_triggered": bool }
```

#### Nodes

| Node | Responsibility |
|---|---|
| **Router Node** | Uses GPT-4o-mini + BioBERT output to classify intent and severity |
| **Emergency Node** | Detects emergency keywords/symptoms, composes alert, triggers HITL tool |
| **Schedule Node** | Handles appointment booking flow, calls Calendar Tool |
| **General Q&A Node** | Answers medical queries using GPT-4o-mini with patient context |
| **Profile Lookup Node** | Fetches patient profile from Mock EHR, injects into state |

#### Conditional Edges (Routing Logic)
```
Router → Emergency Node      if severity == "emergency"
Router → Schedule Node       if intent == "appointment"
Router → Profile Lookup Node if intent == "profile" OR patient_profile is empty
Router → General Q&A Node    fallback (query, chitchat, unknown)
```

---

### 4.3 Tools

#### Human-in-the-Loop (HITL) Alert Tool
- Sends an alert to the clinic doctor/nurse when an emergency is detected
- MVP: Sends a Telegram message to a pre-configured `DOCTOR_CHAT_ID`
- The graph pauses (LangGraph interrupt) and waits for a doctor acknowledgment before continuing

| Config | Description |
|---|---|
| `DOCTOR_CHAT_ID` | Telegram chat ID of the on-call doctor |
| `ALERT_TIMEOUT_SECONDS` | Seconds to wait before auto-escalating |

#### Calendar / Appointment Tool
- Manages appointment scheduling, rescheduling, and cancellations
- Mock implementation returns hardcoded availability slots
- Real implementation will integrate with Google Calendar API

| Config | Description | Status |
|---|---|---|
| `CALENDAR_PROVIDER` | `google` \| `mock` | `mock` (MVP) |
| `GOOGLE_CALENDAR_CREDENTIALS_PATH` | Path to OAuth2 JSON credentials | **PLACEHOLDER** |
| `GOOGLE_CALENDAR_ID` | Target calendar ID | **PLACEHOLDER** |

**Google Calendar Placeholder Note:**
The tool interface is production-ready. Switching to live Google Calendar requires:
1. Creating a Google Cloud project and enabling **Google Calendar API**
2. Creating OAuth2 credentials (service account recommended for server-side)
3. Setting `CALENDAR_PROVIDER=google` and providing credential path

#### Mock EHR Tool
- Reads patient records from a local `data/mock_patients.json` file
- Keyed by `patient_id` (derived from channel sender ID)
- Returns: name, age, known conditions, allergies, last visit, medications

---

### 4.4 NLP Layer — BioBERT (HuggingFace)

Used alongside GPT-4o-mini for structured medical entity extraction.

| Task | Model | Purpose |
|---|---|---|
| Named Entity Recognition | `d4data/biomedical-ner-all` or `allenai/scibert` | Extract symptoms, body parts, conditions, medications from patient text |
| Severity Classification | Fine-tuned BERT (or rule-based v1) | Score urgency: routine / urgent / emergency |

> **MVP Approach:** Rule-based severity scoring with keyword lists for MVP speed. BioBERT NER integration in Week 2.

---

### 4.5 Memory Layer

| Stage | Technology | Purpose |
|---|---|---|
| MVP | SQLite + LangGraph SQLiteCheckpointer | Per-session message history, state persistence |
| Post-MVP | PostgreSQL | Multi-tenant, persistent, queryable history |

Memory is scoped per `session_id`. A new session is created per conversation thread. Patient identity (`patient_id`) links sessions to the mock EHR record.

---

### 4.6 Observability Layer

| Tool | Purpose |
|---|---|
| **LangSmith** | Full LangGraph trace logging — every node input/output, LLM call, token count |
| **Python `logging`** | Structured application logs (INFO / ERROR) |
| **Sentry** (post-MVP) | Exception monitoring and alerting |

Required env vars:
```
LANGCHAIN_TRACING_V2=true
LANGCHAIN_API_KEY=<your_key>
LANGCHAIN_PROJECT=healio-mvp
```

---

## 5. Data Flow — End-to-End Example

**Scenario: Patient sends "I have severe chest pain" on Telegram**

```
1. Patient sends message on Telegram
2. Telegram delivers POST to /webhook/telegram
3. FastAPI handler validates request, extracts text + sender_id
4. Message Normalizer creates IncomingMessage DTO
5. LangGraph invoked with session_id = sender_id
6. Router Node:
   a. BioBERT NER extracts: symptom="chest pain", severity_hint="severe"
   b. GPT-4o-mini classifies intent="query", severity="emergency"
7. Conditional edge routes to Emergency Node
8. Emergency Node:
   a. Composes emergency alert message
   b. Calls HITL Alert Tool → sends alert to DOCTOR_CHAT_ID on Telegram
   c. LangGraph sets interrupt checkpoint, waits for doctor ack
9. Response Formatter generates patient-facing message:
   "I've alerted the on-call doctor immediately. Please stay calm.
    Call 112 if symptoms worsen. Help is on the way."
10. FastAPI sends reply back to patient on Telegram
11. LangSmith logs full trace
```

---

## 6. Technology Stack Summary

| Layer | Technology | Version / Notes |
|---|---|---|
| Language | Python | 3.11+ |
| API Framework | FastAPI | Async, webhook handling |
| AI Orchestration | LangGraph | Stateful graph, HITL support |
| LLM Framework | LangChain | Tool integrations, prompt management |
| LLM (Conversation) | OpenAI GPT-4o-mini | Cost-effective, healthcare-tuned prompts |
| LLM (Medical NLP) | HuggingFace Transformers + BioBERT | NER, symptom extraction |
| Memory / State | LangGraph SQLiteCheckpointer | SQLite → PostgreSQL post-MVP |
| Messaging (Primary) | Telegram Bot API | python-telegram-bot |
| Messaging (Secondary) | WhatsApp (Twilio / Meta) | **PLACEHOLDER** |
| Appointment Scheduling | Google Calendar API | **PLACEHOLDER** — mock in MVP |
| Tracing | LangSmith | Full graph observability |
| Containerisation | Docker + Docker Compose | Local dev + deployment |
| Deployment | LangGraph Cloud | Production |
| Package Manager | uv / pip | pyproject.toml |

---

## 7. Environment Configuration

All secrets and toggles are managed via environment variables (`.env` file, never committed).

```env
# ── LLM ──────────────────────────────────────────────
OPENAI_API_KEY=<required>
OPENAI_MODEL=gpt-4o-mini

# ── LangSmith Tracing ─────────────────────────────────
LANGCHAIN_TRACING_V2=true
LANGCHAIN_API_KEY=<required>
LANGCHAIN_PROJECT=healio-mvp

# ── Telegram ─────────────────────────────────────────
TELEGRAM_BOT_TOKEN=<required>
DOCTOR_CHAT_ID=<required>         # Telegram chat ID for HITL alerts
ALERT_TIMEOUT_SECONDS=30

# ── WhatsApp [PLACEHOLDER] ────────────────────────────
WHATSAPP_PROVIDER=                # Set to "twilio" or "meta" when ready
WHATSAPP_ACCOUNT_SID=             # Twilio only
WHATSAPP_AUTH_TOKEN=              # Twilio only
WHATSAPP_ACCESS_TOKEN=            # Meta Cloud API only
WHATSAPP_PHONE_NUMBER_ID=         # Meta Cloud API only

# ── Google Calendar [PLACEHOLDER] ─────────────────────
CALENDAR_PROVIDER=mock            # Change to "google" when ready
GOOGLE_CALENDAR_CREDENTIALS_PATH= # Path to service account JSON
GOOGLE_CALENDAR_ID=               # e.g., primary or clinic@gmail.com

# ── Storage ───────────────────────────────────────────
DATABASE_URL=sqlite:///./healio.db   # Change to postgres:// post-MVP

# ── App ───────────────────────────────────────────────
APP_ENV=development               # development | production
LOG_LEVEL=INFO
```

---

## 8. Project Structure

```
healio/
├── app/
│   ├── main.py                   # FastAPI app, router registration
│   ├── config.py                 # Settings (pydantic-settings, .env)
│   ├── channels/
│   │   ├── __init__.py
│   │   ├── normalizer.py         # IncomingMessage DTO
│   │   ├── telegram.py           # Telegram webhook handler
│   │   └── whatsapp.py           # WhatsApp handler [PLACEHOLDER]
│   ├── graph/
│   │   ├── __init__.py
│   │   ├── state.py              # HealioState TypedDict
│   │   ├── nodes.py              # All graph nodes
│   │   ├── edges.py              # Conditional routing functions
│   │   └── graph.py              # Graph assembly + compilation
│   ├── tools/
│   │   ├── __init__.py
│   │   ├── alerts.py             # HITL emergency alert tool
│   │   ├── calendar.py           # Appointment tool (mock + Google placeholder)
│   │   └── ehr.py                # Mock EHR lookup tool
│   └── nlp/
│       ├── __init__.py
│       └── biobert.py            # HuggingFace BioBERT NER wrapper
├── data/
│   └── mock_patients.json        # Mock patient profiles
├── tests/
│   ├── test_graph.py
│   ├── test_tools.py
│   └── test_channels.py
├── docs/
│   ├── requirements.md
│   └── high-level-architecture.md  ← this file
├── .env.example                  # Template (committed, no secrets)
├── .env                          # Actual secrets (git-ignored)
├── .gitignore
├── docker-compose.yml
├── Dockerfile
└── pyproject.toml
```

---

## 9. Key Design Decisions

| Decision | Choice | Rationale |
|---|---|---|
| LangGraph over raw LangChain | LangGraph | Native support for stateful multi-turn flows, HITL interrupts, and graph-based conditional routing — essential for medical triage |
| SQLite for MVP memory | SQLite | Zero setup, compatible with LangGraph's built-in checkpointer; easy migration to PostgreSQL |
| GPT-4o-mini over GPT-4o | GPT-4o-mini | 10x cheaper; sufficient for conversational triage; upgrade path available |
| BioBERT for NER | HuggingFace | Specialised medical vocabulary; more accurate than GPT for structured entity extraction |
| Telegram-first | Telegram | Free bot API, no business approval required for MVP; WhatsApp requires verified business account |
| Mock EHR | JSON file | Unblocks development; interface-compatible with real EHR adapters later |
| No auth for MVP | Skip | Fastest path to validate core AI loop; add Auth0 post-pilot |

---

## 10. Risks & Mitigations

| Risk | Impact | Mitigation |
|---|---|---|
| OpenAI API latency in medical context | High | Add 3s timeout + fallback message; cache common responses |
| False negative on emergency detection | Critical | Conservative keyword list + severity scoring; always err toward escalation |
| BioBERT model cold start (HuggingFace) | Medium | Pre-load model at startup; or use API endpoint for MVP |
| WhatsApp Business approval delay | Medium | Telegram as primary; WhatsApp as placeholder until approved |
| Patient data privacy (even mock) | Medium | No real PII in MVP; document data handling for future DPDP/HIPAA compliance |
| LangGraph Cloud costs | Low | Start on free tier; monitor LangSmith for token usage |

---

## 11. Future Roadmap (Post-MVP)

| Phase | Features |
|---|---|
| **Phase 2** | WhatsApp Business API integration, Google Calendar live sync, PostgreSQL migration |
| **Phase 3** | Real EHR integration (HL7 FHIR), Auth0 + MFA, multi-clinic support |
| **Phase 4** | Voice interface (Whisper ASR), regional language support (Tamil, Kannada) |
| **Phase 5** | ISO 13485 compliance, DPDP Act compliance, enterprise clinic dashboard |
| **Phase 6** | Medical device troubleshooting agent (ZEISS-like enterprise use case) |

---

*Document maintained by: Healio Engineering*
*Next review: After MVP pilot (Week 6)*
