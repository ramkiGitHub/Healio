"""
Healio — Conversational Health OS
==================================
AI-powered medical assistant for Indian clinics.

Package structure:
    app/config.py        — Settings and environment variables
    app/constants.py     — Enums and static values
    app/exceptions.py    — Custom exception hierarchy
    app/logging_config.py — Structured logging setup
    app/main.py          — FastAPI application entry point
    app/channels/        — Messaging channel integrations (Telegram, WhatsApp)
    app/graph/           — LangGraph AI pipeline (nodes, edges, state, graph)
    app/tools/           — LangChain tools (EHR, Calendar, Alerts) [Phase 2]
    app/nlp/             — HuggingFace BioBERT NLP layer [Phase 2]
"""
