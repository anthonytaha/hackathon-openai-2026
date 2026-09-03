"""Whisper + openai/privacy-filter + FFmpeg audio redaction primitives."""

from __future__ import annotations

import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Literal
from urllib.parse import quote

import httpx
import torch
from pydantic import BaseModel, Field
from dotenv import load_dotenv
from transformers import AutoModelForTokenClassification, AutoTokenizer, pipeline

load_dotenv()

PRIVACY_MODEL = "openai/privacy-filter"
DEFAULT_WHISPER_MODEL = os.getenv("WHISPER_MODEL", "openai/whisper-small")
SUPABASE_URL = os.getenv("SUPABASE_URL", "").rstrip("/")
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "")


class PipelineError(RuntimeError):
    """A safe error suitable for returning from the HTTP layer."""


class Word(BaseModel):
    text: str
    start: float
    end: float


class DetectedEntity(BaseModel):
    label: str
    text: str
    start: int
    end: int
    score: float


class AudioSpan(BaseModel):
    start: float = Field(ge=0)
    end: float = Field(ge=0)


class SanitizeResult(BaseModel):
    transcript: str
    redacted_transcript: str
    entities: list[DetectedEntity]
    audio_spans: list[AudioSpan]


@dataclass(frozen=True)
class _Transcript:
    text: str
    words: list[Word]


