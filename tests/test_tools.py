"""
tests/test_tools.py
===================
Unit tests for the tool layer: MockEHRTool and HITLAlertTool.

MockEHRTool tests use the real ``data/mock_patients.json`` fixture so we
also verify the data file is in good shape.

HITLAlertTool tests mock the httpx.Client to avoid real Telegram API calls.
"""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import httpx

from app.exceptions import AlertToolError, EHRLookupError, PatientNotFoundError
from app.tools.alerts import HITLAlertTool
from app.tools.ehr import MockEHRTool, get_ehr_tool


# ── MockEHRTool ────────────────────────────────────────────────────────────────

class TestMockEHRToolInitialisation:
    """Tests for MockEHRTool.__init__() loading behaviour."""

    def test_loads_all_five_patients(self) -> None:
        tool = MockEHRTool()
        assert tool.patient_count == 5

    def test_raises_ehr_error_for_missing_file(self, tmp_path: Path) -> None:
        missing = tmp_path / "nonexistent.json"
        with pytest.raises(EHRLookupError, match="not found"):
            MockEHRTool(data_path=missing)

    def test_raises_ehr_error_for_invalid_json(self, tmp_path: Path) -> None:
        bad_json = tmp_path / "bad.json"
        bad_json.write_text("this is not json", encoding="utf-8")
        with pytest.raises(EHRLookupError, match="Invalid JSON"):
            MockEHRTool(data_path=bad_json)

    def test_skips_records_without_patient_id(self, tmp_path: Path) -> None:
        data = [
            {"patient_id": "P001", "name": "Alice"},
            {"name": "No ID Here"},   # no patient_id key → skipped
        ]
        fixture = tmp_path / "partial.json"
        fixture.write_text(json.dumps(data), encoding="utf-8")
        tool = MockEHRTool(data_path=fixture)
        assert tool.patient_count == 1


class TestMockEHRToolLookup:
    """Tests for MockEHRTool.lookup_patient()."""

    def setup_method(self) -> None:
        self.tool = MockEHRTool()

    def test_known_patient_returns_profile(self) -> None:
        profile = self.tool.lookup_patient("P001")
        assert profile["name"] == "Anjali Sharma"
        assert profile["patient_id"] == "P001"

    def test_test_patient_has_expected_fields(self) -> None:
        profile = self.tool.lookup_patient("test_patient")
        assert profile["name"] == "Ravi Kumar"
        assert profile["age"] == 35
        assert "Penicillin" in profile["allergies"]
        assert "Type 2 Diabetes" in profile["conditions"]

    def test_unknown_patient_raises_not_found(self) -> None:
        with pytest.raises(PatientNotFoundError) as exc_info:
            self.tool.lookup_patient("nonexistent_id")
        assert exc_info.value.patient_id == "nonexistent_id"

    def test_returned_profile_is_a_copy(self) -> None:
        profile = self.tool.lookup_patient("P001")
        profile["name"] = "Mutated"
        # Fetch again — original should be unchanged
        fresh = self.tool.lookup_patient("P001")
        assert fresh["name"] == "Anjali Sharma"

    def test_p002_has_no_allergies(self) -> None:
        profile = self.tool.lookup_patient("P002")
        assert profile["allergies"] == []

    def test_p004_has_multiple_conditions(self) -> None:
        profile = self.tool.lookup_patient("P004")
        assert len(profile["conditions"]) >= 2

    def test_all_five_patient_ids_are_loadable(self) -> None:
        for pid in ["test_patient", "P001", "P002", "P003", "P004"]:
            profile = self.tool.lookup_patient(pid)
            assert profile["patient_id"] == pid


class TestGetEHRToolSingleton:
    """Tests for the get_ehr_tool() lru_cache singleton."""

    def test_returns_mock_ehr_tool_instance(self) -> None:
        tool = get_ehr_tool()
        assert isinstance(tool, MockEHRTool)

    def test_singleton_returns_same_instance(self) -> None:
        tool1 = get_ehr_tool()
        tool2 = get_ehr_tool()
        assert tool1 is tool2


# ── HITLAlertTool ──────────────────────────────────────────────────────────────

