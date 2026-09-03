# Example pipeline outputs

These files are produced from the included `test_audio.wav` fixture using
`action=beep`:

- `sanitized_audio.mp3`: audio with PII spans replaced by a beep.
- `whisper_transcript.txt`: the unredacted Whisper transcription.
- `privacy_filter_result.json`: detected PII, the redacted transcript, and
  exact audio redaction timestamps.
