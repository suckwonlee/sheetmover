# -*- coding: utf-8 -*-
"""Consolidate one Sheet Mover execution into one JSON in results/current."""
from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import shutil


SINGLE_LOG_VERSION = "2026-10-08-single-current-log-v1.1"
FINAL_LOG_NAME = "sheetmover-run-latest.json"


def _dict(value):
    return value if isinstance(value, dict) else {}


def _read_json(path):
    path = Path(path)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    return payload


def _is_generated_current_json(path):
    name = Path(path).name
    return (
        name == FINAL_LOG_NAME
        or name.startswith("full-run-")
        or name.startswith("roll20-")
        or name.startswith("sheet-result-")
        or name.startswith("sheet-partial-")
    ) and name.endswith(".json")


def _walk_json_paths(value):
    # Every existing JSON path embedded in the run state is eligible.
    # Keys under state["paths"] are named target/basic/spells/etc. and do
    # not necessarily end in "_path", so filtering by key name loses logs.
    if isinstance(value, str):
        if value.lower().endswith(".json"):
            yield value
        return

    if isinstance(value, dict):
        for child in value.values():
            yield from _walk_json_paths(child)
        return

    if isinstance(value, list):
        for child in value:
            yield from _walk_json_paths(child)


def _copy_sheet_results_to_cache(current_dir, cache_dir):
    current_dir = Path(current_dir)
    cache_dir = Path(cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)

    copied = {}
    if not current_dir.is_dir():
        return copied

    for source in current_dir.glob("sheet-result-*.json"):
        if not source.is_file():
            continue
        target = cache_dir / source.name
        shutil.copy2(source, target)
        copied[str(source.resolve())] = str(target.resolve())
    return copied


def _artifact_candidates(run_state):
    seen = set()
    ordered = []

    for value in _walk_json_paths(run_state):
        try:
            path = Path(value)
        except (TypeError, ValueError):
            continue
        try:
            key = str(path.resolve())
        except OSError:
            key = str(path)
        if key in seen:
            continue
        seen.add(key)
        ordered.append(path)

    return ordered


def previous_stage_report(current_dir, key):
    path = Path(current_dir) / FINAL_LOG_NAME
    payload = _read_json(path)
    if not isinstance(payload, dict):
        return {}

    history = _dict(payload.get("last_known_stage_reports"))
    report = history.get(str(key))
    if isinstance(report, dict):
        return deepcopy(report)

    reports = _dict(payload.get("reports"))
    report = reports.get(str(key))
    if isinstance(report, dict):
        return deepcopy(report)

    # Compatibility with the first single-log draft, which nested the
    # original full-run payload under "run".
    run = _dict(payload.get("run"))
    reports = _dict(run.get("reports"))
    report = reports.get(str(key))
    return deepcopy(report) if isinstance(report, dict) else {}


def _previous_history(current_dir):
    path = Path(current_dir) / FINAL_LOG_NAME
    payload = _read_json(path)
    if not isinstance(payload, dict):
        return {}
    return deepcopy(_dict(payload.get("last_known_stage_reports")))


