"""Workflow operations shared by the UI and tests."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from app.models.processing_result import ProcessingResult
from app.services.sms_client import SmsResult, send_processing_complete_sms
from app.services.supabase_storage import generate_output_storage_paths, upload_object


UploadFunction = Callable[[Any, str, str, Path], str]
SmsFunction = Callable[..., SmsResult]


def upload_processing_artifacts(
    *,
    client: Any,
    bucket: str,
    source_object_path: str,
    output_prefix: str,
    job_id: str,
    result: ProcessingResult,
    uploader: UploadFunction = upload_object,
) -> dict[str, str]:
    """Upload the processed recording and transcript artifacts."""

    local_by_name = {result.output_audio_path.name: result.output_audio_path}
    local_by_name.update({path.name: path for path in result.artifacts})
    required = ("processed.mp3", "transcript.txt", "transcript.json")
    missing = [name for name in required if name not in local_by_name]
    if missing:
        raise FileNotFoundError(f"Pipeline did not create: {', '.join(missing)}")

    destinations = generate_output_storage_paths(
        source_object_path=source_object_path,
        job_id=job_id,
        output_prefix=output_prefix,
        artifact_names=required,
    )
    uploaded: dict[str, str] = {}
    for name in required:
        uploaded[name] = uploader(client, bucket, destinations[name], local_by_name[name])
    return uploaded


def upload_and_notify(
    *,
    client: Any,
    bucket: str,
    source_object_path: str,
    output_prefix: str,
    job_id: str,
    result: ProcessingResult,
    recipient: str,
    sms_api_url: str,
    sms_api_key: str,
    uploader: UploadFunction = upload_object,
    sms_sender: SmsFunction = send_processing_complete_sms,
) -> tuple[dict[str, str], SmsResult]:
    """Upload every artifact, then notify.

    The sequential boundary is deliberate: if any upload raises, control never
    reaches the SMS function.
    """

    uploaded = upload_processing_artifacts(
        client=client,
        bucket=bucket,
        source_object_path=source_object_path,
        output_prefix=output_prefix,
        job_id=job_id,
        result=result,
        uploader=uploader,
    )
    sms_result = sms_sender(
        recipient=recipient,
        source_object_path=source_object_path,
        output_object_path=uploaded["processed.mp3"],
        job_id=job_id,
        api_url=sms_api_url,
        api_key=sms_api_key,
    )
    return uploaded, sms_result
