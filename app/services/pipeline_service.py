"""Replaceable audio/transcription pipeline boundary."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from app.models.processing_result import ProcessingResult


PLACEHOLDER_TRANSCRIPT = """Placeholder transcription.

This recording has successfully passed through the demo transcription pipeline.

Replace app/services/pipeline_service.py with the real transcription implementation.
"""


def process_audio(input_path: Path, output_dir: Path) -> ProcessingResult:
    """Run the placeholder pipeline and return its implementation-neutral result.

    # TODO: Replace this placeholder implementation with the real
    # transcription and audio-processing pipeline.

    A future implementation should preserve this function signature and return a
    populated :class:`ProcessingResult`; the dashboard needs no other changes.
    """

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

    shutil.copyfile(input_path, output_audio_path)

    metadata = {
        "pipeline": "placeholder",
        "language": "unknown",
        "duration_seconds": None,
        "segment_count": 1,
    }
    transcript_txt_path.write_text(PLACEHOLDER_TRANSCRIPT, encoding="utf-8")
    transcript_json_path.write_text(
        json.dumps(
            {
                "pipeline": "placeholder",
                "transcript": PLACEHOLDER_TRANSCRIPT,
                "metadata": {
                    "language": "unknown",
                    "duration_seconds": None,
                    "segment_count": 1,
                },
            },
            indent=2,
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )

    return ProcessingResult(
        transcript=PLACEHOLDER_TRANSCRIPT,
        input_audio_path=input_path,
        output_audio_path=output_audio_path,
        metadata=metadata,
        artifacts=[transcript_txt_path, transcript_json_path],
        warnings=[
            "This is demo output from the placeholder pipeline, not a real transcription."
        ],
    )