class TestHITLAlertToolFormatAlert:
    """Tests for HITLAlertTool._format_alert() (no HTTP calls)."""

    def test_emergency_uses_siren_emoji(self) -> None:
        text = HITLAlertTool._format_alert("P001", "Anjali", "chest pain", "tg:1", "emergency")
        assert "🚨" in text

    def test_urgent_uses_warning_emoji(self) -> None:
        text = HITLAlertTool._format_alert("P001", "Anjali", "high fever", "tg:1", "urgent")
        assert "⚠️" in text

    def test_contains_patient_id(self) -> None:
        text = HITLAlertTool._format_alert("P001", "Anjali", "chest pain", "tg:1", "emergency")
        assert "P001" in text

    def test_contains_patient_name(self) -> None:
        text = HITLAlertTool._format_alert("P001", "Anjali Sharma", "chest pain", "tg:1", "emergency")
        assert "Anjali Sharma" in text

    def test_contains_session_id(self) -> None:
        text = HITLAlertTool._format_alert("P001", "Anjali", "chest pain", "telegram:999", "emergency")
        assert "telegram:999" in text

    def test_message_truncated_at_200_chars(self) -> None:
        long_msg = "a" * 300
        text = HITLAlertTool._format_alert("P001", "X", long_msg, "tg:1", "emergency")
        assert "..." in text

    def test_short_message_not_truncated(self) -> None:
        short_msg = "chest pain"
        text = HITLAlertTool._format_alert("P001", "X", short_msg, "tg:1", "emergency")
        assert "chest pain" in text
        assert "..." not in text.split(short_msg)[1].split("\n")[0]

    def test_unknown_patient_name_falls_back(self) -> None:
        text = HITLAlertTool._format_alert("P001", "", "chest pain", "tg:1", "emergency")
        assert "Unknown Patient" in text

    def test_severity_is_uppercased(self) -> None:
        text = HITLAlertTool._format_alert("P001", "X", "pain", "tg:1", "emergency")
        assert "EMERGENCY" in text


class TestHITLAlertToolSendAlert:
    """Tests for HITLAlertTool.send_alert() with mocked httpx."""

    def test_successful_alert_posts_to_telegram(self) -> None:
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()

        with patch("app.tools.alerts.httpx.Client") as mock_client_cls:
            mock_client = MagicMock()
            mock_client.__enter__ = MagicMock(return_value=mock_client)
            mock_client.__exit__ = MagicMock(return_value=False)
            mock_client.post.return_value = mock_response
            mock_client_cls.return_value = mock_client

            tool = HITLAlertTool()
            # Should not raise
            tool.send_alert(
                patient_id="P001",
                patient_name="Anjali Sharma",
                message="I have chest pain",
                session_id="telegram:123",
                severity="emergency",
            )

            mock_client.post.assert_called_once()
            call_kwargs = mock_client.post.call_args
            payload = call_kwargs[1]["json"]
            assert payload["parse_mode"] == "Markdown"
            assert "chest pain" in payload["text"] or "Anjali" in payload["text"]

    def test_http_error_raises_alert_tool_error(self) -> None:
        mock_response = MagicMock()
        mock_response.status_code = 401

        http_error = httpx.HTTPStatusError(
            "Unauthorized", request=MagicMock(), response=mock_response
        )

        with patch("app.tools.alerts.httpx.Client") as mock_client_cls:
            mock_client = MagicMock()
            mock_client.__enter__ = MagicMock(return_value=mock_client)
            mock_client.__exit__ = MagicMock(return_value=False)
            mock_client.post.side_effect = http_error
            mock_client_cls.return_value = mock_client

            tool = HITLAlertTool()
            with pytest.raises(AlertToolError, match="HTTP 401"):
                tool.send_alert(
                    patient_id="P001",
                    patient_name="Test",
                    message="emergency",
                    session_id="tg:1",
                    severity="emergency",
                )

    def test_network_error_raises_alert_tool_error(self) -> None:
        request_error = httpx.RequestError("Connection refused", request=MagicMock())

        with patch("app.tools.alerts.httpx.Client") as mock_client_cls:
            mock_client = MagicMock()
            mock_client.__enter__ = MagicMock(return_value=mock_client)
            mock_client.__exit__ = MagicMock(return_value=False)
            mock_client.post.side_effect = request_error
            mock_client_cls.return_value = mock_client

            tool = HITLAlertTool()
            with pytest.raises(AlertToolError):
                tool.send_alert(
                    patient_id="P001",
                    patient_name="Test",
                    message="emergency",
                    session_id="tg:1",
                    severity="emergency",
                )