def consolidate_current_results(*, data_root, run_state, run_id):
    """Write one final current log, then remove per-stage current JSONs."""
    from .result_store import write_json_atomic

    data_root = Path(data_root)
    current_dir = data_root / "results" / "current"
    cache_dir = data_root / "results" / "cache"
    current_dir.mkdir(parents=True, exist_ok=True)
    cache_dir.mkdir(parents=True, exist_ok=True)

    state = deepcopy(_dict(run_state))
    final_path = current_dir / FINAL_LOG_NAME

    previous_history = _previous_history(current_dir)
    last_known = previous_history
    for key, report in _dict(state.get("reports")).items():
        if isinstance(report, dict):
            last_known[str(key)] = deepcopy(report)

    artifacts = {}
    artifact_paths = {}
    for path in _artifact_candidates(state):
        payload = _read_json(path)
        if payload is None:
            continue
        name = path.name
        key = name
        index = 2
        while key in artifacts:
            key = f"{name}#{index}"
            index += 1
        artifacts[key] = payload
        try:
            artifact_paths[key] = str(path.resolve())
        except OSError:
            artifact_paths[key] = str(path)

    old_full_run_path = state.get("full_run_report")
    if isinstance(old_full_run_path, str) and old_full_run_path.endswith(".json"):
        path = Path(old_full_run_path)
        payload = _read_json(path)
        if payload is not None and path.name not in artifacts:
            artifacts[path.name] = payload
            try:
                artifact_paths[path.name] = str(path.resolve())
            except OSError:
                artifact_paths[path.name] = str(path)

    cache_copies = _copy_sheet_results_to_cache(current_dir, cache_dir)

    sheet_path_text = _dict(state.get("paths")).get("sheet_result")
    if isinstance(sheet_path_text, str) and sheet_path_text.endswith(".json"):
        sheet_path = Path(sheet_path_text)
        if sheet_path.name not in artifacts:
            payload = _read_json(sheet_path)
            if payload is not None:
                artifacts[sheet_path.name] = payload
                try:
                    artifact_paths[sheet_path.name] = str(sheet_path.resolve())
                except OSError:
                    artifact_paths[sheet_path.name] = str(sheet_path)

    state["full_run_report"] = str(final_path.resolve())

    # Keep the original full-run report shape at the top level so existing
    # worker/UI/report verification code can read status/error/reports/paths
    # exactly as before. The consolidation metadata is additive.
    consolidated = deepcopy(state)
    consolidated["run_id"] = str(
        run_id or state.get("run_id") or ""
    )
    consolidated["single_log_version"] = SINGLE_LOG_VERSION
    consolidated["last_known_stage_reports"] = last_known
    consolidated["artifacts"] = artifacts
    consolidated["artifact_original_paths"] = artifact_paths
    consolidated["sheet_result_cache_copies"] = cache_copies
    consolidated["current_directory_policy"] = {
        "one_final_json": True,
        "final_filename": FINAL_LOG_NAME,
        "per_stage_reports_embedded": True,
        "per_stage_backups_embedded": True,
        "sheet_results_embedded": True,
        "sheet_results_relocated_to": str(cache_dir.resolve()),
    }

    write_json_atomic(final_path, consolidated)

    cleanup_errors = []
    for path in list(current_dir.glob("*.json")):
        if path.name == FINAL_LOG_NAME:
            continue
        if not _is_generated_current_json(path):
            continue
        try:
            path.unlink(missing_ok=True)
        except OSError as exc:
            cleanup_errors.append(f"{path.name}: {exc}")

    if cleanup_errors:
        consolidated["cleanup_errors"] = cleanup_errors
        write_json_atomic(final_path, consolidated)

    return consolidated, final_path


def install_single_current_log(original_run_full_move):
    if getattr(original_run_full_move, "_sheetmover_single_current_log_v1", False):
        return original_run_full_move

    def run_full_move_single_log(
        source_url,
        settings,
        *,
        emit=None,
        run_id=None,
        update_existing=False,
    ):
        from .app_config import data_dir

        actual_emit = emit or (lambda _event: None)
        held_complete = None

        def proxy_emit(event):
            nonlocal held_complete
            if isinstance(event, dict) and event.get("type") == "complete":
                held_complete = dict(event)
                return
            actual_emit(event)

        try:
            if update_existing:
                state = original_run_full_move(
                    source_url,
                    settings,
                    emit=proxy_emit,
                    run_id=run_id,
                    update_existing=True,
                )
            else:
                state = original_run_full_move(
                    source_url,
                    settings,
                    emit=proxy_emit,
                    run_id=run_id,
                )
        except Exception as exc:
            failure_state = getattr(exc, "failure_report", None)
            if not isinstance(failure_state, dict):
                raise

            try:
                consolidated, final_path = consolidate_current_results(
                    data_root=data_dir(),
                    run_state=failure_state,
                    run_id=run_id or failure_state.get("run_id"),
                )
            except Exception as log_exc:
                exc.save_errors = list(getattr(exc, "save_errors", [])) + [
                    f"최종 통합 로그 저장 실패: {log_exc}"
                ]
                raise

            exc.failure_report = consolidated
            exc.failure_report_path = str(final_path.resolve())
            raise

        consolidated, final_path = consolidate_current_results(
            data_root=data_dir(),
            run_state=state,
            run_id=run_id or state.get("run_id"),
        )
        state["full_run_report"] = str(final_path.resolve())

        event = held_complete or {
            "type": "complete",
            "source_id": state.get("source_character_id"),
            "run_id": state.get("run_id"),
        }
        event["report"] = str(final_path.resolve())
        actual_emit(event)
        return state

    run_full_move_single_log._sheetmover_single_current_log_v1 = True
    return run_full_move_single_log
