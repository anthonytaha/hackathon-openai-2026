"""Streamlit operator dashboard for the MP3 processing workflow."""

from __future__ import annotations

import logging
import os
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import streamlit as st
from dotenv import load_dotenv

from app.models.processing_result import JobState
from app.services.pipeline_service import process_audio
from app.services.sms_client import SmsError, send_processing_complete_sms
from app.services.supabase_storage import (
    StorageObject,
    SupabaseSettings,
    create_supabase_client,
    download_object,
    list_mp3_objects,
)
from app.services.workflow_service import upload_processing_artifacts


LOGGER = logging.getLogger(__name__)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _friendly_size(size: int | None) -> str:
    if size is None:
        return "Unknown"
    value = float(size)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            return f"{value:.0f} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024
    return f"{size} B"


@st.cache_resource(show_spinner=False)
def _get_client(url: str, key: str) -> Any:
    return create_supabase_client(url, key)


def _init_session() -> None:
    defaults: dict[str, Any] = {
        "recordings": None,
        "recordings_error": None,
        "preview_path": None,
        "preview_audio": None,
        "preview_error": None,
        "active_job": None,
        "job_history": [],
        "job_workspace": None,
        "side_effect_in_progress": False,
    }
    for key, value in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = value


def _load_recordings(client: Any, settings: SupabaseSettings) -> None:
    try:
        st.session_state.recordings = list_mp3_objects(
            client, settings.bucket, settings.input_prefix
        )
        st.session_state.recordings_error = None
    except Exception as exc:
        LOGGER.exception("Supabase recording listing failed")
        st.session_state.recordings = []
        st.session_state.recordings_error = str(exc)


def _load_preview(client: Any, settings: SupabaseSettings, object_path: str) -> None:
    if st.session_state.preview_path == object_path and st.session_state.preview_audio:
        return
    st.session_state.preview_path = object_path
    st.session_state.preview_audio = None
    st.session_state.preview_error = None
    try:
        st.session_state.preview_audio = download_object(
            client, settings.bucket, object_path
        )
    except Exception as exc:
        LOGGER.exception("Selected recording preview download failed")
        st.session_state.preview_error = str(exc)


def _cleanup_workspace() -> None:
    workspace = st.session_state.get("job_workspace")
    if workspace is not None:
        try:
            workspace.cleanup()
        except Exception:
            LOGGER.exception("Could not clean a temporary job workspace")
    st.session_state.job_workspace = None


def _sms_configuration() -> tuple[str, str, str]:
    return (
        os.getenv("SMS_API_URL", "").strip(),
        os.getenv("SMS_API_KEY", "").strip(),
        os.getenv("SMS_RECIPIENT", "").strip(),
    )


def _notify(job: JobState) -> None:
    api_url, api_key, recipient = _sms_configuration()
    if not all((api_url, api_key, recipient)):
        missing = [
            name
            for name, value in (
                ("SMS_API_URL", api_url),
                ("SMS_API_KEY", api_key),
                ("SMS_RECIPIENT", recipient),
            )
            if not value
        ]
        job.sms_status = "skipped"
        job.sms_detail = f"Not configured: {', '.join(missing)}"
        return

    job.status = "notifying"
    try:
        result = send_processing_complete_sms(
            recipient=recipient,
            source_object_path=job.source_object_path,
            output_object_path=job.uploaded_output_path or "",
            job_id=job.job_id,
            api_url=api_url,
            api_key=api_key,
        )
        job.sms_status = "successful"
        job.sms_detail = f"SMS API returned HTTP {result.status_code}."
    except SmsError as exc:
        LOGGER.warning("SMS notification failed for job %s: %s", job.job_id, exc)
        job.sms_status = "failed"
        job.sms_detail = str(exc)
        job.failed_stage = "sms"


