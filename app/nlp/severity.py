"""
app/nlp/severity.py
===================
Severity scoring from BioBERT-extracted medical entities.

Why this file exists
--------------------
Keyword-based severity detection (``app/constants.py``) catches obvious
phrases like "chest pain" or "heart attack". But patients often describe
symptoms in ways that keywords miss:

- "बुखार है बहुत तेज" (Hindi — high fever)
- "my heart is racing and I feel dizzy"

BioBERT extracts these as ``Sign_symptom`` entities regardless of phrasing.
``SeverityScorer`` checks whether any extracted entity text matches known
high-severity patterns, and returns the highest severity level found.

Integration
-----------
``SeverityScorer`` is called from ``router_node`` in ``app/graph/nodes.py``
after ``BioBERTExtractor.extract()`` runs. Its result is merged with the
keyword-based result using ``_max_severity()``, so the strictest
classification always wins.

Extending
---------
Add new entity texts to ``_EMERGENCY_ENTITY_TEXTS`` or ``_URGENT_ENTITY_TEXTS``
to teach Healio about additional high-severity symptom descriptions.

Usage
-----
    from app.nlp.severity import SeverityScorer
    from app.nlp.biobert import get_biobert_extractor, MedicalEntity

    scorer = SeverityScorer()
    entities = [MedicalEntity("chest pain", "Sign_symptom", 0.97)]
    severity = scorer.score_from_entities(entities)
    # SeverityLevel.EMERGENCY
"""

from app.constants import SeverityLevel
from app.logging_config import get_logger
from app.nlp.biobert import MedicalEntity

log = get_logger(__name__)

# Minimum BioBERT confidence score to act on an entity.
# Entities below this threshold are noisy and ignored.
_MIN_CONFIDENCE: float = 0.75

# ── Entity text lookup sets ────────────────────────────────────────────────────
# These supplement the keyword lists in constants.py.
# Keys are lowercase; comparison is case-insensitive.

_EMERGENCY_ENTITY_TEXTS: frozenset[str] = frozenset({
    # Cardiac
    "chest pain",
    "heart attack",
    "cardiac arrest",
    "myocardial infarction",
    "chest tightness",
    # Neurological
    "stroke",
    "seizure",
    "unconscious",
    "loss of consciousness",
    "unresponsive",
    "paralysis",
    # Respiratory
    "can't breathe",
    "cannot breathe",
    "shortness of breath",
    "respiratory failure",
    "choking",
    # Other life-threatening
    "overdose",
    "severe bleeding",
    "haemorrhage",
    "hemorrhage",
    "anaphylaxis",
    "anaphylactic shock",
    "fainted",
    "fainting",
})

_URGENT_ENTITY_TEXTS: frozenset[str] = frozenset({
    # Fever / infection
    "high fever",
    "severe fever",
    "persistent fever",
    # Pain
    "severe pain",
    "acute pain",
    "unbearable pain",
    # Cardiovascular
    "high blood pressure",
    "hypertensive crisis",
    "palpitations",
    # Gastrointestinal
    "vomiting blood",
    "blood in stool",
    "severe abdominal pain",
    # Allergy
    "allergic reaction",
    "rash spreading",
    # Respiratory
    "difficulty breathing",
    "wheezing",
    # Neurological
    "severe headache",
    "sudden headache",
    "vision loss",
})


class SeverityScorer:
    """Classify severity from a list of BioBERT-extracted MedicalEntity objects.

    Scans the entity text of each high-confidence entity against pre-defined
    emergency and urgent term sets. Returns the worst-case severity found.

    This class is stateless — instantiate once and call ``score_from_entities``
    repeatedly.

    Usage:
        scorer = SeverityScorer()
        severity = scorer.score_from_entities(entities)
    """

    def score_from_entities(self, entities: list[MedicalEntity]) -> SeverityLevel:
        """Determine the severity level based on extracted medical entities.

        Only considers entities whose confidence score exceeds
        ``_MIN_CONFIDENCE`` (0.75). Returns ``ROUTINE`` when no entities
        match or the entity list is empty.

        Priority: EMERGENCY > URGENT > ROUTINE.
        Short-circuits as soon as an EMERGENCY entity is found.

        Args:
            entities: List of ``MedicalEntity`` objects from ``BioBERTExtractor``.

        Returns:
            The highest ``SeverityLevel`` found among the entities.

        Example:
            >>> scorer = SeverityScorer()
            >>> scorer.score_from_entities([
            ...     MedicalEntity("chest pain", "Sign_symptom", 0.97),
            ... ])
            <SeverityLevel.EMERGENCY: 'emergency'>

            >>> scorer.score_from_entities([])
            <SeverityLevel.ROUTINE: 'routine'>
        """
        if not entities:
            return SeverityLevel.ROUTINE

        current_severity = SeverityLevel.ROUTINE

        for entity in entities:
            # Skip low-confidence entities — they produce noisy false positives
            if entity.score < _MIN_CONFIDENCE:
                continue

            entity_text_lower = entity.text.lower()

            if entity_text_lower in _EMERGENCY_ENTITY_TEXTS:
                log.info(
                    "severity_scorer_emergency_entity",
                    entity=entity.text,
                    label=entity.label,
                    score=round(entity.score, 3),
                )
                # Short-circuit: can't get worse than EMERGENCY
                return SeverityLevel.EMERGENCY

            if entity_text_lower in _URGENT_ENTITY_TEXTS:
                log.info(
                    "severity_scorer_urgent_entity",
                    entity=entity.text,
                    label=entity.label,
                    score=round(entity.score, 3),
                )
                current_severity = SeverityLevel.URGENT

        return current_severity
