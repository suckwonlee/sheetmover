"""D&D Beyond -> Roll20 orchestration with durable failure reports."""
from __future__ import annotations

import argparse
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime
import json
from pathlib import Path
import re
import sys
import time
import traceback
from uuid import uuid4

from .app_config import (
    AppSettings, apply_runtime_environment, check_google, check_ollama,
    data_dir, load_settings, validate_settings,
)

FULL_RUN_VERSION = "2026-10-07-stage13-full-run-v2.5.2-diagnostic-log"
STAGES = (
    "D&D Beyond 수집 · 번역 · 계산", "Roll20 대상 확인", "기본 능력치",
    "인벤토리", "주문", "특성", "무기 공격", "주문 공격", "숙련", "자원",
)


def _write_json(path: Path, payload: dict) -> Path:
    from .result_store import write_json_atomic
    return write_json_atomic(path, payload)


def _result_summary(payload):
    if not isinstance(payload, dict):
        return {}
    summary = payload.get("translation_summary")
    if isinstance(summary, dict):
        return summary
    translated = payload.get("translated")
    if isinstance(translated, dict):
        summary = translated.get("translation_summary")
        if isinstance(summary, dict):
            return summary
    return {}


def _stable_json(value):
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    except (TypeError, ValueError):
        return ""


def _find_reusable_result(source_id: str, raw_source: dict):
    # Reuse only a result produced from the exact same D&D Beyond raw payload.
    # results/current is reserved for one consolidated execution log.
    folders = [
        data_dir() / "results" / "cache",
        data_dir() / "results" / "current",
    ]

    wanted_raw = _stable_json(raw_source)
    if not wanted_raw:
        return None, None

    candidates = []
    for folder in folders:
        if not folder.is_dir():
            continue
        candidates.extend(
            folder.glob(f"sheet-result-{source_id}-*.json")
        )
    candidates = sorted(
        candidates,
        key=lambda path: path.name,
        reverse=True,
    )
    for path in candidates:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            continue
        if not isinstance(payload, dict):
            continue

        original = payload.get("original")
        if not isinstance(original, dict):
            continue
        if str(original.get("source_id") or "").strip() != str(source_id).strip():
            continue

        summary = _result_summary(payload)
        if str(summary.get("status") or "").strip() not in {"complete", "partial"}:
            continue
        if not isinstance(payload.get("roll20_payload"), dict):
            continue

        cached_raw = payload.get("raw_source")
        if not isinstance(cached_raw, dict):
            continue
        if _stable_json(cached_raw) != wanted_raw:
            continue
        return payload, path

    return None, None



def _refresh_roll20_views(cdp_url: str, timeout: float = 30.0):
    from urllib.parse import urlparse
    from .roll20_connection import (
        _attach_driver,
        _disconnect_driver,
        _ensure_cdp,
    )

    _ensure_cdp(cdp_url)
    driver = _attach_driver(cdp_url)
    refreshed = []
    editor_ready = False
    original_handle = getattr(driver, "current_window_handle", None)

    try:
        handles = list(driver.window_handles)
        for handle in handles:
            try:
                driver.switch_to.window(handle)
                url = str(driver.current_url or "")
                parsed = urlparse(url)
                if parsed.hostname != "app.roll20.net":
                    continue
                driver.refresh()
                refreshed.append(url)
            except Exception:
                continue

        if not refreshed:
            raise RuntimeError(
                "최종 동기화할 Roll20 탭을 찾지 못했습니다."
            )

        deadline = time.monotonic() + max(5.0, float(timeout))
        while time.monotonic() < deadline:
            for handle in list(driver.window_handles):
                try:
                    driver.switch_to.window(handle)
                    url = str(driver.current_url or "")
                    parsed = urlparse(url)
                    if (
                        parsed.hostname != "app.roll20.net"
                        or not parsed.path.startswith("/editor")
                    ):
                        continue
                    ready = str(
                        driver.execute_script(
                            "return document.readyState || '';"
                        )
                        or ""
                    )
                    campaign = bool(
                        driver.execute_script(
                            "return !!((window.d20 && window.d20.Campaign) "
                            "|| window.Campaign);"
                        )
                    )
                    if ready == "complete" and campaign:
                        editor_ready = True
                        break
                except Exception:
                    continue

            if editor_ready:
                break
            time.sleep(0.25)

        if not editor_ready:
            raise RuntimeError(
                "Roll20 저장은 끝났지만 최종 화면 재로딩 후 "
                "게임 준비 상태를 확인하지 못했습니다."
            )

        return {
            "status": "pass",
            "refreshed_view_count": len(refreshed),
            "editor_ready": True,
        }
    finally:
        if original_handle:
            try:
                if original_handle in list(driver.window_handles):
                    driver.switch_to.window(original_handle)
            except Exception:
                pass
        _disconnect_driver(driver)



