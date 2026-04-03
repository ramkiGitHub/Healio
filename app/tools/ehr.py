"""
app/tools/ehr.py
================
Mock Electronic Health Record (EHR) tool backed by ``data/mock_patients.json``.

Why this file exists
--------------------
In a production Indian clinic deployment, patient data would come from an EHR
system — ABDM Health Stack, Practo, or a clinic-specific HIS (Hospital
Information System). For the Healio MVP, this file simulates that integration
using a local JSON file with realistic patient profiles.

Replacing this with a real EHR in production means:
1. Implementing a new class that implements ``lookup_patient(patient_id) -> dict``.
2. Swapping the import in ``nodes.py`` (no other changes needed).

Data file: ``data/mock_patients.json``
Loaded once at startup; all lookups run in O(1) from an in-memory dict.

Security note
-------------
The mock data file contains fictional patient records for development use only.
Never commit real patient data. In production, use an authenticated EHR API
with TLS and follow DPDP Act 2023 (India) and HIPAA data handling requirements.

Usage
-----
    from app.tools.ehr import get_ehr_tool

    tool = get_ehr_tool()
    try:
        profile = tool.lookup_patient("P001")
    except PatientNotFoundError:
        profile = {}
"""

import json
from functools import lru_cache
from pathlib import Path

from app.exceptions import EHRLookupError, PatientNotFoundError
from app.logging_config import get_logger

log = get_logger(__name__)

# Path to the mock patient data file, resolved relative to this file's location
# g:\Healio\app\tools\ehr.py → go up 3 levels → g:\Healio\ → data/mock_patients.json
_MOCK_DATA_PATH: Path = (
    Path(__file__).resolve().parent.parent.parent / "data" / "mock_patients.json"
)


class MockEHRTool:
    """In-memory EHR tool backed by ``data/mock_patients.json``.

    Loads all patient records into a dict at initialisation for O(1) lookups.
    Returns a copy of each record to prevent accidental state mutation.

    In production, replace this class with an HTTP client that calls the
    clinic's EHR API (ABDM FHIR endpoint, Practo API, etc.), keeping the
    same ``lookup_patient(patient_id: str) -> dict`` interface.

    Attributes:
        _records: Dict mapping ``patient_id`` → patient profile dict.

    Usage:
        tool = get_ehr_tool()
        profile = tool.lookup_patient("P001")
        print(profile["name"])  # "Anjali Sharma"
    """

    def __init__(self, data_path: Path | None = None) -> None:
        """Load patient records from the JSON data file.

        Args:
            data_path: Override the default data file path. Useful for tests
                       that provide a custom fixture file.

        Raises:
            EHRLookupError: If the data file is missing or contains invalid JSON.
        """
        path = data_path or _MOCK_DATA_PATH

        try:
            with open(path, encoding="utf-8") as f:
                records: list[dict] = json.load(f)
        except FileNotFoundError as exc:
            raise EHRLookupError(
                detail=f"Mock patient data file not found: {path}",
                patient_id="N/A",
            ) from exc
        except json.JSONDecodeError as exc:
            raise EHRLookupError(
                detail=f"Invalid JSON in patient data file {path}: {exc}",
                patient_id="N/A",
            ) from exc

        # Index by patient_id for O(1) lookup
        self._records: dict[str, dict] = {
            record["patient_id"]: record
            for record in records
            if "patient_id" in record
        }

        log.info(
            "ehr_tool_initialised",
            patient_count=len(self._records),
            data_path=str(path),
        )

    def lookup_patient(self, patient_id: str) -> dict:
        """Return the profile for a given patient ID.

        Args:
            patient_id: Unique patient identifier (e.g., ``"P001"``, ``"test_patient"``).
                        Matched exactly — no fuzzy matching.

        Returns:
            A copy of the patient profile dict. Keys include:
            ``patient_id``, ``name``, ``age``, ``blood_group``, ``conditions``,
            ``allergies``, ``medications``, ``last_visit``, ``clinic``.

        Raises:
            PatientNotFoundError: If ``patient_id`` does not exist in the EHR store.

        Example:
            >>> tool = get_ehr_tool()
            >>> profile = tool.lookup_patient("P001")
            >>> profile["name"]
            'Anjali Sharma'
            >>> profile["allergies"]
            ['Aspirin', 'Ibuprofen']
        """
        profile = self._records.get(patient_id)

        if profile is None:
            log.warning("ehr_patient_not_found", patient_id=patient_id)
            raise PatientNotFoundError(patient_id=patient_id)

        log.info(
            "ehr_patient_found",
            patient_id=patient_id,
            name=profile.get("name", "unknown"),
        )
        # Return a shallow copy to prevent callers from mutating the cached record
        return dict(profile)

    @property
    def patient_count(self) -> int:
        """Total number of patient records currently loaded.

        Returns:
            int: Number of records indexed from the data file.
        """
        return len(self._records)


@lru_cache(maxsize=1)
def get_ehr_tool() -> MockEHRTool:
    """Return the application-wide singleton ``MockEHRTool`` instance.

    Uses ``lru_cache`` so the JSON file is read only once at startup.
    All subsequent calls return the same cached instance.

    Returns:
        A fully initialised ``MockEHRTool``.

    Raises:
        EHRLookupError: If the data file cannot be loaded on first call.

    Example:
        >>> tool = get_ehr_tool()
        >>> tool.patient_count
        5
    """
    return MockEHRTool()
