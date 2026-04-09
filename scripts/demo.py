"""
scripts/demo.py
===============
Healio MVP — end-to-end demonstration script.

Runs three canned conversation flows against the LangGraph pipeline
*locally* (no HTTP server required) using real LLM calls:

  Flow 1 — Emergency triage
    Patient reports chest pain → emergency node fires → HITL doctor alert
    is attempted (will fail gracefully if TELEGRAM_BOT_TOKEN is not live)

  Flow 2 — Appointment booking (multi-turn)
    Patient books a Tuesday appointment for a follow-up over 3 turns

  Flow 3 — General Q&A with allergy conflict
    Patient P001 (allergic to Penicillin) asks about Penicillin →
    allergy warning must appear in the reply

Usage
-----
    # From repo root — requires a .env with a real OPENAI_API_KEY
    python scripts/demo.py

    # Skip live LLM calls (uses stubbed responses via OPENAI_API_KEY=sk-test-...)
    # This will raise an AuthenticationError from OpenAI — only use with a
    # real key.

Environment
-----------
The script loads .env automatically via python-dotenv if present.
Set DEMO_PATIENT_ID to override the EHR patient id used in flows 2 & 3
(default: "P001" which exists in data/mock_patients.json).

Exit codes
----------
  0 — all flows completed without unhandled exceptions
  1 — one or more flows raised an unexpected exception
"""

from __future__ import annotations

import asyncio
import os
import sys
import textwrap
import time
from pathlib import Path

# ── Bootstrap: load .env and add repo root to sys.path ───────────────────────
_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT))

try:
    from dotenv import load_dotenv

    load_dotenv(_REPO_ROOT / ".env")
except ImportError:
    pass  # python-dotenv not installed; rely on env vars being set already

# ── Stub env vars so pydantic-settings doesn't fail on import ─────────────────
os.environ.setdefault("OPENAI_API_KEY", "sk-demo-placeholder")
os.environ.setdefault("LANGCHAIN_API_KEY", "ls-demo-placeholder")
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "000000000:AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAaa")
os.environ.setdefault("DOCTOR_CHAT_ID", "123456789")
os.environ.setdefault("DISABLE_BIOBERT", "true")
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./data/db/healio_demo.db")

from app.channels.normalizer import IncomingMessage  # noqa: E402
from app.constants import ChannelType  # noqa: E402
from app.graph.graph import run_graph  # noqa: E402

# ── ANSI colour helpers ───────────────────────────────────────────────────────

_USE_COLOUR = sys.stdout.isatty()


def _c(code: str, text: str) -> str:
    return f"\033[{code}m{text}\033[0m" if _USE_COLOUR else text


def cyan(t: str) -> str:
    return _c("36;1", t)


def green(t: str) -> str:
    return _c("32;1", t)


def yellow(t: str) -> str:
    return _c("33;1", t)


def red(t: str) -> str:
    return _c("31;1", t)


def bold(t: str) -> str:
    return _c("1", t)


# ── Pretty-printer ────────────────────────────────────────────────────────────

_WIDTH = 72


def _divider(char: str = "─", colour: str = "90") -> None:
    print(_c(colour, char * _WIDTH))


def _print_turn(turn: int, patient_text: str, reply: str) -> None:
    _divider()
    print(f"  {bold(f'Turn {turn}')}  {yellow('Patient:')} {patient_text}")
    wrapped = textwrap.fill(reply, width=_WIDTH - 12, subsequent_indent=" " * 12)
    print(f"  {' ' * 7}{green('Healio:')}  {wrapped}")


# ── Flow runners ──────────────────────────────────────────────────────────────

DEMO_PATIENT_ID = os.environ.get("DEMO_PATIENT_ID", "P001")


def _make_message(
    session_id: str,
    text: str,
    patient_id: str = DEMO_PATIENT_ID,
) -> IncomingMessage:
    return IncomingMessage(
        session_id=session_id,
        patient_id=patient_id,
        channel=ChannelType.WHATSAPP,
        text=text,
        sender_name="Demo Patient",
    )


