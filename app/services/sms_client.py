"""Provider-neutral HTTP SMS integration."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Mapping

import httpx


class SmsError(RuntimeError):
    """A sanitized, human-readable SMS delivery failure."""


@dataclass(frozen=True, slots=True)
class SmsResult:
    success: bool
    status_code: int
    response_data: Any = None


def send_processing_complete_sms(
    *,
    recipient: str,
    source_object_path: str,
    output_object_path: str,
    job_id: str,
    api_url: str | None = None,
    api_key: str | None = None,
    timeout_seconds: float = 10.0,
    client: Any | None = None,
) -> SmsResult:
    """Notify a recipient after uploads succeed.

    Request construction is intentionally isolated here so a provider-specific
    payload or authentication scheme can be introduced without changing the UI.
    """

    api_url = (api_url or os.getenv("SMS_API_URL", "")).strip()
    api_key = (api_key or os.getenv("SMS_API_KEY", "")).strip()
    recipient = recipient.strip()
    missing = [
        name
        for name, value in (
            ("SMS_API_URL", api_url),
            ("SMS_API_KEY", api_key),
            ("SMS_RECIPIENT", recipient),
        )
        if not value
    ]
    if missing:
        raise SmsError(f"Missing SMS configuration: {', '.join(missing)}")

    payload = {
        "to": recipient,
        "message": "Audio processing completed.",
        "job_id": job_id,
        "source_file": source_object_path,
        "processed_file": output_object_path,
    }
    owns_client = client is None
    http_client = client or httpx.Client(timeout=timeout_seconds)
    try:
        response = http_client.post(
            api_url,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=timeout_seconds,
        )
        response.raise_for_status()
    except httpx.TimeoutException as exc:
        raise SmsError(f"SMS API timed out after {timeout_seconds:g} seconds.") from exc
    except httpx.HTTPStatusError as exc:
        status = exc.response.status_code
        raise SmsError(f"SMS API returned HTTP {status}.") from exc
    except httpx.RequestError as exc:
        raise SmsError(f"Could not reach the SMS API: {type(exc).__name__}.") from exc
    except Exception as exc:
        # Test doubles and alternative httpx transports may raise generic errors.
        raise SmsError(f"SMS request failed: {type(exc).__name__}.") from exc
    finally:
        if owns_client:
            http_client.close()

    try:
        response_data: Any = response.json()
    except (ValueError, TypeError):
        response_data = response.text[:500]
    return SmsResult(
        success=True,
        status_code=response.status_code,
        response_data=response_data,
    )
