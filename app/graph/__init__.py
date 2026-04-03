"""
app/graph/__init__.py
=====================
LangGraph orchestration package for Healio.

This package contains the full AI pipeline:
- state.py    — HealioState TypedDict (the data flowing through the graph)
- nodes.py    — All graph nodes (Router, Emergency, Q&A, Profile, Schedule)
- edges.py    — Conditional routing functions (intent/severity-based)
- graph.py    — Graph assembly, compilation, and the run_graph() entry point
"""
