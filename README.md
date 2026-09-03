# Privacy-Aware MP3 Transcription Workflow

This project combines a Streamlit operator dashboard with an audio privacy
pipeline. It lists private MP3 recordings from Supabase Storage, transcribes a
selected recording with Whisper, detects personal data with
`openai/privacy-filter`, redacts the matching audio spans with FFmpeg, uploads the
processed artifacts, and calls a provider-neutral SMS endpoint.

The same privacy pipeline is also available through a FastAPI service.

## Architecture

```text
app/
├── dashboard.py                    Streamlit UI and session-state controller
├── models/
│   └── processing_result.py        ProcessingResult and JobState contracts
└── services/
    ├── pipeline_service.py         Dashboard adapter for the privacy pipeline
    ├── supabase_calls.py           allo_calls query and call-ID/path mapping
    ├── supabase_storage.py         Storage listing/download/upload helpers
    ├── sms_client.py               Generic SMS HTTP client
    └── workflow_service.py         Upload-before-notification sequencing

privacy_pipeline.py                 Whisper + privacy filter + FFmpeg core
main.py                             Streamlit entry point
api.py                              FastAPI entry point
tests/                              Unit tests with mocked integrations
```

The dashboard still consumes only `ProcessingResult`. Model-specific behavior is
contained in `privacy_pipeline.py` and its adapter in
`app/services/pipeline_service.py`.

## Requirements

- Python 3.13 or newer
- FFmpeg available on `PATH`
- Enough disk and memory for the configured Whisper and privacy-filter models

Models download lazily on the first processing request.

## Installation

With `uv`:

```bash
uv sync
source .venv/bin/activate
```

Or with standard Python packaging:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e '.[test]'
```

## Configuration

```bash
cp .env.example .env
```

```env
SUPABASE_URL=
SUPABASE_KEY=
SUPABASE_AUDIO_BUCKET=
SUPABASE_CALLS_TABLE=allo_calls
SUPABASE_INPUT_PREFIX=
SUPABASE_OUTPUT_PREFIX=processed

WHISPER_MODEL=openai/whisper-small
PRIVACY_REDACTION_ACTION=beep

SMS_API_URL=
SMS_API_KEY=
SMS_RECIPIENT=
```

`PRIVACY_REDACTION_ACTION` accepts `beep` or `mute`. `SUPABASE_CALLS_TABLE`
defaults to `allo_calls` in the `public` schema. Supabase credentials are optional
for local uploads. If configured, the dashboard uploads the processed artifacts
and can send an SMS; otherwise local results remain available for download in the
browser.

Keep Supabase and SMS credentials server-side. They are never rendered by the UI,
and the Storage bucket does not need to be public.

## Streamlit dashboard

```bash
.venv/bin/streamlit run main.py
```

Use **Local upload** to process an MP3 or WAV directly from your computer. The
**Supabase calls** tab reads lightweight rows from `public.allo_calls`
without retrieving the `payload` JSON. When `recording_path` is populated, it is
used as the path inside `SUPABASE_AUDIO_BUCKET`. Otherwise the dashboard maps
`call_id` to `<call_id>.mp3` below `SUPABASE_INPUT_PREFIX`. Selecting a call
downloads only that recording. Processing, uploads, and SMS happen only after an
explicit button click. Upload and SMS failures have stage-specific retry buttons,
preventing Streamlit reruns from duplicating earlier side effects.

Every processing attempt uses a unique temporary directory and UUID. Outputs are
uploaded as:

```text
processed/<source-stem>/<job-id>/processed.mp3
processed/<source-stem>/<job-id>/transcript.txt
processed/<source-stem>/<job-id>/transcript.json
```

`processed.mp3` contains the beeped or muted private-data ranges. `transcript.txt`
contains the raw Whisper transcript. `transcript.json` contains the raw and redacted
transcripts, detected entities, confidence scores, and audio redaction ranges. Treat
the transcript artifacts as sensitive data and configure Supabase access policies
accordingly.

## FastAPI service

```bash
uvicorn api:app --reload
```

Upload the included fixture:

```bash
curl -X POST http://127.0.0.1:8000/v1/sanitize \
  -F 'audio=@test_audio.wav' \
  -F 'action=beep'
```

Use `action=mute` to silence detected ranges. `POST /v1/sanitize` accepts MP3 and
WAV uploads. `POST /v1/sanitize-url` accepts public/signed HTTP URLs and
`supabase://bucket/object-key` sources. Successful responses provide endpoints for
the sanitized audio, Whisper transcript, and privacy-filter result.

## Tests

```bash
pytest
```

The unit tests use temporary files and mocked HTTP/Supabase/model clients. They do
not require production credentials or model downloads.

Example output generated from `test_audio.wav` is available under
`example_artifacts/`.

## Future persistence

`JobState` currently lives in Streamlit session state. It can later map to a
Supabase `processing_jobs` table containing the job ID, source/output paths, status,
transcript, pipeline metadata, SMS status, timestamps, and error message.
