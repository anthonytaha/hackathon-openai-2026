from app.services.supabase_storage import (
    StorageObject,
    filter_mp3_objects,
    generate_output_storage_paths,
    list_mp3_objects,
)


def test_mp3_filtering_normalizes_supported_objects() -> None:
    objects = [
        {"name": "first.mp3", "metadata": {"size": 123}, "updated_at": "2026-01-02"},
        {"name": "SECOND.MP3", "metadata": {"size": "456"}},
        {"name": "notes.txt", "metadata": {"size": 4}},
        {"name": "almost.mp3.tmp", "metadata": {"size": 9}},
    ]

    result = filter_mp3_objects(objects, parent_prefix="incoming/calls")

    assert result == [
        StorageObject("first.mp3", "incoming/calls/first.mp3", 123, "2026-01-02"),
        StorageObject("SECOND.MP3", "incoming/calls/SECOND.MP3", 456, None),
    ]


def test_nested_mp3_listing_does_not_download_files() -> None:
    class Bucket:
        def __init__(self) -> None:
            self.paths = []

        def list(self, *, path, options):
            self.paths.append(path)
            if path == "incoming":
                return [
                    {"name": "top.mp3", "id": "1", "metadata": {"size": 1}},
                    {"name": "nested", "id": None, "metadata": None},
                ]
            return [{"name": "inside.mp3", "id": "2", "metadata": {"size": 2}}]

    class Storage:
        def __init__(self, bucket):
            self.bucket = bucket

        def from_(self, name):
            assert name == "audio"
            return self.bucket

    bucket = Bucket()
    client = type("Client", (), {"storage": Storage(bucket)})()

    result = list_mp3_objects(client, "audio", "incoming")

    assert [item.path for item in result] == [
        "incoming/nested/inside.mp3",
        "incoming/top.mp3",
    ]
    assert bucket.paths == ["incoming", "incoming/nested"]


def test_output_storage_path_generation_sanitizes_source_name() -> None:
    result = generate_output_storage_paths(
        source_object_path="incoming/client calls/../../Quarterly Call!!.mp3",
        output_prefix="processed/",
        job_id="123e4567-e89b-12d3-a456-426614174000",
    )

    base = "processed/Quarterly-Call/123e4567-e89b-12d3-a456-426614174000"
    assert result == {
        "processed.mp3": f"{base}/processed.mp3",
        "transcript.txt": f"{base}/transcript.txt",
        "transcript.json": f"{base}/transcript.json",
    }