async def _run_flow(
    name: str,
    turns: list[tuple[str, str]],   # list of (session_id, message_text)
    patient_id: str = DEMO_PATIENT_ID,
) -> bool:
    """Run a single demo flow.

    Args:
        name: Display name for the flow.
        turns: Ordered list of ``(session_id, text)`` pairs.
        patient_id: Patient ID to use for EHR lookup.

    Returns:
        True if all turns completed without error, False otherwise.
    """
    print()
    _divider("═", "36")
    print(f"  {cyan(name)}")
    _divider("═", "36")

    success = True
    for i, (session_id, text) in enumerate(turns, start=1):
        msg = _make_message(session_id, text, patient_id)
        t0 = time.perf_counter()
        try:
            reply = await run_graph(msg)
            elapsed = time.perf_counter() - t0
            _print_turn(i, text, reply)
            print(_c("90", f"  {'':>14}[{elapsed:.2f}s]"))
        except Exception as exc:  # noqa: BLE001
            elapsed = time.perf_counter() - t0
            _print_turn(i, text, red(f"ERROR — {type(exc).__name__}: {exc}"))
            print(_c("90", f"  {'':>14}[{elapsed:.2f}s]"))
            success = False

    return success


# ── Individual flows ──────────────────────────────────────────────────────────

# EMERGENCY_SESSION = "telegram:demo_emergency_001"
# APPOINTMENT_SESSION = "telegram:demo_appt_001"
# ALLERGY_SESSION = "telegram:demo_allergy_001"

EMERGENCY_SESSION="WhATSAPP:demo_emergency_001"
APPOINTMENT_SESSION="WHATSAPP:demo_appt_001"
ALLERGY_SESSION="WHATSAPP:demo_allergy_001"


async def flow_emergency() -> bool:
    """Flow 1: Emergency triage — chest pain."""
    return await _run_flow(
        name="Flow 1 — Emergency Triage (chest pain)",
        turns=[
            (EMERGENCY_SESSION, "I have severe chest pain and I can't breathe properly"),
        ],
    )


async def flow_appointment() -> bool:
    """Flow 2: Multi-turn appointment booking."""
    return await _run_flow(
        name="Flow 2 — Appointment Booking (multi-turn)",
        turns=[
            (APPOINTMENT_SESSION, "I'd like to book an appointment with the doctor"),
            (APPOINTMENT_SESSION, "Tuesday next week, around 10 in the morning"),
            (APPOINTMENT_SESSION, "For a routine follow-up on my diabetes medication"),
            (APPOINTMENT_SESSION, "Yes, that's correct, please confirm the booking"),
        ],
    )


async def flow_allergy_qa() -> bool:
    """Flow 3: General Q&A with allergy conflict detection."""
    return await _run_flow(
        name="Flow 3 — Q&A with Allergy Conflict (Penicillin)",
        turns=[
            (ALLERGY_SESSION, "Can I take Penicillin for my throat infection?"),
        ],
    )


# ── Main ──────────────────────────────────────────────────────────────────────

async def main() -> int:
    print()
    print(bold("  ██╗  ██╗███████╗ █████╗ ██╗     ██╗ ██████╗ "))
    print(bold("  ██║  ██║██╔════╝██╔══██╗██║     ██║██╔═══██╗"))
    print(bold("  ███████║█████╗  ███████║██║     ██║██║   ██║"))
    print(bold("  ██╔══██║██╔══╝  ██╔══██║██║     ██║██║   ██║"))
    print(bold("  ██║  ██║███████╗██║  ██║███████╗██║╚██████╔╝"))
    print(bold("  ╚═╝  ╚═╝╚══════╝╚═╝  ╚═╝╚══════╝╚═╝ ╚═════╝ "))
    print(bold("  Conversational Health OS — MVP Demo"))
    print(_c("90", f"  Patient ID: {DEMO_PATIENT_ID}  |  DISABLE_BIOBERT={os.environ.get('DISABLE_BIOBERT', 'false')}"))
    print()

    results = await asyncio.gather(
        flow_emergency(),
        return_exceptions=False,
    )
    # Run appointment and allergy flows sequentially so sessions don't collide
    results = list(results)
    results.append(await flow_appointment())
    results.append(await flow_allergy_qa())

    # ── Summary ───────────────────────────────────────────────────────────────
    print()
    _divider("═", "36")
    print(f"  {bold('Demo Summary')}")
    _divider()
    flow_names = [
        "Flow 1 — Emergency Triage",
        "Flow 2 — Appointment Booking",
        "Flow 3 — Allergy Q&A",
    ]
    all_passed = True
    for name, ok in zip(flow_names, results):
        status = green("PASS") if ok else red("FAIL")
        print(f"  [{status}]  {name}")
        if not ok:
            all_passed = False

    print()
    if all_passed:
        print(green("  All flows completed successfully."))
    else:
        print(red("  One or more flows encountered errors (see above)."))
    print()

    return 0 if all_passed else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
