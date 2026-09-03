from types import SimpleNamespace

import pytest

from app.services.supabase_calls import (
    SELECT_COLUMNS,
    SupabaseCallsError,
    list_call_recordings,
    resolve_recording_object_path,
)


def test_call_id_maps_to_mp3_below_input_prefix() -> None:
    assert (
        resolve_recording_object_path(
            call_id="call-123",
            recording_path=None,
            bucket="recordings",
            input_prefix="incoming/calls",
        )
        == "incoming/calls/call-123.mp3"
    )


@pytest.mark.parametrize(
    ("recording_path", "expected"),
    [
        ("existing/call-123.mp3", "existing/call-123.mp3"),
        ("recordings/existing/call-123.mp3", "existing/call-123.mp3"),
        ("existing/call-123", "existing/call-123.mp3"),
    ],
)
def test_recording_path_is_used_when_present(
    recording_path: str, expected: str
) -> None:
    assert (
        resolve_recording_object_path(
            call_id="call-123",
            recording_path=recording_path,
            bucket="recordings",
            input_prefix="incoming",
        )
        == expected
    )


def test_allo_calls_query_is_paginated_and_does_not_fetch_payload() -> None:
    pages = [
        [
            {
                "call_id": "call-2",
                "topic": "Support",
                "recording_path": None,
                "recording_status": "ready",
                "received_at": "2026-09-03T12:00:00Z",
            },
            {
                "call_id": "call-1",
                "topic": "Sales",
                "recording_path": "custom/call-1.mp3",
                "recording_status": "complete",
                "received_at": "2026-09-03T11:00:00Z",
            },
        ],
        [],
    ]

    class Query:
        def __init__(self) -> None:
            self.selected = None
            self.ranges = []

        def select(self, columns):
            self.selected = columns
            return self

        def order(self, column, *, desc):
            assert (column, desc) == ("received_at", True)
            return self

        def range(self, start, end):
            self.ranges.append((start, end))
            return self

        def execute(self):
            return SimpleNamespace(data=pages.pop(0))

    query = Query()

    class Client:
        def table(self, name):
            assert name == "allo_calls"
            return query

    recordings = list_call_recordings(
        Client(),
        table="allo_calls",
        bucket="recordings",
        input_prefix="incoming",
        page_size=2,
    )

    assert "payload" not in SELECT_COLUMNS
    assert query.selected == SELECT_COLUMNS
    assert query.ranges == [(0, 1), (2, 3)]
    assert [recording.call_id for recording in recordings] == ["call-2", "call-1"]
    assert recordings[0].object_path == "incoming/call-2.mp3"
    assert recordings[1].object_path == "custom/call-1.mp3"


def test_invalid_call_id_is_rejected() -> None:
    with pytest.raises(ValueError, match="call_id"):
        resolve_recording_object_path(
            call_id="../secret",
            recording_path=None,
            bucket="recordings",
        )


def test_invalid_table_name_is_rejected_before_query() -> None:
    with pytest.raises(SupabaseCallsError, match="table name"):
        list_call_recordings(
            object(),
            table="public.allo_calls",
            bucket="recordings",
        )
