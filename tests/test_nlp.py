"""
tests/test_nlp.py
=================
Unit tests for the NLP components: BioBERTExtractor and SeverityScorer.

BioBERT tests run with DISABLE_BIOBERT=true (set in conftest.py) so no
model is downloaded in CI. All tests validate behaviour in the disabled
state and with mocked entities.
"""

import pytest

from app.constants import SeverityLevel
from app.nlp.biobert import BioBERTExtractor, MedicalEntity, get_biobert_extractor
from app.nlp.severity import SeverityScorer


# ── MedicalEntity ──────────────────────────────────────────────────────────────

class TestMedicalEntity:
    """Tests for the MedicalEntity dataclass."""

    def test_fields_stored_correctly(self) -> None:
        entity = MedicalEntity(text="chest pain", label="Sign_symptom", score=0.97)
        assert entity.text == "chest pain"
        assert entity.label == "Sign_symptom"
        assert entity.score == 0.97

    def test_to_dict_returns_correct_keys(self) -> None:
        entity = MedicalEntity(text="fever", label="Sign_symptom", score=0.85)
        d = entity.to_dict()
        assert d == {"text": "fever", "label": "Sign_symptom", "score": 0.85}

    def test_to_dict_is_a_new_dict(self) -> None:
        entity = MedicalEntity(text="diabetes", label="Disease_disorder", score=0.90)
        d = entity.to_dict()
        # Modifying the returned dict should not affect the entity
        d["text"] = "mutated"
        assert entity.text == "diabetes"


# ── BioBERTExtractor (DISABLE_BIOBERT=true) ────────────────────────────────────

class TestBioBERTExtractorDisabled:
    """Tests for BioBERTExtractor when DISABLE_BIOBERT=true (CI mode).

    The conftest.py fixture sets DISABLE_BIOBERT=true before module import,
    so the model is never loaded. These tests verify graceful degradation.
    """

    def test_is_available_is_false_when_disabled(self) -> None:
        extractor = BioBERTExtractor()
        assert extractor.is_available is False

    def test_extract_returns_empty_list_when_disabled(self) -> None:
        extractor = BioBERTExtractor()
        result = extractor.extract("I have chest pain and high fever")
        assert result == []

    def test_extract_empty_string_returns_empty(self) -> None:
        extractor = BioBERTExtractor()
        result = extractor.extract("")
        assert result == []

    def test_extract_long_text_returns_empty_when_disabled(self) -> None:
        extractor = BioBERTExtractor()
        long_text = "headache " * 100
        result = extractor.extract(long_text)
        assert result == []

    def test_singleton_is_disabled(self) -> None:
        extractor = get_biobert_extractor()
        assert extractor.is_available is False


# ── SeverityScorer ─────────────────────────────────────────────────────────────

