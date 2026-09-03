"""Adapter between the dashboard and the audio privacy pipeline."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from app.models.processing_result import ProcessingResult


_PIPELINE: Any | None = None


def _get_pipeline() -> Any:
    """Load the large ML dependencies and models only when processing starts."""

    global _PIPELINE
    if _PIPELINE is None:
        from privacy_pipeline import AudioPrivacyPipeline

        _PIPELINE = AudioPrivacyPipeline()
    return _PIPELINE


def process_audio(input_path: Path, output_dir: Path) -> ProcessingResult:
    """Transcribe, detect private data, and redact the matching audio spans."""

    input_path = Path(input_path)
    output_dir = Path(output_dir)
    if not input_path.is_file():
        raise FileNotFoundError(f"Input audio does not exist: {input_path}")
    if input_path.suffix.lower() != ".mp3":
        raise ValueError("The pipeline accepts MP3 input files only.")

    output_dir.mkdir(parents=True, exist_ok=True)
    output_audio_path = output_dir / "processed.mp3"
    transcript_txt_path = output_dir / "transcript.txt"
    transcript_json_path = output_dir / "transcript.json"
    action = os.getenv("PRIVACY_REDACTION_ACTION", "beep").strip().lower()
    if action not in {"beep", "mute"}:
        raise ValueError("PRIVACY_REDACTION_ACTION must be 'beep' or 'mute'.")

    pipeline = _get_pipeline()
    sanitized = pipeline.process_file(input_path, output_audio_path, action=action)
    entities = [entity.model_dump() for entity in sanitized.entities]
    redaction_ranges = [span.model_dump() for span in sanitized.audio_spans]
    metadata = {
        "pipeline": "whisper_privacy_filter",
        "whisper_model": pipeline.whisper_model,
        "privacy_model": "openai/privacy-filter",
        "redaction_action": action,
        "detected_entity_count": len(entities),
        "redaction_range_count": len(redaction_ranges),
    }
    transcript_txt_path.write_text(sanitized.transcript + "\n", encoding="utf-8")
    transcript_json_path.write_text(
        json.dumps(
            {
                "pipeline": "whisper_privacy_filter",
                "transcript": sanitized.transcript,
                "redacted_transcript": sanitized.redacted_transcript,
                "metadata": metadata,
                "entities": entities,
                "redaction_ranges": redaction_ranges,
            },
            indent=2,
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )

    return ProcessingResult(
        transcript=sanitized.redacted_transcript,
        input_audio_path=input_path,
        output_audio_path=output_audio_path,
        metadata=metadata,
        artifacts=[transcript_txt_path, transcript_json_path],
        warnings=[],
    )