def _start_job(
    *,
    client: Any | None,
    settings: SupabaseSettings | None,
    source_object_path: str,
    local_audio: bytes | None = None,
) -> None:
    """Perform side effects once, exclusively from the Process button callback path."""

    if st.session_state.side_effect_in_progress:
        return
    st.session_state.side_effect_in_progress = True
    _cleanup_workspace()
    workspace = tempfile.TemporaryDirectory(prefix="mp3-processing-")
    st.session_state.job_workspace = workspace
    work_dir = Path(workspace.name)
    job = JobState(
        job_id=str(uuid.uuid4()),
        source_object_path=source_object_path,
        status="downloading",
        started_at=_utcnow(),
        sms_status="pending",
    )
    st.session_state.active_job = job
    st.session_state.job_history.append(job)

    try:
        with st.status("Processing recording…", expanded=True) as status:
            try:
                if local_audio is None:
                    if client is None or settings is None:
                        raise RuntimeError("Supabase is not configured")
                    st.write("Downloading audio from Supabase")
                    audio_bytes = download_object(client, settings.bucket, source_object_path)
                else:
                    st.write("Reading uploaded audio")
                    audio_bytes = local_audio
                suffix = Path(source_object_path).suffix.lower()
                input_path = work_dir / f"input{suffix if suffix in {'.mp3', '.wav'} else '.mp3'}"
                input_path.write_bytes(audio_bytes)
                job.original_audio = audio_bytes
            except Exception as exc:
                job.status = "failed"
                job.failed_stage = "download"
                job.error = str(exc)
                status.update(label="Download failed", state="error")
                LOGGER.exception("Job %s download failed", job.job_id)
                return

            try:
                job.status = "processing"
                st.write("Running Whisper and privacy-filter pipeline")
                result = process_audio(input_path, work_dir / "output")
                st.write("Generating artifacts")
                for expected in [result.output_audio_path, *result.artifacts]:
                    if not expected.is_file():
                        raise FileNotFoundError(f"Expected artifact was not created: {expected.name}")
                job.processing_result = result
                job.local_output_path = result.output_audio_path
                job.processed_audio = result.output_audio_path.read_bytes()
                job.transcript = result.transcript
                job.pipeline_metadata = result.metadata
                job.warnings = result.warnings
            except Exception as exc:
                job.status = "failed"
                job.failed_stage = "pipeline"
                job.error = str(exc)
                status.update(label="Pipeline failed", state="error")
                LOGGER.exception("Job %s pipeline failed", job.job_id)
                return

            if client is not None and settings is not None:
                try:
                    job.status = "uploading"
                    st.write("Uploading processed files")
                    job.uploaded_artifacts = upload_processing_artifacts(
                        client=client,
                        bucket=settings.bucket,
                        source_object_path=source_object_path,
                        output_prefix=settings.output_prefix,
                        job_id=job.job_id,
                        result=result,
                    )
                    job.uploaded_output_path = job.uploaded_artifacts["processed.mp3"]
                except Exception as exc:
                    job.status = "failed"
                    job.failed_stage = "upload"
                    job.error = str(exc)
                    status.update(label="Upload failed", state="error")
                    LOGGER.exception("Job %s upload failed", job.job_id)
                    return

                st.write("Calling SMS API")
                _notify(job)
            else:
                job.sms_status = "skipped"
                job.sms_detail = "Supabase is not configured; results are available for local download."
            job.status = "completed"
            job.completed_at = _utcnow()
            job.error = None
            status.update(label="Processing complete", state="complete")
    finally:
        st.session_state.side_effect_in_progress = False


def _retry_upload(client: Any, settings: SupabaseSettings, job: JobState) -> None:
    """Retry upload only; never rerun the pipeline."""

    if st.session_state.side_effect_in_progress or job.processing_result is None:
        return
    st.session_state.side_effect_in_progress = True
    try:
        with st.status("Retrying upload…", expanded=True) as status:
            job.status = "uploading"
            job.error = None
            st.write("Uploading processed files")
            try:
                job.uploaded_artifacts = upload_processing_artifacts(
                    client=client,
                    bucket=settings.bucket,
                    source_object_path=job.source_object_path,
                    output_prefix=settings.output_prefix,
                    job_id=job.job_id,
                    result=job.processing_result,
                )
                job.uploaded_output_path = job.uploaded_artifacts["processed.mp3"]
            except Exception as exc:
                job.status = "failed"
                job.failed_stage = "upload"
                job.error = str(exc)
                status.update(label="Upload retry failed", state="error")
                LOGGER.exception("Job %s upload retry failed", job.job_id)
                return
            st.write("Calling SMS API")
            job.failed_stage = None
            _notify(job)
            job.status = "completed"
            job.completed_at = _utcnow()
            status.update(label="Upload retry complete", state="complete")
    finally:
        st.session_state.side_effect_in_progress = False


