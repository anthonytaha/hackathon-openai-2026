"""Data exchanged between the pipeline, workflow, and dashboard."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Literal


@dataclass(slots=True)
class ProcessingResult:
    """The stable contract implemented by any audio processing pipeline."""

    transcript: str
    input_audio_path: Path
    output_audio_path: Path
    metadata: dict[str, Any] = field(default_factory=dict)
    artifacts: list[Path] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


JobStatus = Literal[
    "selected",
    "downloading",
    "processing",
    "uploading",
    "notifying",
    "completed",
    "failed",
]


@dataclass
class JobState:
    """State for one explicit processing attempt.

    This currently lives in Streamlit session state. Its serializable fields also
    map cleanly to a future ``processing_jobs`` database table.
    """

    job_id: str
    source_object_path: str
    status: JobStatus = "selected"
    started_at: datetime | None = None
    completed_at: datetime | None = None
    local_output_path: Path | None = None
    uploaded_output_path: str | None = None
    uploaded_artifacts: dict[str, str] = field(default_factory=dict)
    transcript: str = ""
    pipeline_metadata: dict[str, Any] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    sms_status: str = "pending"
    sms_detail: str | None = None
    error: str | None = None
    failed_stage: str | None = None
    original_audio: bytes | None = field(default=None, repr=False)
    processed_audio: bytes | None = field(default=None, repr=False)
    processing_result: ProcessingResult | None = field(default=None, repr=False)
