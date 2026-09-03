import json
from pathlib import Path

from app.models.processing_result import ProcessingResult
from app.services.pipeline_service import PLACEHOLDER_TRANSCRIPT, process_audio


def test_placeholder_pipeline_creates_all_artifacts(tmp_path: Path) -> None:
    input_path = tmp_path / "sample.mp3"
    original_bytes = b"ID3\x04\x00\x00demo audio bytes"
    input_path.write_bytes(original_bytes)

    result = process_audio(input_path, tmp_path / "job-output")

    assert isinstance(result, ProcessingResult)
    assert result.input_audio_path == input_path
    assert result.output_audio_path.name == "processed.mp3"
    assert result.output_audio_path.read_bytes() == original_bytes
    assert result.transcript == PLACEHOLDER_TRANSCRIPT
    assert result.metadata == {
        "pipeline": "placeholder",
        "language": "unknown",
        "duration_seconds": None,
        "segment_count": 1,
    }

    artifacts = {path.name: path for path in result.artifacts}
    assert artifacts["transcript.txt"].read_text(encoding="utf-8") == PLACEHOLDER_TRANSCRIPT
    transcript_json = json.loads(artifacts["transcript.json"].read_text(encoding="utf-8"))
    assert transcript_json["pipeline"] == "placeholder"
    assert transcript_json["transcript"] == PLACEHOLDER_TRANSCRIPT
    assert transcript_json["metadata"]["segment_count"] == 1
    assert result.warnings


def test_pipeline_rejects_non_mp3_input(tmp_path: Path) -> None:
    input_path = tmp_path / "sample.wav"
    input_path.write_bytes(b"audio")

    try:
        process_audio(input_path, tmp_path / "output")
    except ValueError as exc:
        assert "MP3" in str(exc)
    else:  # pragma: no cover - assertion guard
        raise AssertionError("Expected a ValueError")
