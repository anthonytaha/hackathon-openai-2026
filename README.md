# Audio Privacy Pipeline

FastAPI core for sanitising MP3 and WAV files: Whisper transcribes with word timestamps, `openai/privacy-filter` finds personal data, and FFmpeg mutes or beeps the matching audio.

## Test locally

Install the project environment and start the API in one terminal:

```bash
uv sync
.venv/bin/uvicorn main:app --reload
```

Then upload the included WAV fixture from a second terminal:

```bash
curl -X POST http://127.0.0.1:8000/v1/sanitize \
  -F 'audio=@test_audio.wav' \
  -F 'action=beep'
```

Use `-F 'action=mute'` to silence PII instead of replacing it with a beep. The JSON response includes the sanitized-audio, Whisper-transcript, and privacy-filter-result URLs.

`POST /v1/sanitize` explicitly accepts `.mp3` and `.wav` uploads. `POST /v1/sanitize-url` accepts public or signed URLs. It also accepts `supabase://bucket/object.wav` when `SUPABASE_URL` and `SUPABASE_KEY` are set. Every successful run returns and saves three outputs: the sanitized MP3, the raw Whisper transcript (`.whisper.txt`), and the privacy-filter result (`.privacy-filter.json`, with redacted text, entities, and audio ranges). Set `action=mute` for silence or `action=beep` for replacement beeps only at PII ranges. Models download lazily on the first request; FFmpeg must be on `PATH`.
