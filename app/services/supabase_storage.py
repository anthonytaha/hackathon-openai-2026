"""Server-side helpers for Supabase Storage."""

from __future__ import annotations

import mimetypes
import os
import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Iterable, Mapping


class SupabaseStorageError(RuntimeError):
    """A human-readable Supabase Storage failure."""


@dataclass(frozen=True, slots=True)
class SupabaseSettings:
    url: str
    key: str
    bucket: str
    input_prefix: str = ""
    output_prefix: str = "processed"
    calls_table: str = "allo_calls"

    @classmethod
    def from_env(cls) -> "SupabaseSettings":
        values = {
            "url": os.getenv("SUPABASE_URL", "").strip(),
            "key": os.getenv("SUPABASE_KEY", "").strip(),
            "bucket": os.getenv("SUPABASE_AUDIO_BUCKET", "").strip(),
        }
        missing = [
            env_name
            for field_name, env_name in (
                ("url", "SUPABASE_URL"),
                ("key", "SUPABASE_KEY"),
                ("bucket", "SUPABASE_AUDIO_BUCKET"),
            )
            if not values[field_name]
        ]
        if missing:
            raise ValueError(f"Missing required environment variables: {', '.join(missing)}")
        return cls(
            **values,
            input_prefix=os.getenv("SUPABASE_INPUT_PREFIX", "").strip("/"),
            output_prefix=os.getenv("SUPABASE_OUTPUT_PREFIX", "processed").strip("/")
            or "processed",
            calls_table=os.getenv("SUPABASE_CALLS_TABLE", "allo_calls").strip()
            or "allo_calls",
        )


@dataclass(frozen=True, slots=True)
class StorageObject:
    name: str
    path: str
    size: int | None = None
    modified_at: str | None = None


def create_supabase_client(url: str, key: str) -> Any:
    """Create the official client without importing it during unit tests."""

    try:
        from supabase import create_client
    except ImportError as exc:  # pragma: no cover - depends on environment setup
        raise SupabaseStorageError(
            "The 'supabase' package is not installed. Run 'pip install -e .'."
        ) from exc
    try:
        return create_client(url, key)
    except Exception as exc:
        raise SupabaseStorageError(f"Could not initialize Supabase: {exc}") from exc


def _field(item: Any, name: str, default: Any = None) -> Any:
    if isinstance(item, Mapping):
        return item.get(name, default)
    return getattr(item, name, default)


def _join_object_path(prefix: str, name: str) -> str:
    clean_prefix = prefix.strip("/")
    clean_name = name.lstrip("/")
    return f"{clean_prefix}/{clean_name}" if clean_prefix else clean_name


def filter_mp3_objects(
    objects: Iterable[Any], *, parent_prefix: str = ""
) -> list[StorageObject]:
    """Normalize an object listing and retain MP3 files only (case-insensitive)."""

    result: list[StorageObject] = []
    for item in objects:
        name = str(_field(item, "name", "") or "")
        if not name.lower().endswith(".mp3"):
            continue
        metadata = _field(item, "metadata", {}) or {}
        size = _field(item, "size")
        if size is None and isinstance(metadata, Mapping):
            size = metadata.get("size")
        try:
            normalized_size = int(size) if size is not None else None
        except (TypeError, ValueError):
            normalized_size = None
        modified = (
            _field(item, "updated_at")
            or _field(item, "last_modified")
            or _field(item, "created_at")
        )
        path = _join_object_path(parent_prefix, name)
        result.append(
            StorageObject(
                name=PurePosixPath(name).name,
                path=path,
                size=normalized_size,
                modified_at=str(modified) if modified else None,
            )
        )
    return sorted(result, key=lambda obj: obj.path.lower())


def _looks_like_folder(item: Any) -> bool:
    name = str(_field(item, "name", "") or "")
    if not name or name.lower().endswith(".mp3"):
        return False
    metadata = _field(item, "metadata")
    object_id = _field(item, "id")
    return metadata is None and object_id is None


