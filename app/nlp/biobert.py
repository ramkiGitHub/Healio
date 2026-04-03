"""
app/nlp/biobert.py
==================
Medical Named Entity Recognition (NER) using a BioBERT-based HuggingFace model.

Why this file exists
--------------------
Standard NLP models struggle with clinical language — terms like "SOB"
(shortness of breath), "CAD" (coronary artery disease), or "Hb" (haemoglobin)
are opaque to general-purpose models. BioBERT models are pre-trained on
biomedical text (PubMed, clinical notes) and accurately identify entities such
as conditions, symptoms, medications, and body parts.

Extracted entities are used to:
1. Enhance severity scoring (see ``app/nlp/severity.py``).
2. Enrich patient context passed to the LLM in general_qa_node.

Model used: ``d4data/biomedical-ner-all`` (HuggingFace Hub)
Entity labels: Disease_disorder, Sign_symptom, Medication, Body_part,
               Biological_structure, Lab_value, Therapeutic_procedure, etc.

Performance note
----------------
BioBERT inference adds ~40–300ms per message depending on hardware.
Set ``DISABLE_BIOBERT=true`` in ``.env`` for faster startup in development.
The ``BioBERTExtractor`` class degrades gracefully when disabled — all
methods return empty results rather than raising errors.

Usage
-----
    from app.nlp.biobert import get_biobert_extractor

    extractor = get_biobert_extractor()
    if extractor.is_available:
        entities = extractor.extract("I have chest pain and shortness of breath")
        # [MedicalEntity(text='chest pain', label='Sign_symptom', score=0.97), ...]
"""

from dataclasses import dataclass
from functools import lru_cache

from app.config import settings
from app.exceptions import ModelNotLoadedError
from app.logging_config import get_logger

log = get_logger(__name__)


# ── Data model ─────────────────────────────────────────────────────────────────

@dataclass
class MedicalEntity:
    """A single named medical entity extracted from a patient message.

    Produced by ``BioBERTExtractor.extract()`` and consumed by
    ``SeverityScorer.score_from_entities()``.

    Attributes:
        text: The exact span of text identified as an entity (e.g., "chest pain").
        label: The entity type label from the BioBERT model.
               Common values: "Sign_symptom", "Disease_disorder", "Medication",
               "Body_part", "Lab_value", "Biological_structure".
        score: Model confidence score in range [0.0, 1.0]. Scores above 0.75
               are considered high-confidence.

    Example:
        >>> entity = MedicalEntity(text="chest pain", label="Sign_symptom", score=0.97)
        >>> entity.score > 0.75
        True
    """

    text: str
    label: str
    score: float

    def to_dict(self) -> dict:
        """Serialise the entity to a plain dict for LangGraph state storage.

        SQLite checkpointing requires all state values to be JSON-serialisable.
        LangChain messages are handled by LangGraph natively, but custom
        dataclasses must be converted manually.

        Returns:
            Dict with keys: ``text``, ``label``, ``score``.
        """
        return {"text": self.text, "label": self.label, "score": self.score}


# ── Extractor class ────────────────────────────────────────────────────────────

class BioBERTExtractor:
    """Medical NER extractor using the ``d4data/biomedical-ner-all`` HuggingFace model.

    Wraps a HuggingFace ``transformers.pipeline`` with ``aggregation_strategy="simple"``
    so sub-word tokens are merged into full entity spans
    (e.g., ``"chest"`` + ``" pain"`` → ``"chest pain"``).

    Controlled by the ``DISABLE_BIOBERT`` config flag. When disabled, all
    methods return empty results and no model is loaded into memory.

    Attributes:
        _pipeline: The HuggingFace NER pipeline, or None if disabled/failed.
        _loaded: True if the model was loaded successfully.

    Usage:
        extractor = get_biobert_extractor()
        if extractor.is_available:
            entities = extractor.extract("Patient reports severe headache")
    """

    def __init__(self) -> None:
        """Initialise the extractor and load the BioBERT model if enabled.

        Respects the ``DISABLE_BIOBERT`` config flag. If True, the model is
        skipped entirely — useful for development and CI environments where
        the 400MB model download is undesirable.

        Raises:
            ModelNotLoadedError: If the model fails to load and DISABLE_BIOBERT
                                 is False (i.e., loading was expected to succeed).
        """
        self._pipeline = None
        self._loaded = False

        if settings.disable_biobert:
            log.info("biobert_disabled", reason="DISABLE_BIOBERT=true in config")
            return

        try:
            # Import here to avoid loading transformers when BioBERT is disabled
            from transformers import pipeline as hf_pipeline  # type: ignore[import-untyped]

            log.info("biobert_loading", model=settings.biobert_model)
            self._pipeline = hf_pipeline(
                "ner",
                model=settings.biobert_model,
                aggregation_strategy="simple",
            )
            self._loaded = True
            log.info("biobert_model_loaded", model=settings.biobert_model)

        except Exception as exc:
            log.error(
                "biobert_load_failed",
                model=settings.biobert_model,
                error=str(exc),
            )
            raise ModelNotLoadedError(model_name=settings.biobert_model) from exc

    @property
    def is_available(self) -> bool:
        """True if the BioBERT model is loaded and available for inference.

        Returns:
            bool: True when the model loaded successfully, False otherwise.
        """
        return self._loaded

    def extract(self, text: str) -> list[MedicalEntity]:
        """Extract medical named entities from a patient message.

        Runs the BioBERT NER pipeline on the input text and returns a list
        of ``MedicalEntity`` objects for each identified span. Returns an
        empty list (without raising) if the model is disabled or unavailable.

        Args:
            text: The patient's message text. Should be a single message,
                  not the full conversation history.

        Returns:
            List of ``MedicalEntity`` objects sorted by position in the text.
            Returns ``[]`` if BioBERT is disabled or the pipeline is not loaded.

        Example:
            >>> extractor = get_biobert_extractor()
            >>> entities = extractor.extract("I have chest pain and high fever")
            >>> [(e.text, e.label) for e in entities]
            [('chest pain', 'Sign_symptom'), ('high fever', 'Sign_symptom')]
        """
        if not self._loaded or self._pipeline is None:
            return []

        try:
            raw_entities = self._pipeline(text)
            return [
                MedicalEntity(
                    text=str(ent["word"]).strip(),
                    label=str(ent["entity_group"]),
                    score=float(ent["score"]),
                )
                for ent in raw_entities
                if ent.get("word") and ent.get("entity_group")
            ]
        except Exception as exc:
            # Log and degrade gracefully — never crash a conversation for NLP
            log.warning(
                "biobert_inference_failed",
                error=str(exc),
                text_preview=text[:80],
            )
            return []


# ── Singleton factory ──────────────────────────────────────────────────────────

@lru_cache(maxsize=1)
def get_biobert_extractor() -> BioBERTExtractor:
    """Return the application-wide singleton ``BioBERTExtractor`` instance.

    Uses ``lru_cache`` so the model is downloaded and loaded only once at
    startup, not on every request.

    Returns:
        A configured ``BioBERTExtractor`` (may be in disabled state if
        ``DISABLE_BIOBERT=true``).

    Example:
        >>> extractor = get_biobert_extractor()
        >>> extractor.is_available
        False  # In dev with DISABLE_BIOBERT=true
    """
    return BioBERTExtractor()