def _retry_sms(job: JobState) -> None:
    """Retry notification only; never duplicate processing or uploads."""

    if st.session_state.side_effect_in_progress or not job.uploaded_output_path:
        return
    st.session_state.side_effect_in_progress = True
    try:
        job.sms_status = "pending"
        job.sms_detail = None
        job.failed_stage = None
        _notify(job)
        job.status = "completed"
        job.completed_at = _utcnow()
    finally:
        st.session_state.side_effect_in_progress = False


def _render_job(job: JobState) -> None:
    st.subheader("Results")
    processing_ok = job.processing_result is not None
    upload_ok = bool(job.uploaded_output_path)
    summary_cols = st.columns(3)
    summary_cols[0].metric("Processing", "Successful" if processing_ok else "Failed")
    summary_cols[1].metric("Upload", "Successful" if upload_ok else "Failed")
    summary_cols[2].metric("SMS", job.sms_status.title())

    st.caption(f"Job ID: {job.job_id} · Status: {job.status.title()}")
    if job.error:
        st.error(job.error)
    if job.sms_detail and job.sms_status in {"failed", "skipped"}:
        st.warning(f"SMS: {job.sms_detail}")
    elif job.sms_detail:
        st.success(job.sms_detail)

    original, processed = st.columns(2)
    with original:
        st.markdown("#### Original")
        st.write(f"**Source filename:** `{Path(job.source_object_path).name}`")
        st.write(f"**Source path:** `{job.source_object_path}`")
        if job.original_audio:
            st.audio(job.original_audio, format="audio/mpeg")

    with processed:
        st.markdown("#### Processed")
        pipeline_name = str(job.pipeline_metadata.get("pipeline", "Unknown"))
        st.info(f"Pipeline: {pipeline_name.replace('_', ' ').title()}")
        st.write("**Output filename:** `processed.mp3`")
        if job.uploaded_output_path:
            st.write(f"**Output storage path:** `{job.uploaded_output_path}`")
        if job.processed_audio:
            st.audio(job.processed_audio, format="audio/mpeg")
            st.download_button(
                "Download processed.mp3",
                data=job.processed_audio,
                file_name="processed.mp3",
                mime="audio/mpeg",
                key=f"download-audio-{job.job_id}",
            )
        if job.transcript:
            st.markdown("##### Transcript")
            st.text_area(
                "Complete transcript",
                value=job.transcript,
                height=190,
                disabled=True,
                key=f"transcript-{job.job_id}",
            )
            st.markdown("##### Pipeline metadata")
            st.json(job.pipeline_metadata)
        if job.warnings:
            for warning in job.warnings:
                st.warning(warning)

    if job.uploaded_artifacts:
        st.markdown("#### Uploaded artifacts")
        for name, path in job.uploaded_artifacts.items():
            st.code(f"{name}: {path}")

    if job.processing_result:
        st.markdown("#### Local artifacts")
        for artifact in job.processing_result.artifacts:
            if artifact.is_file():
                mime = "application/json" if artifact.suffix == ".json" else "text/plain"
                st.download_button(
                    f"Download {artifact.name}",
                    data=artifact.read_bytes(),
                    file_name=artifact.name,
                    mime=mime,
                    key=f"download-{job.job_id}-{artifact.name}",
                )


