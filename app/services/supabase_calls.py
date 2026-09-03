"""Read call recording references from the public ``allo_calls`` table."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any, Mapping


class SupabaseCallsError(RuntimeError):
    """A human-readable call-table query or mapping failure."""


@dataclass(frozen=True, slots=True)
class CallRecording:
    call_id: str
    object_path: str
    topic: str
    recording_status: str
    recording_path: str | None = None
    webhook_id: str | None = None
    webhook_timestamp: str | None = None
    received_at: str | None = None

    @property
    def filename(self) -> str:
        return PurePosixPath(self.object_path).name


SELECT_COLUMNS = (
    "call_id,webhook_id,topic,webhook_timestamp,received_at,"
    "recording_path,recording_status"
)


def resolve_recording_object_path(
    *,
    call_id: str,
    recording_path: str | None,
    bucket: str,
    input_prefix: str = "",
) -> str:
    """Resolve a table row to an object path inside the configured bucket.

    ``recording_path`` wins when it names an MP3. Otherwise the agreed naming
    convention, ``<call_id>.mp3``, is used below ``SUPABASE_INPUT_PREFIX``.
    """

    normalized_call_id = call_id.strip()
    if (
        not normalized_call_id
        or normalized_call_id in {".", ".."}
        or "/" in normalized_call_id
        or "\\" in normalized_call_id
    ):
        raise ValueError("call_id cannot be used as a Storage filename")

    configured_path = (recording_path or "").strip().strip("/")
    if configured_path:
        bucket_prefix = f"{bucket.strip('/')}/"
        if configured_path.startswith(bucket_prefix):
            configured_path = configured_path[len(bucket_prefix) :]
        if not configured_path.lower().endswith(".mp3"):
            configured_path = f"{configured_path}.mp3"
        candidate = configured_path
    else:
        filename = (
            normalized_call_id
            if normalized_call_id.lower().endswith(".mp3")
            else f"{normalized_call_id}.mp3"
        )
        prefix = input_prefix.strip("/")
        candidate = f"{prefix}/{filename}" if prefix else filename

    parts = PurePosixPath(candidate).parts
    if not parts or any(part in {"", ".", ".."} for part in parts):
        raise ValueError("recording_path is not a safe Storage object path")
    return "/".join(parts)


def _response_data(response: Any) -> list[Mapping[str, Any]]:
    data = response.get("data") if isinstance(response, Mapping) else getattr(response, "data", None)
    if data is None:
        return []
    if not isinstance(data, list):
        raise SupabaseCallsError("Supabase returned an invalid allo_calls response.")
    return data


def list_call_recordings(
    client: Any,
    *,
    bucket: str,
    input_prefix: str = "",
    table: str = "allo_calls",
    page_size: int = 1000,
) -> list[CallRecording]:
    """Fetch lightweight call rows and map them to bucket MP3 object paths."""

    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", table):
        raise SupabaseCallsError("SUPABASE_CALLS_TABLE is not a valid table name.")
    if page_size < 1:
        raise ValueError("page_size must be positive")

    recordings: list[CallRecording] = []
    seen_call_ids: set[str] = set()
    offset = 0
    while True:
        try:
            response = (
                client.table(table)
                .select(SELECT_COLUMNS)
                .order("received_at", desc=True)
                .range(offset, offset + page_size - 1)
                .execute()
            )
        except Exception as exc:
            raise SupabaseCallsError(
                f"Could not read public.{table}: {exc}"
            ) from exc

        rows = _response_data(response)
        for row in rows:
            call_id = str(row.get("call_id") or "").strip()
            if not call_id or call_id in seen_call_ids:
                continue
            try:
                object_path = resolve_recording_object_path(
                    call_id=call_id,
                    recording_path=(
                        str(row["recording_path"])
                        if row.get("recording_path") is not None
                        else None
                    ),
                    bucket=bucket,
                    input_prefix=input_prefix,
                )
            except ValueError as exc:
                raise SupabaseCallsError(
                    f"Call '{call_id}' has an invalid recording reference: {exc}"
                ) from exc
            recordings.append(
                CallRecording(
                    call_id=call_id,
                    object_path=object_path,
                    topic=str(row.get("topic") or ""),
                    recording_status=str(row.get("recording_status") or "pending"),
                    recording_path=(
                        str(row["recording_path"])
                        if row.get("recording_path") is not None
                        else None
                    ),
                    webhook_id=(
                        str(row["webhook_id"])
                        if row.get("webhook_id") is not None
                        else None
                    ),
                    webhook_timestamp=(
                        str(row["webhook_timestamp"])
                        if row.get("webhook_timestamp") is not None
                        else None
                    ),
                    received_at=(
                        str(row["received_at"])
                        if row.get("received_at") is not None
                        else None
                    ),
                )
            )
            seen_call_ids.add(call_id)

        if len(rows) < page_size:
            break
        offset += page_size

    return recordings
