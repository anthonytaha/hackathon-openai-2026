# MP3 Transcription Workflow

A Streamlit operator dashboard that lists private MP3 recordings from Supabase
Storage, previews a selected file, runs a deliberately isolated placeholder
pipeline, uploads the resulting audio and transcript artifacts, and calls a
provider-neutral SMS HTTP endpoint.

The current transcript is demo text. No speech recognition, confidence score,
speaker detection, timestamps, or language detection is performed.

## Architecture

```text
app/
├── dashboard.py                    Streamlit UI and session-state controller
├── models/
│   └── processing_result.py        ProcessingResult and JobState contracts
└── services/
    ├── pipeline_service.py         Replaceable placeholder pipeline
    ├── supabase_storage.py         Storage listing/download/upload helpers
    ├── sms_client.py               Generic SMS HTTP client
    └── workflow_service.py         Upload-before-notification sequencing
tests/                              Isolated unit tests with mocked integrations
```

The dashboard consumes `ProcessingResult`, not a transcription SDK. Supabase and
SMS details are isolated behind service functions. Job state is currently held in
`st.session_state`; it could later be persisted to a `processing_jobs` Supabase
table without changing the pipeline contract.

## Installation

Python 3.10 or newer is required.

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e '.[test]'
```

## Configuration

Create a local environment file (it is ignored by Git):

```bash
cp .env.example .env
```

Configure these values:

```env
SUPABASE_URL=
SUPABASE_KEY=
SUPABASE_AUDIO_BUCKET=
SUPABASE_INPUT_PREFIX=
SUPABASE_OUTPUT_PREFIX=processed

SMS_API_URL=
SMS_API_KEY=
SMS_RECIPIENT=
```

`SUPABASE_URL`, `SUPABASE_KEY`, and `SUPABASE_AUDIO_BUCKET` are required to list
recordings. The input prefix may be empty. SMS configuration is checked only when
notification is reached; if it is incomplete, the completed processing/upload job
is displayed with SMS marked `Skipped`.

Use a server-side Supabase key with only the Storage permissions the application
needs. The key and SMS bearer token are never rendered by the app. The bucket may
remain private; no public-bucket shortcut is needed.

The SMS endpoint receives:

```json
{
  "to": "+...",
  "message": "Audio processing completed.",
  "job_id": "...",
  "source_file": "incoming/example.mp3",
  "processed_file": "processed/example/<job-id>/processed.mp3"
}
```

with `Authorization: Bearer $SMS_API_KEY`. Provider-specific request changes belong
only in `app/services/sms_client.py`.

## Start the dashboard

From the repository root:

```bash
streamlit run app/dashboard.py
```

Listing retrieves metadata only; it does not download every recording. Selecting
one recording downloads it for preview. Processing, uploads, and SMS happen only
after an explicit button click. Failed uploads and SMS calls have separate retry
buttons, preventing Streamlit reruns from repeating earlier side effects.

Each job uses a unique temporary directory and UUID. Uploaded objects use:

```text
processed/<source-stem>/<job-id>/processed.mp3
processed/<source-stem>/<job-id>/transcript.txt
processed/<source-stem>/<job-id>/transcript.json
```

Starting another job or clearing the current job removes the previous temporary
workspace.

## Run tests

```bash
pytest
```

Tests use local temporary files and mock clients. They require no Supabase or SMS
credentials.

## Replacing the placeholder pipeline

Replace this one function:

```text
app/services/pipeline_service.py::process_audio(input_path, output_dir)
```

Keep its signature and return a populated `ProcessingResult`. The implementation
must create an output MP3 and any desired artifacts in the supplied output
directory. For compatibility with the current uploader, retain artifacts named
`processed.mp3`, `transcript.txt`, and `transcript.json`. Whisper, OpenAI
speech-to-text, Deepgram, AssemblyAI, or a custom processor can then be introduced
without rewriting the dashboard, Storage service, or SMS integration.

## Future persistence

A future `processing_jobs` table can store the job ID, source/output paths, status,
transcript, pipeline metadata, SMS status, timestamps, and error message. Database
persistence is intentionally not required in this version.