def main() -> None:
    load_dotenv()
    logging.basicConfig(level=logging.INFO)
    st.set_page_config(page_title="MP3 Transcription Workflow", page_icon="🎙️", layout="wide")
    _init_session()

    st.title("MP3 Transcription Workflow")
    st.caption(
        "Supabase Storage → privacy-aware transcription pipeline → uploads → SMS"
    )
    st.info(
        "The processing pipeline uses Whisper for transcription, openai/privacy-filter "
        "for private-data detection, and FFmpeg to redact matching audio spans."
    )

    settings: SupabaseSettings | None = None
    client: Any | None = None
    supabase_error: str | None = None
    try:
        settings = SupabaseSettings.from_env()
        client = _get_client(settings.url, settings.key)
    except Exception as exc:
        supabase_error = str(exc)

    source_tab, supabase_tab = st.tabs(["Local upload", "Supabase recordings"])
    with source_tab:
        st.header("1. Upload an audio file")
        uploaded_file = st.file_uploader("MP3 or WAV file", type=["mp3", "wav"])
        if uploaded_file is not None:
            uploaded_audio = uploaded_file.getvalue()
            st.write(f"**Selected file:** `{uploaded_file.name}` · {_friendly_size(len(uploaded_audio))}")
            st.audio(uploaded_audio, format="audio/wav" if uploaded_file.name.lower().endswith(".wav") else "audio/mpeg")
            if st.button(
                "Process uploaded file",
                type="primary",
                disabled=st.session_state.side_effect_in_progress,
                help="Starts one new job. Rerenders do not repeat it.",
            ):
                _start_job(
                    client=client,
                    settings=settings,
                    source_object_path=f"local-upload/{Path(uploaded_file.name).name}",
                    local_audio=uploaded_audio,
                )

    with supabase_tab:
        st.header("1. Supabase recordings")
        if supabase_error:
            st.info("Configure Supabase to browse recordings and upload processed artifacts.")
            st.code(supabase_error)
        elif settings is not None and client is not None:
            refresh_col, filter_col = st.columns([1, 4])
            with refresh_col:
                refresh = st.button("Refresh", use_container_width=True)
            with filter_col:
                query = st.text_input("Filter by filename or path", placeholder="meeting.mp3")

            if st.session_state.recordings is None or refresh:
                with st.spinner("Fetching MP3 objects…"):
                    _load_recordings(client, settings)
            if st.session_state.recordings_error:
                st.error(st.session_state.recordings_error)

            recordings: list[StorageObject] = st.session_state.recordings or []
            normalized_query = query.casefold().strip()
            filtered = [
                item
                for item in recordings
                if not normalized_query
                or normalized_query in item.name.casefold()
                or normalized_query in item.path.casefold()
            ]
            if not recordings:
                st.info("No MP3 files were found in the configured bucket and input prefix.")
            elif not filtered:
                st.info("No MP3 files match the current filter.")
            else:
                st.dataframe(
                    [
                        {
                            "Filename": item.name,
                            "Storage object path": item.path,
                            "File size": _friendly_size(item.size),
                            "Modified": item.modified_at or "Unknown",
                        }
                        for item in filtered
                    ],
                    use_container_width=True,
                    hide_index=True,
                )

                object_by_path = {item.path: item for item in filtered}
                selected_path = st.selectbox(
                    "Select one recording",
                    options=list(object_by_path),
                    format_func=lambda path: f"{object_by_path[path].name} — {path}",
                )
                selected = object_by_path[selected_path]

                st.header("2. Selected recording")
                st.write(f"Object path: `{selected.path}`")
                with st.spinner("Loading audio preview…"):
                    _load_preview(client, settings, selected.path)
                if st.session_state.preview_error:
                    st.error(st.session_state.preview_error)
                elif st.session_state.preview_audio:
                    st.audio(st.session_state.preview_audio, format="audio/mpeg")

                process_disabled = bool(
                    st.session_state.side_effect_in_progress or not st.session_state.preview_audio
                )
                if st.button(
                    "Process Recording",
                    type="primary",
                    disabled=process_disabled,
                    help="Starts one new job. Rerenders do not repeat it.",
                ):
                    _start_job(client=client, settings=settings, source_object_path=selected.path)

    job: JobState | None = st.session_state.active_job
    if job is not None:
        if job.failed_stage == "upload" and job.processing_result is not None and client is not None and settings is not None:
            if st.button("Retry upload and notification", key=f"retry-upload-{job.job_id}"):
                _retry_upload(client, settings, job)
        if job.sms_status == "failed":
            if st.button("Retry SMS only", key=f"retry-sms-{job.job_id}"):
                _retry_sms(job)
        _render_job(job)

        if st.button("Clear current job and temporary files"):
            _cleanup_workspace()
            st.session_state.active_job = None
            st.rerun()


if __name__ == "__main__":
    main()