def list_mp3_objects(client: Any, bucket: str, prefix: str = "") -> list[StorageObject]:
    """Recursively list MP3 objects without downloading object contents."""

    bucket_api = client.storage.from_(bucket)
    found: list[StorageObject] = []
    visited: set[str] = set()

    def walk(current_prefix: str) -> None:
        current_prefix = current_prefix.strip("/")
        if current_prefix in visited:
            return
        visited.add(current_prefix)
        offset = 0
        page_size = 1000
        while True:
            try:
                entries = bucket_api.list(
                    path=current_prefix,
                    options={
                        "limit": page_size,
                        "offset": offset,
                        "sortBy": {"column": "name", "order": "asc"},
                    },
                )
            except Exception as exc:
                raise SupabaseStorageError(
                    f"Could not list bucket '{bucket}' at '{current_prefix or '/'}': {exc}"
                ) from exc
            entries = entries or []
            found.extend(filter_mp3_objects(entries, parent_prefix=current_prefix))
            for entry in entries:
                if _looks_like_folder(entry):
                    walk(_join_object_path(current_prefix, str(_field(entry, "name"))))
            if len(entries) < page_size:
                break
            offset += page_size

    walk(prefix)
    return sorted(found, key=lambda obj: obj.path.lower())


def download_object(client: Any, bucket: str, object_path: str) -> bytes:
    try:
        content = client.storage.from_(bucket).download(object_path)
    except Exception as exc:
        raise SupabaseStorageError(f"Could not download '{object_path}': {exc}") from exc
    if not isinstance(content, (bytes, bytearray)):
        raise SupabaseStorageError(f"Supabase returned no binary data for '{object_path}'.")
    return bytes(content)


def upload_object(
    client: Any, bucket: str, object_path: str, local_path: Path
) -> str:
    """Upload a local artifact with upsert enabled to support explicit retries."""

    local_path = Path(local_path)
    if not local_path.is_file():
        raise SupabaseStorageError(f"Upload artifact does not exist: {local_path}")
    content_type = mimetypes.guess_type(local_path.name)[0] or "application/octet-stream"
    try:
        client.storage.from_(bucket).upload(
            path=object_path,
            file=local_path.read_bytes(),
            file_options={"content-type": content_type, "upsert": "true"},
        )
    except Exception as exc:
        raise SupabaseStorageError(f"Could not upload '{object_path}': {exc}") from exc
    return object_path


def create_signed_url(
    client: Any, bucket: str, object_path: str, expires_in: int = 3600
) -> str:
    try:
        response = client.storage.from_(bucket).create_signed_url(object_path, expires_in)
    except Exception as exc:
        raise SupabaseStorageError(
            f"Could not create a signed URL for '{object_path}': {exc}"
        ) from exc
    if isinstance(response, Mapping):
        url = response.get("signedURL") or response.get("signed_url")
    else:
        url = getattr(response, "signed_url", None)
    if not url:
        raise SupabaseStorageError("Supabase did not return a signed URL.")
    return str(url)


def _safe_source_stem(source_object_path: str) -> str:
    stem = PurePosixPath(source_object_path).stem
    safe = re.sub(r"[^A-Za-z0-9._-]+", "-", stem).strip(".-")
    return safe or "recording"


def generate_output_storage_paths(
    *,
    source_object_path: str,
    job_id: str,
    output_prefix: str = "processed",
    artifact_names: Iterable[str] = ("processed.mp3", "transcript.txt", "transcript.json"),
) -> dict[str, str]:
    """Build safe, deterministic destination paths for a processing attempt."""

    safe_job_id = re.sub(r"[^A-Za-z0-9-]+", "", job_id)
    if not safe_job_id:
        raise ValueError("job_id must contain letters, digits, or hyphens")
    base = "/".join(
        part
        for part in (output_prefix.strip("/"), _safe_source_stem(source_object_path), safe_job_id)
        if part
    )
    paths: dict[str, str] = {}
    for artifact_name in artifact_names:
        safe_name = PurePosixPath(artifact_name).name
        if safe_name not in {"processed.mp3", "transcript.txt", "transcript.json"}:
            raise ValueError(f"Unsupported artifact name: {artifact_name}")
        paths[safe_name] = f"{base}/{safe_name}"
    return paths