class AudioPrivacyPipeline:
    """Lazily-loaded synchronous pipeline suitable for FastAPI workers or scripts."""

    def __init__(self, whisper_model: str = DEFAULT_WHISPER_MODEL) -> None:
        self.whisper_model = whisper_model
        self._asr = None
        self._privacy_filter = None

    @property
    def asr(self):
        if self._asr is None:
            self._asr = pipeline("automatic-speech-recognition", model=self.whisper_model, chunk_length_s=30, device=-1)
        return self._asr

    @property
    def privacy_filter(self):
        if self._privacy_filter is None:
            tokenizer = AutoTokenizer.from_pretrained(PRIVACY_MODEL)
            # The filter is roughly 1B parameters.  Let Accelerate choose the
            # available GPU/MPS device, matching the supplied model example.
            dtype = torch.bfloat16 if (torch.cuda.is_available() or torch.backends.mps.is_available()) else torch.float32
            model = AutoModelForTokenClassification.from_pretrained(PRIVACY_MODEL, device_map="auto", torch_dtype=dtype)
            self._privacy_filter = pipeline("token-classification", model=model, tokenizer=tokenizer, aggregation_strategy="simple")
        return self._privacy_filter

    def process_file(self, input_path: Path, output_path: Path, *, action: Literal["mute", "beep"] = "beep") -> SanitizeResult:
        if not input_path.is_file() or input_path.stat().st_size == 0:
            raise PipelineError("The supplied audio file is empty or unavailable")
        transcript = self.transcribe(input_path)
        entities = self.detect_entities(transcript.text)
        spans = self.entities_to_audio_spans(entities, transcript.text, transcript.words)
        self.redact_audio(input_path, output_path, spans, action=action)
        return SanitizeResult(transcript=transcript.text, redacted_transcript=self.redact_text(transcript.text, entities), entities=entities, audio_spans=spans)

    def transcribe(self, input_path: Path) -> _Transcript:
        try:
            result = self.asr(str(input_path), return_timestamps="word")
        except Exception as error:
            raise PipelineError(f"Whisper transcription failed: {error}") from error
        words = [Word(text=str(chunk.get("text", "")).strip(), start=float(chunk["timestamp"][0]), end=float(chunk["timestamp"][1])) for chunk in result.get("chunks", []) if chunk.get("timestamp") and None not in chunk["timestamp"] and str(chunk.get("text", "")).strip()]
        text = str(result.get("text", "")).strip()
        if not text:
            raise PipelineError("Whisper did not produce a transcript")
        if not words:
            raise PipelineError("Whisper did not return word timestamps; cannot safely redact audio")
        return _Transcript(text=text, words=words)

    def detect_entities(self, text: str) -> list[DetectedEntity]:
        try:
            predictions = self.privacy_filter(text)
        except Exception as error:
            raise PipelineError(f"Privacy filter inference failed: {error}") from error
        entities = [DetectedEntity(label=str(item.get("entity_group", item.get("entity", "PII"))), text=text[int(item["start"]):int(item["end"])], start=int(item["start"]), end=int(item["end"]), score=float(item.get("score", 0))) for item in predictions if item.get("start") is not None and item.get("end") is not None and item["end"] > item["start"]]
        return self._merge_entities(entities, text)

    @staticmethod
    def _merge_entities(entities: list[DetectedEntity], source_text: str) -> list[DetectedEntity]:
        merged: list[DetectedEntity] = []
        for entity in sorted(entities, key=lambda item: (item.start, item.end)):
            if merged and entity.start <= merged[-1].end:
                previous = merged[-1]
                end = max(previous.end, entity.end)
                merged[-1] = previous.model_copy(
                    update={
                        "end": end,
                        "text": source_text[previous.start:end],
                        "score": max(previous.score, entity.score),
                    }
                )
            else:
                merged.append(entity)
        return merged

    @staticmethod
    def redact_text(text: str, entities: list[DetectedEntity]) -> str:
        for entity in sorted(entities, key=lambda item: item.start, reverse=True):
            text = text[:entity.start] + f"[{entity.label}]" + text[entity.end:]
        return text

    @staticmethod
    def entities_to_audio_spans(entities: list[DetectedEntity], text: str, words: list[Word]) -> list[AudioSpan]:
        spans: list[AudioSpan] = []
        word_positions: list[tuple[int, int, Word]] = []
        cursor = 0
        for word in words:
            start = text.lower().find(word.text.lower(), cursor)
            if start >= 0:
                end = start + len(word.text)
                word_positions.append((start, end, word))
                cursor = end
        for entity in entities:
            overlapping = [word for start, end, word in word_positions if start < entity.end and end > entity.start]
            if overlapping:
                spans.append(AudioSpan(start=max(0, overlapping[0].start - 0.08), end=overlapping[-1].end + 0.08))
        return AudioPrivacyPipeline._merge_spans(spans)

    @staticmethod
    def _merge_spans(spans: list[AudioSpan]) -> list[AudioSpan]:
        merged: list[AudioSpan] = []
        for span in sorted(spans, key=lambda item: item.start):
            if merged and span.start <= merged[-1].end + 0.05:
                merged[-1] = AudioSpan(start=merged[-1].start, end=max(merged[-1].end, span.end))
            else:
                merged.append(span)
        return merged

    @staticmethod
    def redact_audio(input_path: Path, output_path: Path, spans: list[AudioSpan], *, action: str) -> None:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        if not spans:
            command = ["ffmpeg", "-y", "-i", str(input_path), "-vn", "-c:a", "libmp3lame", "-q:a", "2", str(output_path)]
        else:
            enabled = "+".join(f"between(t,{span.start:.3f},{span.end:.3f})" for span in spans)
            if action == "mute":
                graph = f"volume=enable='{enabled}':volume=0"
                command = ["ffmpeg", "-y", "-i", str(input_path), "-vn", "-af", graph, "-c:a", "libmp3lame", "-q:a", "2", str(output_path)]
            else:
                # `enable` bypasses a filter outside its window, which made the
                # old tone source audible for the whole file. Bake the time gate
                # into the generated waveform instead: zero samples elsewhere.
                tone = f"0.18*sin(2*PI*1000*t)*({enabled})"
                graph = f"[0:a]volume=enable='{enabled}':volume=0[clean];aevalsrc='{tone}':s=44100[tone];[clean][tone]amix=inputs=2:duration=first"
                command = ["ffmpeg", "-y", "-i", str(input_path), "-vn", "-filter_complex", graph, "-c:a", "libmp3lame", "-q:a", "2", str(output_path)]
        completed = subprocess.run(command, capture_output=True, text=True)
        if completed.returncode:
            raise PipelineError(f"FFmpeg redaction failed: {completed.stderr[-800:]}")

    @staticmethod
    def download_source(source: str, destination: Path) -> None:
        headers: dict[str, str] = {}
        if source.startswith("supabase://"):
            if not SUPABASE_URL or not SUPABASE_KEY:
                raise PipelineError("SUPABASE_URL and SUPABASE_KEY are required for supabase:// sources")
            bucket, separator, key = source.removeprefix("supabase://").partition("/")
            if not bucket or not separator or not key:
                raise PipelineError("Supabase sources must use supabase://bucket/object-key")
            source = f"{SUPABASE_URL}/storage/v1/object/{quote(bucket)}/{quote(key, safe='/')}"
            headers = {"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}"}
        try:
            with httpx.stream("GET", source, headers=headers, follow_redirects=True, timeout=60) as response:
                response.raise_for_status()
                with destination.open("wb") as target:
                    for chunk in response.iter_bytes():
                        target.write(chunk)
        except httpx.HTTPError as error:
            raise PipelineError(f"Could not download audio source: {error}") from error
