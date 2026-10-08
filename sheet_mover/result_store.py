"""Paths and discovery for generated Sheet Mover result files."""
from __future__ import annotations

import json
from datetime import datetime
import os
from pathlib import Path
import re
import tempfile
from typing import Iterable


_DATA_ROOT = Path(os.getenv("SHEETMOVER_DATA_DIR", "."))
CURRENT_RESULT_DIR = _DATA_ROOT / "results" / "current"
ARCHIVE_DIR = _DATA_ROOT / "output_archive"

_RESULT_RE = re.compile(
    r"^sheet-result-(?P<source>.+)-(?P<stamp>\d{8}-\d{6})(?:\(\d+\))?\.json$"
)
_PARTIAL_RE = re.compile(
    r"^sheet-partial-(?P<stamp>\d{8}-\d{6})(?:\(\d+\))?\.json$"
)


def write_json_atomic(path: str | Path, payload: dict) -> Path:
    """Keep the last intact JSON if serialization or replacement fails."""
    path = Path(path)
    text = json.dumps(payload, ensure_ascii=False, indent=2)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=path.parent,
            prefix=f".{path.name}.", suffix=".tmp", delete=False,
        ) as stream:
            temp_path = Path(stream.name)
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp_path, path)
    finally:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)
    return path


def record_stage_failure(exc: Exception, report: dict, path: Path):
    """Expose a failed writer's evidence without hiding the original error."""
    report["status"] = "error"
    report["error"] = str(exc)
    report["error_code"] = getattr(exc, "code", "stage_failed")
    if report["error_code"] == "verification_unconfirmed":
        report.setdefault("verification", {})["status"] = "unconfirmed"
    exc.stage_report = report
    exc.stage_report_path = str(Path(path).resolve())
    try:
        write_json_atomic(path, report)
    except Exception as save_exc:
        exc.save_errors = list(getattr(exc, "save_errors", [])) + [
            f"단계 실패 기록 저장 실패: {save_exc}"
        ]


def result_timestamp(path: Path) -> str:
    match = _RESULT_RE.match(path.name) or _PARTIAL_RE.match(path.name)
    return (match.group("stamp") if match else "").replace("-", "")


def result_sort_key(path: Path):
    stamp = result_timestamp(path)
    try:
        mtime = path.stat().st_mtime
    except OSError:
        mtime = 0.0
    return (bool(stamp), stamp, mtime, path.name)


def is_complete_result(path: Path) -> bool:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return False
    if not isinstance(payload, dict):
        return False
    summary = payload.get("translation_summary")
    if not isinstance(summary, dict):
        translated = payload.get("translated")
        summary = (
            translated.get("translation_summary")
            if isinstance(translated, dict)
            else None
        )
    return (
        isinstance(summary, dict)
        and summary.get("status") == "complete"
    )


def source_id_from_result(path: Path) -> str:
    match = _RESULT_RE.match(path.name)
    if match:
        return match.group("source")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        original = payload.get("original") if isinstance(payload, dict) else None
        return str((original or {}).get("source_id") or "").strip()
    except Exception:
        return ""


def iter_result_candidates(
    root: str | Path | None = None,
    include_legacy_root: bool = True,
) -> Iterable[Path]:
    if root is None:
        data_root = _DATA_ROOT.resolve()
        legacy_root = Path(".").resolve()
    else:
        data_root = Path(root).resolve()
        legacy_root = data_root

    seen: set[Path] = set()
    folders = [
        data_root / "results" / "cache",
        data_root / "results" / "current",
    ]

    # Backward compatibility for the source checkout layout.
    if include_legacy_root:
        folders.append(legacy_root)

    for folder in folders:
        try:
            paths = folder.glob("sheet-result-*.json")
        except OSError:
            continue
        for path in paths:
            try:
                resolved = path.resolve()
            except OSError:
                resolved = path
            if resolved in seen:
                continue
            seen.add(resolved)
            yield path


def latest_complete_result(
    root: str | Path | None = None,
    source_id: str | None = None,
) -> Path | None:
    wanted = str(source_id or "").strip()
    paths = []
    for path in iter_result_candidates(root):
        if wanted and source_id_from_result(path) != wanted:
            continue
        if is_complete_result(path):
            paths.append(path)
    if not paths:
        return None
    return sorted(paths, key=result_sort_key, reverse=True)[0]


def load_result(path: str | Path) -> dict:
    path = Path(path)
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"결과 JSON 최상위가 객체가 아닙니다: {path}")
    return payload


def default_result_path(
    source_id: str,
    now: datetime | None = None,
    root: str | Path | None = None,
) -> Path:
    now = now or datetime.now()
    safe_source_id = "".join(
        ch for ch in str(source_id or "unknown")
        if ch.isalnum() or ch in ("-", "_")
    ) or "unknown"
    data_root = Path(root) if root is not None else _DATA_ROOT
    return (
        data_root
        / "results"
        / "current"
        / f"sheet-result-{safe_source_id}-{now:%Y%m%d-%H%M%S}.json"
    )
