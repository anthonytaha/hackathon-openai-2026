import json
from pathlib import Path
from types import SimpleNamespace

from app.models.processing_result import ProcessingResult
from app.services import pipeline_service


class Dumpable:
    def __init__(self, **values) -> None:
        self.values = values

    def model_dump(self):
        return self.values


class FakePrivacyPipeline:
    whisper_model = "test-whisper"

    def process_file(self, input_path, output_path, *, action):
        assert action == "beep"
        output_path.write_bytes(b"sanitized mp3")
        return SimpleNamespace(
            transcript="Call Alice at 1234.",
            redacted_transcript="Call [private_person] at [private_phone].",
            entities=[
                Dumpable(
                    label="private_person",
                    text="Alice",
                    start=5,
                    end=10,
                    score=0.99,
                )
            ],
            audio_spans=[Dumpable(start=0.5, end=1.1)],
        )


def test_privacy_pipeline_adapter_creates_all_artifacts(tmp_path: Path, monkeypatch) -> None:
    input_path = tmp_path / "sample.mp3"
    original_bytes = b"ID3\x04\x00\x00demo audio bytes"
    input_path.write_bytes(original_bytes)
    monkeypatch.setattr(pipeline_service, "_PIPELINE", FakePrivacyPipeline())
    monkeypatch.setenv("PRIVACY_REDACTION_ACTION", "beep")

    result = pipeline_service.process_audio(input_path, tmp_path / "job-output")

    assert isinstance(result, ProcessingResult)
    assert result.input_audio_path == input_path
    assert result.output_audio_path.name == "processed.mp3"
    assert result.output_audio_path.read_bytes() == b"sanitized mp3"
    assert result.transcript == "Call [private_person] at [private_phone]."
    assert result.metadata == {
        "pipeline": "whisper_privacy_filter",
        "whisper_model": "test-whisper",
        "privacy_model": "openai/privacy-filter",
        "redaction_action": "beep",
        "detected_entity_count": 1,
        "redaction_range_count": 1,
    }

    artifacts = {path.name: path for path in result.artifacts}
    assert artifacts["transcript.txt"].read_text(encoding="utf-8") == "Call Alice at 1234.\n"
    transcript_json = json.loads(artifacts["transcript.json"].read_text(encoding="utf-8"))
    assert transcript_json["pipeline"] == "whisper_privacy_filter"
    assert transcript_json["redacted_transcript"] == result.transcript
    assert transcript_json["entities"][0]["label"] == "private_person"
    assert transcript_json["redaction_ranges"] == [{"start": 0.5, "end": 1.1}]
    assert result.warnings == []


def test_pipeline_rejects_non_mp3_input(tmp_path: Path) -> None:
    input_path = tmp_path / "sample.wav"
    input_path.write_bytes(b"audio")

    try:
        pipeline_service.process_audio(input_path, tmp_path / "output")
    except ValueError as exc:
        assert "MP3" in str(exc)
    else:  # pragma: no cover - assertion guard
        raise AssertionError("Expected a ValueError")
