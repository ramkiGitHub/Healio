
## Plan: Healio MVP — Production-Quality Build

**TL;DR:** Every file is written to production standard — full Google-style docstrings, type hints, structured logging, custom exceptions, retry logic, pydantic validation — while staying readable and developer-friendly for onboarding.

---

### Code Standards Applied Everywhere

| Standard                 | Implementation                                                                                                       |
| ------------------------ | -------------------------------------------------------------------------------------------------------------------- |
| **Documentation**  | Google-style docstrings on every class, method, function. Module-level docstrings explaining purpose + usage example |
| **Type hints**     | All function signatures typed (Python 3.11+), Pydantic v2 models for all data shapes                                 |
| **Logging**        | `structlog` — structured JSON logs with context (session_id, patient_id, intent)                                  |
| **Error handling** | Custom exception hierarchy in `app/exceptions.py` — no bare `except`                                            |
| **Retries**        | `tenacity` retry decorators on all external calls (OpenAI, Telegram, HuggingFace)                                  |
| **Config**         | `pydantic-settings` in `app/config.py` — validated at startup, fails fast on missing vars                       |
| **Constants**      | `app/constants.py` — no magic strings or numbers anywhere                                                         |
| **Startup**        | FastAPI `lifespan` — pre-loads BioBERT, initialises DB, verifies API keys on boot                                 |
| **Health check**   | `GET /health` endpoint — liveness + readiness                                                                     |
| **Input safety**   | Webhook payloads sanitised and validated at channel boundary before entering graph                                   |
| **Developer docs** | Every module has a section:*What this does / Why it exists / How to extend it*                                     |

---

### Phase 1 — Project Foundation *(Week 1)*

**Steps:**

1. `pyproject.toml` — all dependencies pinned, dev dependencies group (`pytest`, `httpx`, `ruff`)
2. `Dockerfile` + `docker-compose.yml` — multi-stage build, non-root user, health check
3. `.env.example` — every variable commented with type, default, and activation instructions
4. `.gitignore`, `ruff.toml` (linting config)
5. `app/constants.py` — all intent names, severity levels, channel names as typed enums
6. `app/exceptions.py` — custom exception hierarchy (`HealioBaseError`, `ChannelError`, `GraphError`, `ToolError`, `NLPError`)
7. `app/config.py` — `pydantic-settings` `Settings` class; validates all required vars; logs placeholders as warnings not errors
8. `app/main.py` — FastAPI app with `lifespan`, `/health`, router registration, global exception handler
9. `app/channels/normalizer.py` — `IncomingMessage` Pydantic model + `normalize_telegram()` + `normalize_whatsapp()` *placeholder*
10. `app/channels/telegram.py` — webhook handler with signature verification, input sanitisation, error reply on failure
11. `app/channels/whatsapp.py` — stub endpoint returning `501` with clear comment block explaining activation steps
12. `app/graph/state.py` — `HealioState` TypedDict with docstring for every field
13. `app/graph/nodes.py` — Router Node only (GPT-4o-mini); with tenacity retry, structured logging per node
14. `app/graph/edges.py` — routing functions with docstrings explaining each decision branch
15. `app/graph/graph.py` — graph assembly, SQLite checkpointer, graph compilation

**Verification:** `docker compose up` → send Telegram message → get GPT reply → LangSmith trace visible.

---

### Phase 2 — Core AI & NLP *(Week 2)*

16. `app/nlp/biobert.py` — `BioBERTExtractor` class; pre-loaded in lifespan; `extract_entities()` returns typed `MedicalEntities` Pydantic model; graceful fallback if model unavailable *(parallel with 17)*
17. `app/nlp/severity.py` — `SeverityScorer` class; rule-based v1 with keyword registry in `constants.py`; returns `SeverityLevel` enum *(parallel with 16)*
18. `data/mock_patients.json` — 10 mock profiles (varied conditions, allergies, medications)
19. `app/tools/ehr.py` — `MockEHRTool` LangChain tool; `lookup_patient()` with typed `PatientProfile` return; raises `PatientNotFoundError`
20. `app/tools/alerts.py` — `HITLAlertTool`; sends Telegram alert to `DOCTOR_CHAT_ID`; tenacity retry; sets LangGraph interrupt; documents HITL flow in docstring
21. `app/graph/nodes.py` — add Emergency Node, Profile Lookup Node, update Router Node with severity routing
22. Multi-turn memory — SQLite checkpoint, session scoping documented with example in `graph.py`

**Verification:** "chest pain" → doctor Telegram alert fires → patient receives emergency response → trace in LangSmith shows all nodes.

---

### Phase 3 — Tools & Conversation Flows *(Week 3)*

23. `app/tools/calendar.py` — `CalendarTool` with `CalendarProvider` enum (`mock` | `google`); `MockCalendarProvider` class; `GoogleCalendarProvider` stub with full docstring activation guide *(parallel with 24)*
24. Schedule Node — multi-turn appointment dialogue; state machine within node documented step-by-step *(parallel with 23)*
25. General Q&A Node — system prompt construction with patient profile + allergy flag injection; documented prompt template
26. Allergy flag logic in `MockEHRTool` — `check_allergies()` method with typed return
27. Response Formatter in `normalizer.py` — `format_for_channel()` with per-channel formatting rules
28. End-to-end tests: appointment booking flow, allergy-flagged Q&A flow

**Verification:** 5-turn appointment booking completes correctly. Allergy flag surfaces in response when relevant medication mentioned.

---

### Phase 4 — Deploy & Polish *(Week 4)*

29. Finalize `Dockerfile` (multi-stage, pinned base image)
30. `docker-compose.yml` — SQLite volume, env-file mount, restart policy
31. LangGraph Cloud deployment config
32. LangSmith dashboard verification (token usage, latency per node)
33. `tests/test_graph.py` — routing logic, emergency detection, edge conditions
34. `tests/test_tools.py` — EHR lookup, mock calendar, alert tool, allergy flag
35. `tests/test_channels.py` — normalizer, Telegram handler, WhatsApp 501 stub
36. Demo conversation script (emergency + appointment + Q&A flows)

**Verification:** All tests pass (`pytest`). `docker compose up` cold-starts cleanly. LangGraph Cloud deploy succeeds.

---

**Relevant files**

* `app/main.py`, `app/config.py`, `app/constants.py`, `app/exceptions.py`
* `app/channels/normalizer.py`, `telegram.py`, `whatsapp.py`
* `app/graph/state.py`, `nodes.py`, `edges.py`, `graph.py`
* `app/tools/alerts.py`, `calendar.py`, `ehr.py`
* `app/nlp/biobert.py`, `app/nlp/severity.py`
* `data/mock_patients.json`
* `tests/test_graph.py`, `test_tools.py`, `test_channels.py`
* `Dockerfile`, `docker-compose.yml`, `pyproject.toml`, `.env.example`

---

**Further Considerations**

1. **Docstring format** — I'll use **Google style** (`Args:`, `Returns:`, `Raises:`, `Example:`). Let me know if you prefer NumPy or reStructuredText.
2. **Logging** — I'll use `structlog` for structured JSON logs. If you prefer plain `logging` from stdlib (simpler), say so.
3. **Test runner** — `pytest` with `httpx` for async FastAPI testing. Confirm if you want coverage reports (`pytest-cov`) included from the start.
