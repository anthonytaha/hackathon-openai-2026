from pathlib import Path

import pytest

from app.models.processing_result import ProcessingResult
from app.services.workflow_service import upload_and_notify, upload_processing_artifacts


def _result(tmp_path: Path) -> ProcessingResult:
    input_path = tmp_path / "input.mp3"
    output_path = tmp_path / "processed.mp3"
    txt_path = tmp_path / "transcript.txt"
    json_path = tmp_path / "transcript.json"
    for path in (input_path, output_path, txt_path, json_path):
        path.write_bytes(b"content")
    return ProcessingResult(
        transcript="demo",
        input_audio_path=input_path,
        output_audio_path=output_path,
        metadata={"pipeline": "placeholder"},
        artifacts=[txt_path, json_path],
    )


def test_uploads_use_generated_paths(tmp_path: Path) -> None:
    uploaded = []

    def uploader(client, bucket, object_path, local_path):
        uploaded.append((bucket, object_path, local_path.name))
        return object_path

    paths = upload_processing_artifacts(
        client=object(),
        bucket="audio",
        source_object_path="incoming/call.mp3",
        output_prefix="processed",
        job_id="job-1",
        result=_result(tmp_path),
        uploader=uploader,
    )

    assert list(paths) == ["processed.mp3", "transcript.txt", "transcript.json"]
    assert uploaded[0] == (
        "audio",
        "processed/call/job-1/processed.mp3",
        "processed.mp3",
    )


def test_sms_is_not_called_when_upload_fails(tmp_path: Path) -> None:
    sms_calls = []

    def failing_uploader(client, bucket, object_path, local_path):
        raise RuntimeError("bucket unavailable")

    def sms_sender(**kwargs):
        sms_calls.append(kwargs)
        raise AssertionError("SMS must not be called")

    with pytest.raises(RuntimeError, match="bucket unavailable"):
        upload_and_notify(
            client=object(),
            bucket="audio",
            source_object_path="incoming/call.mp3",
            output_prefix="processed",
            job_id="job-1",
            result=_result(tmp_path),
            recipient="+33123456789",
            sms_api_url="https://sms.example.test/send",
            sms_api_key="key",
            uploader=failing_uploader,
            sms_sender=sms_sender,
        )

    assert sms_calls == []