_BATCH_PROGRESS_RE = re.compile(r"^번역 \d+/\d+ \(배치 처리\)$")
_NOISY_STDOUT_RE = re.compile(
    r"^\[시트 이동기\] (?:주문|특성|주문 공격) \d+/\d+:"
)


def _diagnostic_event_lines(event):
    if not isinstance(event, dict):
        return []

    kind = str(event.get("type") or "")
    message = str(event.get("message") or "").strip()

    if kind == "progress":
        if not message or _BATCH_PROGRESS_RE.match(message):
            return []
        return [f"[진행] {message}"]

    if kind == "stage":
        status_label = {
            "running": "시작",
            "pass": "완료",
            "error": "실패",
        }.get(
            str(event.get("status") or ""),
            str(event.get("status") or ""),
        )
        index = event.get("index")
        total = event.get("total")
        name = str(event.get("name") or "").strip() or "단계"
        prefix = (
            f"[단계 {index}/{total}]"
            if index and total
            else f"[단계 {index}]"
            if index
            else "[단계]"
        )
        line = f"{prefix} {name}: {status_label}".rstrip()
        if message:
            line += f" - {message}"
        return [line]

    if kind == "complete":
        return ["[완료] Roll20 시트 이동 완료"]

    if kind == "error":
        lines = [f"[오류] {message or '알 수 없는 오류'}"]
        trace = str(event.get("traceback") or "").strip()
        if trace:
            lines.append("[오류 상세]")
            lines.extend(trace.splitlines())
        report = str(event.get("report") or "").strip()
        if report:
            lines.append(f"[오류 보고서] {report}")
        for value in event.get("save_errors") or []:
            lines.append(f"[저장 오류] {value}")
        return lines

    return []


def _keep_runtime_stdout_line(line):
    value = str(line or "").strip()
    if not value:
        return False
    if _NOISY_STDOUT_RE.match(value):
        return False
    if value == "[시트 이동기] 특성 표시 순서를 정리합니다.":
        return False
    return True


class _DiagnosticLogStream:
    def __init__(self, sink):
        self.sink = sink
        self.buffer = ""

    def write(self, value):
        value = str(value)
        self.buffer += value
        while "\n" in self.buffer:
            line, self.buffer = self.buffer.split("\n", 1)
            if _keep_runtime_stdout_line(line):
                self.sink.write(line + "\n")
        return len(value)

    def flush(self):
        self.sink.flush()

    def finish(self):
        if self.buffer:
            if _keep_runtime_stdout_line(self.buffer):
                self.sink.write(self.buffer + "\n")
            self.buffer = ""
        self.sink.flush()


def _write_diagnostic_event(log, event):
    for line in _diagnostic_event_lines(event):
        log.write(line + "\n")
    log.flush()


def _emit_stdout(event: dict):
    if sys.stdout is None:
        raise RuntimeError("작업 통신 경로가 없습니다. GUI 작업은 --events가 필요합니다.")
    print(json.dumps(event, ensure_ascii=False), flush=True)