class TestSeverityScorer:
    """Tests for SeverityScorer.score_from_entities()."""

    def setup_method(self) -> None:
        self.scorer = SeverityScorer()

    # ── Empty / no entities ────────────────────────────────────────────────────

    def test_empty_entities_returns_routine(self) -> None:
        result = self.scorer.score_from_entities([])
        assert result == SeverityLevel.ROUTINE

    # ── Emergency entity detection ─────────────────────────────────────────────

    def test_chest_pain_entity_returns_emergency(self) -> None:
        entities = [MedicalEntity("chest pain", "Sign_symptom", 0.97)]
        result = self.scorer.score_from_entities(entities)
        assert result == SeverityLevel.EMERGENCY

    def test_stroke_entity_returns_emergency(self) -> None:
        entities = [MedicalEntity("stroke", "Disease_disorder", 0.92)]
        result = self.scorer.score_from_entities(entities)
        assert result == SeverityLevel.EMERGENCY

    def test_shortness_of_breath_returns_emergency(self) -> None:
        entities = [MedicalEntity("shortness of breath", "Sign_symptom", 0.88)]
        result = self.scorer.score_from_entities(entities)
        assert result == SeverityLevel.EMERGENCY

    def test_overdose_returns_emergency(self) -> None:
        entities = [MedicalEntity("overdose", "Sign_symptom", 0.91)]
        result = self.scorer.score_from_entities(entities)
        assert result == SeverityLevel.EMERGENCY

    def test_seizure_returns_emergency(self) -> None:
        entities = [MedicalEntity("seizure", "Disease_disorder", 0.90)]
        result = self.scorer.score_from_entities(entities)
        assert result == SeverityLevel.EMERGENCY

    # ── Urgent entity detection ────────────────────────────────────────────────

    def test_high_fever_entity_returns_urgent(self) -> None:
        entities = [MedicalEntity("high fever", "Sign_symptom", 0.85)]
        result = self.scorer.score_from_entities(entities)
        assert result == SeverityLevel.URGENT

    def test_severe_pain_entity_returns_urgent(self) -> None:
        entities = [MedicalEntity("severe pain", "Sign_symptom", 0.82)]
        result = self.scorer.score_from_entities(entities)
        assert result == SeverityLevel.URGENT

    def test_allergic_reaction_returns_urgent(self) -> None:
        entities = [MedicalEntity("allergic reaction", "Disease_disorder", 0.87)]
        result = self.scorer.score_from_entities(entities)
        assert result == SeverityLevel.URGENT

    # ── Routine / no match ────────────────────────────────────────────────────

    def test_routine_entity_returns_routine(self) -> None:
        entities = [MedicalEntity("headache", "Sign_symptom", 0.80)]
        result = self.scorer.score_from_entities(entities)
        assert result == SeverityLevel.ROUTINE

    def test_medication_entity_returns_routine(self) -> None:
        entities = [MedicalEntity("metformin", "Medication", 0.93)]
        result = self.scorer.score_from_entities(entities)
        assert result == SeverityLevel.ROUTINE

    # ── Confidence threshold ────────────────────────────────────────────────────

    def test_low_confidence_entity_is_ignored(self) -> None:
        # Score below _MIN_CONFIDENCE (0.75) — should return ROUTINE
        entities = [MedicalEntity("chest pain", "Sign_symptom", 0.60)]
        result = self.scorer.score_from_entities(entities)
        assert result == SeverityLevel.ROUTINE

    def test_exactly_min_confidence_is_ignored(self) -> None:
        # Score of exactly 0.75 is not above the threshold — ignored
        entities = [MedicalEntity("chest pain", "Sign_symptom", 0.74)]
        result = self.scorer.score_from_entities(entities)
        assert result == SeverityLevel.ROUTINE

    def test_above_min_confidence_is_used(self) -> None:
        entities = [MedicalEntity("chest pain", "Sign_symptom", 0.76)]
        result = self.scorer.score_from_entities(entities)
        assert result == SeverityLevel.EMERGENCY

    # ── Priority: emergency beats urgent ──────────────────────────────────────

    def test_mixed_entities_returns_highest_severity(self) -> None:
        entities = [
            MedicalEntity("high fever", "Sign_symptom", 0.85),   # urgent
            MedicalEntity("chest pain", "Sign_symptom", 0.97),   # emergency
        ]
        result = self.scorer.score_from_entities(entities)
        assert result == SeverityLevel.EMERGENCY

    def test_emergency_short_circuits_after_first_match(self) -> None:
        # Even if the first entity is emergency, rest should not matter
        entities = [
            MedicalEntity("seizure", "Disease_disorder", 0.90),  # emergency
            MedicalEntity("metformin", "Medication", 0.99),        # routine
        ]
        result = self.scorer.score_from_entities(entities)
        assert result == SeverityLevel.EMERGENCY

    # ── Case-insensitivity note ───────────────────────────────────────────────
    # BioBERT returns entities in original case, so the scorer lowercases
    # for comparison.

    def test_entity_text_is_lowercased_for_comparison(self) -> None:
        entities = [MedicalEntity("Chest Pain", "Sign_symptom", 0.97)]
        result = self.scorer.score_from_entities(entities)
        # "Chest Pain" lowercased → "chest pain" → EMERGENCY
        assert result == SeverityLevel.EMERGENCY
