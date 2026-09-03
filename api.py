"""HTTP entry point for the audio privacy pipeline."""

from __future__ import annotations

import json
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, Literal

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
from pydantic import BaseModel, HttpUrl, model_validator

from privacy_pipeline import AudioPrivacyPipeline, PipelineError, SanitizeResult

OUTPUT_DIR = Path("artifacts")
pipeline = AudioPrivacyPipeline()


@asynccontextmanager
async def lifespan(_: FastAPI):
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    yield


app = FastAPI(title="Audio Privacy Pipeline", version="0.1.0", lifespan=lifespan)


class SanitizeResponse(BaseModel):
    id: str
    sanitized_audio_url: str
    whisper_transcript_url: str
    privacy_filter_result_url: str
    whisper_transcript: str
    privacy_filter: dict


class UrlSanitizeRequest(BaseModel):
    """Use a normal public/signed URL or ``supabase://bucket/path/to/file.wav``."""

    source_url: HttpUrl | str
    action: Literal["mute", "beep"] = "beep"

    @model_validator(mode="after")
    def validate_source(self) -> "UrlSanitizeRequest":
        if not str(self.source_url).startswith(("http://", "https://", "supabase://")):
            raise ValueError("source_url must be http(s) or supabase://bucket/object-key")
        return self


def response_from(result_id: str, result: SanitizeResult) -> SanitizeResponse:
    privacy_result = {
        "redacted_transcript": result.redacted_transcript,
        "entities": [entity.model_dump() for entity in result.entities],
        "redaction_ranges": [span.model_dump() for span in result.audio_spans],
    }
    (OUTPUT_DIR / f"{result_id}.whisper.txt").write_text(
        result.transcript + "\n", encoding="utf-8"
    )
    (OUTPUT_DIR / f"{result_id}.privacy-filter.json").write_text(
        json.dumps(privacy_result, indent=2) + "\n", encoding="utf-8"
    )
    return SanitizeResponse(
        id=result_id,
        sanitized_audio_url=f"/v1/results/{result_id}/audio",
        whisper_transcript_url=f"/v1/results/{result_id}/transcript",
        privacy_filter_result_url=f"/v1/results/{result_id}/privacy-filter",
        whisper_transcript=result.transcript,
        privacy_filter=privacy_result,
    )


async def save_upload(upload: UploadFile, destination: Path) -> None:
    with destination.open("wb") as target:
        while chunk := await upload.read(1024 * 1024):
            target.write(chunk)


@app.get("/")
def service_index() -> dict[str, str]:
    return {
        "service": "Audio Privacy Pipeline",
        "health": "/healthz",
        "docs": "/docs",
    }


@app.get("/healthz")
def healthcheck() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/v1/sanitize", response_model=SanitizeResponse)
async def sanitize_upload(
    audio: Annotated[UploadFile, File(description="An MP3 or WAV audio file")],
    action: Annotated[Literal["mute", "beep"], Form()] = "beep",
) -> SanitizeResponse:
    """Sanitize a locally uploaded audio file."""
    result_id = str(uuid.uuid4())
    suffix = Path(audio.filename or ".wav").suffix.lower()
    if suffix not in {".mp3", ".wav"}:
        raise HTTPException(status_code=415, detail="Only .mp3 and .wav uploads are supported")
    input_path = OUTPUT_DIR / f"{result_id}.input{suffix}"
    output_path = OUTPUT_DIR / f"{result_id}.sanitized.mp3"
    try:
        await save_upload(audio, input_path)
        result = pipeline.process_file(input_path, output_path, action=action)
        return response_from(result_id, result)
    except PipelineError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    finally:
        input_path.unlink(missing_ok=True)


@app.post("/v1/sanitize-url", response_model=SanitizeResponse)
def sanitize_url(request: UrlSanitizeRequest) -> SanitizeResponse:
    """Download and sanitize a signed/public URL or a Supabase Storage object."""
    result_id = str(uuid.uuid4())
    downloaded_path = OUTPUT_DIR / f"{result_id}.input.mp3"
    output_path = OUTPUT_DIR / f"{result_id}.sanitized.mp3"
    try:
        pipeline.download_source(str(request.source_url), downloaded_path)
        result = pipeline.process_file(downloaded_path, output_path, action=request.action)
        return response_from(result_id, result)
    except PipelineError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    finally:
        downloaded_path.unlink(missing_ok=True)


@app.get("/v1/results/{result_id}/audio")
def download_sanitized_audio(result_id: str) -> FileResponse:
    path = OUTPUT_DIR / f"{result_id}.sanitized.mp3"
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Sanitized audio not found")
    return FileResponse(path, media_type="audio/mpeg", filename="sanitized.mp3")


@app.get("/v1/results/{result_id}/transcript")
def download_whisper_transcript(result_id: str) -> PlainTextResponse:
    path = OUTPUT_DIR / f"{result_id}.whisper.txt"
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Whisper transcript not found")
    return PlainTextResponse(path.read_text(encoding="utf-8"))


@app.get("/v1/results/{result_id}/privacy-filter")
def download_privacy_filter_result(result_id: str) -> JSONResponse:
    path = OUTPUT_DIR / f"{result_id}.privacy-filter.json"
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Privacy filter result not found")
    return JSONResponse(content=json.loads(path.read_text(encoding="utf-8")))


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("api:app", host="0.0.0.0", port=8000, reload=True)
