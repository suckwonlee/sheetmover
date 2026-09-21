"""Shared translation runtime configuration."""
from __future__ import annotations
import os

MODEL_NAME = os.getenv("SHEETMOVER_REVIEW_MODEL", "gpt-oss:20b")
OLLAMA_HOST = os.getenv("SHEETMOVER_OLLAMA_HOST", "http://127.0.0.1:11434")
OLLAMA_TIMEOUT = max(30, int(os.getenv("SHEETMOVER_OLLAMA_TIMEOUT", "300")))
OLLAMA_REVIEW_RETRIES = max(0, int(os.getenv("SHEETMOVER_OLLAMA_REVIEW_RETRIES", "1")))
OLLAMA_KEEP_ALIVE = os.getenv("SHEETMOVER_OLLAMA_KEEP_ALIVE", "15m")
OLLAMA_THINK = os.getenv("SHEETMOVER_OLLAMA_THINK", "low").strip().lower() or "low"
if OLLAMA_THINK not in {"low", "medium", "high"}:
    OLLAMA_THINK = "low"
OLLAMA_CONTEXT = max(2048, int(os.getenv("SHEETMOVER_OLLAMA_CONTEXT", "4096")))
OLLAMA_NUM_PREDICT = max(256, int(os.getenv("SHEETMOVER_OLLAMA_NUM_PREDICT", "1024")))

GOOGLE_CACHE_VERSION = "2026-09-21-google-v15-first-pass"
REVIEW_CACHE_VERSION = "2026-09-21-review-v15.1-equivalent-finalstate"
VALIDATOR_VERSION = "2026-09-21-semantic-v15"

PIPELINE_BUILD = "2026-09-21-v15.1-equivalent-finalstate"