def run_full_move(
    source_url: str,
    settings: AppSettings,
    *,
    emit=None,
    run_id=None,
    update_existing=False,
):
    emit = emit or (lambda _event: None)
    run_id = run_id or uuid4().hex
    update_existing = bool(update_existing)
    report_path = data_dir() / "results" / "current" / f"full-run-{run_id}.json"
    state = {
        "version": FULL_RUN_VERSION, "run_id": run_id, "status": "running",
        "source_url": source_url, "source_character_id": "", "character_name": "",
        "started_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "run_mode": "update_existing" if update_existing else "initial_move",
        "paths": {}, "stages": list(STAGES), "reports": {},
        "stage_statuses": {str(i): "pending" for i in range(1, len(STAGES) + 1)},
        "active_stage": None, "full_run_report": str(report_path.resolve()),
    }
    payload = None
    previous_result_path = None

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
        # Import stages only after applying the worker's settings snapshot.
        from .mover import run as prepare
        from .result_store import default_result_path
        from .roll20_connection import check_roll20_target
        from .source import fetch_character
        from .roll20_inventory import apply_inventory
        from .roll20_spells import apply_spells
        from .roll20_features import apply_features
        from .roll20_attacks import apply_attacks
        from .roll20_spell_attacks import apply_spell_attacks
        from .roll20_proficiencies import apply_proficiencies
        from .roll20_resources import apply_resources
        from stage5_basic_writer_v3 import run as apply_basic

        # Do not spend translation work until the actual Roll20 destination is ready.
        stage(2, "running", "번역 전에 Roll20 대상과 시트 상태를 확인합니다.")
        overall(1, "D&D Beyond 캐릭터 ID와 이름만 먼저 확인합니다.")
        identity_raw = fetch_character(source_url)
        source_id = str(identity_raw.get("id") or "").strip()
        character_name = str(identity_raw.get("name") or "").strip()
        if not source_id or not character_name:
            raise RuntimeError("D&D Beyond 원본에서 캐릭터 ID/이름을 확인하지 못했습니다.")

        state["source_character_id"] = source_id
        state["character_name"] = character_name

        preflight_path = data_dir() / "workers" / run_id / "roll20-preflight.json"
        _write_json(
            preflight_path,
            {
                "original": {"source_id": source_id, "name": character_name},
                "roll20_payload": {
                    "source_character_id": source_id,
                    "character": {"name": character_name},
                },
            },
        )
        try:
            target, target_path = check_roll20_target(
                result_path=preflight_path,
                source_id=source_id,
                cdp_url=settings.roll20_cdp_url,
                save=True,
            )
        finally:
            preflight_path.unlink(missing_ok=True)

        sheet_type = str(getattr(target, "sheet_type", "") or "").strip()
        if sheet_type != "ogl5e":
            raise RuntimeError(
                "Roll20 대상 캐릭터가 Legacy OGL5e 시트가 아닙니다. "
                f"확인된 시트 유형: {sheet_type or '미확인'}"
            )

        state["reports"]["target"] = (
            target.__dict__ if hasattr(target, "__dict__") else str(target)
        )
        state["paths"]["target"] = str(Path(target_path).resolve())
        stage(2, "pass", "Roll20 게임 탭 · 대상 캐릭터 · Legacy OGL5e 시트 확인 완료")

        if update_existing:
            from .update_mode import prepare_update_context

            update_context = prepare_update_context(
                identity_raw,
                data_root=data_dir(),
            )
            state["update_mode"] = update_context
            previous_result_path = update_context["previous_result_path"]
            classification = str(update_context.get("classification") or "")
            level_delta = int(update_context.get("level_delta") or 0)
            if classification == "probable_level_up":
                overall(
                    5,
                    "기존 결과 비교 완료 · "
                    f"레벨 {update_context.get('previous_total_level')} → "
                    f"{update_context.get('current_total_level')} "
                    f"(+{level_delta}) · 변경사항 업데이트 준비",
                )
            else:
                overall(
                    5,
                    "기존 결과 비교 완료 · "
                    f"변경 구역 {len(update_context.get('changed_sections') or [])}개 · "
                    "변경사항 업데이트 준비",
                )
        else:
            state["update_mode"] = {
                "enabled": False,
                "classification": "initial_move",
            }
            overall(5, "Roll20 준비 완료. 기존 번역 결과를 확인합니다.")

        payload, reusable_path = _find_reusable_result(source_id, identity_raw)
        if payload is not None and reusable_path is not None:
            original = payload.get("original") or {}
            if str(original.get("name") or "").strip() != character_name:
                payload = None
                reusable_path = None

        if payload is not None and reusable_path is not None:
            result_path = Path(reusable_path)
            summary = _result_summary(payload)
            translation_status = str(summary.get("status") or "").strip()
            preserved_count = int(summary.get("original_preserved_count") or 0)
            state["translation_summary"] = summary
            state["paths"]["sheet_result"] = str(result_path.resolve())
            if translation_status == "partial":
                stage(
                    1,
                    "pass",
                    f"기존 번역 결과 재사용: {result_path.name} · "
                    f"원문 유지 {preserved_count}개",
                )
                overall(
                    30,
                    f"기존 번역 재사용 완료 · API 번역 생략 · 원문 유지 {preserved_count}개",
                )
            else:
                stage(1, "pass", f"기존 번역 결과 재사용: {result_path.name}")
                overall(30, "기존 번역 재사용 완료 · API 번역 생략")
        else:
            stage(1, "running", "Roll20 준비 완료. 번역 서비스 상태를 확인합니다.")
            overall(5, "재사용 가능한 번역이 없습니다. 번역 서비스 상태를 확인합니다.")
            for name, check in (("Google", check_google), ("Ollama", check_ollama)):
                result = check(settings)
                if not result.get("ok"):
                    raise RuntimeError(
                        f"{name}: {result.get('message', '설정 오류')} "
                        f"{result.get('detail') or ''}".strip()
                    )

            if update_existing:
                prepared = prepare(
                    source_url,
                    cdp_url=settings.roll20_cdp_url,
                    on_progress=lambda p, m: overall(5 + float(p) * .25, m),
                    raw_source=identity_raw,
                    translation_seed_path=previous_result_path,
                )
            else:
                prepared = prepare(
                    source_url,
                    cdp_url=settings.roll20_cdp_url,
                    on_progress=lambda p, m: overall(5 + float(p) * .25, m),
                    raw_source=identity_raw,
                )
            payload = prepared.to_dict()
            original = payload.get("original") or {}
            prepared_source_id = str(original.get("source_id") or "").strip()
            prepared_name = str(original.get("name") or "").strip()
            if prepared_source_id != source_id or prepared_name != character_name:
                raise RuntimeError(
                    "사전 확인한 D&D Beyond 캐릭터와 번역 대상이 달라졌습니다. "
                    "Roll20 입력을 중단합니다."
                )

            summary = payload.get("translation_summary") or {}
            translation_status = str(summary.get("status") or "").strip()
            preserved_count = int(summary.get("original_preserved_count") or 0)
            state["translation_summary"] = summary
            if translation_status not in {"complete", "partial"}:
                raise RuntimeError(
                    "번역 결과 상태를 확인할 수 없어 Roll20 입력을 중단합니다. "
                    f"상태={translation_status or '없음'}"
                )

            result_path = default_result_path(source_id, root=data_dir())
            _write_json(result_path, payload)
            state["paths"]["sheet_result"] = str(result_path.resolve())

            if translation_status == "partial":
                stage(
                    1,
                    "pass",
                    f"원문 {preserved_count}개를 안전하게 유지하고 계속 진행합니다. "
                    f"결과 저장: {result_path.name}",
                )
                overall(30, f"D&D Beyond 준비 완료 · 원문 유지 {preserved_count}개")
            else:
                stage(1, "pass", f"결과 저장: {result_path.name}")
                overall(30, "D&D Beyond 준비 완료")

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
        overall(99, "Roll20 화면을 저장된 서버 상태로 다시 불러옵니다.")
        state["roll20_ui_refresh"] = _refresh_roll20_views(
            settings.roll20_cdp_url
        )
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
    parser.add_argument("--update-existing", action="store_true", help="이전 시트 이동 결과와 비교해 기존 캐릭터 변경사항을 업데이트합니다.")
    args = parser.parse_args(argv)
    if args.events and (not args.log or not args.run_id):
        parser.error("--events requires --log and --run-id")

    def execute(emit):
        try:
            if args.update_existing:
                run_full_move(
                    args.source,
                    load_settings(args.settings),
                    emit=emit,
                    run_id=args.run_id,
                    update_existing=True,
                )
            else:
                run_full_move(
                    args.source,
                    load_settings(args.settings),
                    emit=emit,
                    run_id=args.run_id,
                )
            return 0
        except Exception as exc:
            emit({"type": "error", "message": str(exc),
                  "traceback": traceback.format_exc(),
                  "report": getattr(exc, "failure_report_path", None),
                  "save_errors": getattr(exc, "save_errors", [])})
            return 1

    if args.events:
        from .worker_protocol import EventWriter
        writer = EventWriter(args.events, args.run_id)
        log_path = Path(args.log)
        log_path.parent.mkdir(parents=True, exist_ok=True)

        with log_path.open("w", encoding="utf-8", buffering=1) as raw_log:
            stream = _DiagnosticLogStream(raw_log)

            def emit(event):
                writer.emit(event)
                _write_diagnostic_event(raw_log, event)

            raw_log.write(
                f"[실행] 시작 · run_id={args.run_id or '없음'}\n"
            )
            try:
                with redirect_stdout(stream), redirect_stderr(stream):
                    code = execute(emit)
            finally:
                stream.finish()

            raw_log.write(f"[실행] 종료 · exit_code={code}\n")
            raw_log.flush()
            return code

    return execute(_emit_stdout)


# runtime-integrity-v2.6.2 cache-refresh hook
from .runtime_integrity_v262 import install_reusable_result_refresh as _install_reusable_result_refresh_v262
_find_reusable_result = _install_reusable_result_refresh_v262(_find_reusable_result)

# runtime-integrity-v2.6.4 subclass-spell cache-refresh hook
from .runtime_integrity_v264 import install_reusable_result_refresh as _install_reusable_result_refresh_v264
_find_reusable_result = _install_reusable_result_refresh_v264(_find_reusable_result)

# single-current-log-v1
from .run_log import install_single_current_log as _install_single_current_log_v1
run_full_move = _install_single_current_log_v1(run_full_move)

if __name__ == "__main__":
    raise SystemExit(cli_main())
