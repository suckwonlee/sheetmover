"""D&D Beyond -> Roll20 orchestration with durable failure reports."""
from __future__ import annotations

import argparse
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime
import json
from pathlib import Path
import sys
import traceback
from uuid import uuid4

from .app_config import (
    AppSettings, apply_runtime_environment, check_google, check_ollama,
    data_dir, load_settings, validate_settings,
)

FULL_RUN_VERSION = "2026-10-06-stage13-full-run-v2"
STAGES = (
    "D&D Beyond 수집 · 번역 · 계산", "Roll20 대상 확인", "기본 능력치",
    "인벤토리", "주문", "특성", "무기 공격", "주문 공격", "숙련", "자원",
)


def _write_json(path: Path, payload: dict) -> Path:
    from .result_store import write_json_atomic
    return write_json_atomic(path, payload)


def _emit_stdout(event: dict):
    if sys.stdout is None:
        raise RuntimeError("작업 통신 경로가 없습니다. GUI 작업은 --events가 필요합니다.")
    print(json.dumps(event, ensure_ascii=False), flush=True)


def run_full_move(source_url: str, settings: AppSettings, *, emit=None, run_id=None):
    emit = emit or (lambda _event: None)
    run_id = run_id or uuid4().hex
    report_path = data_dir() / "results" / "current" / f"full-run-{run_id}.json"
    state = {
        "version": FULL_RUN_VERSION, "run_id": run_id, "status": "running",
        "source_url": source_url, "source_character_id": "", "character_name": "",
        "started_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "paths": {}, "stages": list(STAGES), "reports": {},
        "stage_statuses": {str(i): "pending" for i in range(1, len(STAGES) + 1)},
        "active_stage": None, "full_run_report": str(report_path.resolve()),
    }
    payload = None

    def checkpoint():
        _write_json(report_path, state)

    def stage(index, status, message=""):
        state["active_stage"] = index
        state["stage_statuses"][str(index)] = status
        checkpoint()
        emit({"type": "stage", "index": index, "total": len(STAGES),
              "name": STAGES[index - 1], "status": status, "message": message})

    def overall(value, message):
        emit({"type": "progress", "percent": round(float(value), 1), "message": message})

    def run_stage(index, key, func, **kwargs):
        stage(index, "running")
        overall(30 + ((index - 2) / (len(STAGES) - 1)) * 70,
                f"{STAGES[index - 1]} 진행 중")
        try:
            report, path = func(**kwargs)
        except Exception as exc:
            if hasattr(exc, "stage_report"):
                state["reports"][key] = exc.stage_report
                state["paths"][key] = exc.stage_report_path
            raise
        state["reports"][key] = report
        state["paths"][key] = str(Path(path).resolve())
        if not isinstance(report, dict) or report.get("status") != "pass":
            raise RuntimeError(f"{STAGES[index - 1]} 완료 검증을 통과하지 못했습니다.")
        stage(index, "pass")

    try:
        settings = apply_runtime_environment(settings)
        checkpoint()
        problems = validate_settings(settings)
        if problems:
            raise RuntimeError(" / ".join(problems))
        for name, check in (("Google", check_google), ("Ollama", check_ollama)):
            result = check(settings)
            if not result.get("ok"):
                raise RuntimeError(f"{name}: {result.get('message', '설정 오류')} "
                                   f"{result.get('detail') or ''}".strip())

        # Import stages only after applying the worker's settings snapshot.
        from .mover import run as prepare
        from .result_store import default_result_path
        from .roll20_connection import check_roll20_target
        from .roll20_inventory import apply_inventory
        from .roll20_spells import apply_spells
        from .roll20_features import apply_features
        from .roll20_attacks import apply_attacks
        from .roll20_spell_attacks import apply_spell_attacks
        from .roll20_proficiencies import apply_proficiencies
        from .roll20_resources import apply_resources
        from stage5_basic_writer_v3 import run as apply_basic

        stage(1, "running")
        prepared = prepare(source_url, cdp_url=settings.roll20_cdp_url,
                           on_progress=lambda p, m: overall(float(p) * .30, m))
        payload = prepared.to_dict()
        original = payload.get("original") or {}
        source_id = str(original.get("source_id") or "").strip()
        state["source_character_id"] = source_id
        state["character_name"] = original.get("name") or ""
        if not source_id:
            raise RuntimeError("D&D Beyond source ID를 확인하지 못했습니다.")
        summary = payload.get("translation_summary") or {}
        if summary.get("status") != "complete":
            raise RuntimeError("번역이 미완성이므로 Roll20 입력을 중단합니다. "
                               f"원문 유지 {summary.get('original_preserved_count', 0)}개")
        result_path = default_result_path(source_id, root=data_dir())
        _write_json(result_path, payload)
        state["paths"]["sheet_result"] = str(result_path.resolve())
        stage(1, "pass", f"결과 저장: {result_path.name}")
        overall(30, "D&D Beyond 준비 완료")

        stage(2, "running")
        target, target_path = check_roll20_target(
            result_path=result_path, source_id=source_id,
            cdp_url=settings.roll20_cdp_url, save=True)
        state["reports"]["target"] = (
            target.__dict__ if hasattr(target, "__dict__") else str(target))
        state["paths"]["target"] = str(Path(target_path).resolve())
        stage(2, "pass")
        run_stage(3, "basic", apply_basic, source_id=source_id,
                  result_path=result_path, cdp_url=settings.roll20_cdp_url, dry_run=False)
        for index, key, func in (
            (4, "inventory", apply_inventory), (5, "spells", apply_spells),
            (6, "features", apply_features), (7, "weapon_attacks", apply_attacks),
            (8, "spell_attacks", apply_spell_attacks),
            (9, "proficiencies", apply_proficiencies), (10, "resources", apply_resources),
        ):
            run_stage(index, key, func, result_path=result_path, source_id=source_id,
                      cdp_url=settings.roll20_cdp_url, dry_run=False)
        state["status"] = "pass"
        state["active_stage"] = None
        state["finished_at"] = datetime.now().astimezone().isoformat(timespec="seconds")
        checkpoint()
        emit({"type": "complete", "report": state["full_run_report"],
              "source_id": source_id, "run_id": run_id})
        return state
    except Exception as exc:
        state["status"] = "error"
        index = state["active_stage"]
        if index:
            state["stage_statuses"][str(index)] = "error"
        state["error"] = {"message": str(exc),
                          "code": getattr(exc, "code", "run_failed")}
        state["finished_at"] = datetime.now().astimezone().isoformat(timespec="seconds")
        save_errors = list(getattr(exc, "save_errors", []))
        partial = getattr(exc, "partial_payload", None)
        if partial is None and payload is not None and not state["paths"].get("sheet_result"):
            partial = payload
        if isinstance(partial, dict):
            partial = dict(partial)
            partial["applied"] = False
            partial["full_run_error"] = state["error"]
            partial_path = report_path.parent / f"sheet-partial-{run_id}.json"
            try:
                _write_json(partial_path, partial)
                state["paths"]["partial_result"] = str(partial_path.resolve())
            except Exception as save_exc:
                save_errors.append(f"부분 결과 저장 실패: {save_exc}")
        try:
            state["save_errors"] = save_errors
            checkpoint()
        except Exception as save_exc:
            save_errors.append(f"실패 보고서 저장 실패: {save_exc}")
        exc.failure_report = state
        exc.failure_report_path = str(report_path.resolve())
        exc.save_errors = save_errors
        if index:
            emit({"type": "stage", "index": index, "name": STAGES[index - 1],
                  "status": "error", "message": str(exc)})
        raise


def cli_main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", required=True)
    parser.add_argument("--settings")
    parser.add_argument("--events")
    parser.add_argument("--log")
    parser.add_argument("--run-id")
    args = parser.parse_args(argv)
    if args.events and (not args.log or not args.run_id):
        parser.error("--events requires --log and --run-id")

    def execute(emit):
        try:
            run_full_move(args.source, load_settings(args.settings),
                          emit=emit, run_id=args.run_id)
            return 0
        except Exception as exc:
            emit({"type": "error", "message": str(exc),
                  "traceback": traceback.format_exc(),
                  "report": getattr(exc, "failure_report_path", None),
                  "paths": getattr(exc, "failure_report", {}).get("paths", {}),
                  "save_errors": getattr(exc, "save_errors", [])})
            return 1

    if args.events:
        from .worker_protocol import EventWriter
        writer = EventWriter(args.events, args.run_id)
        with Path(args.log).open("w", encoding="utf-8", buffering=1) as log:
            with redirect_stdout(log), redirect_stderr(log):
                return execute(writer.emit)
    return execute(_emit_stdout)


if __name__ == "__main__":
    raise SystemExit(cli_main())
