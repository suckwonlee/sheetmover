# -*- coding: utf-8 -*-
from __future__ import annotations

import ast
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile

ROOT = Path.cwd()
RESOURCES = ROOT / "sheet_mover" / "roll20_resources.py"
FULL_RUN = ROOT / "sheet_mover" / "full_run.py"
UI = ROOT / "sheet_mover" / "ui.py"

BASE_COMMIT = "c0d3905842da144121a3922ef68fdccf6f30b0c6"


def replace_once(text, old, new, label):
    count = text.count(old)
    if count != 1:
        raise RuntimeError(
            f"{label}: 예상 코드 1개가 필요한데 {count}개를 찾았습니다."
        )
    return text.replace(old, new, 1)


def compile_text(path, text):
    try:
        ast.parse(text, filename=str(path))
    except SyntaxError as exc:
        raise RuntimeError(f"Python 구문 검증 실패: {path}: {exc}") from exc


def backup(path):
    dst = path.with_suffix(path.suffix + ".pre-runtime-v2.5.bak")
    if not dst.exists():
        shutil.copy2(path, dst)


def atomic_write(path, text):
    temp = path.with_name(path.name + ".runtime-v25.tmp")
    temp.write_text(text, encoding="utf-8")
    os.replace(temp, path)


def patch_resources(text):
    if "stage12-roll20-resources-v1.3-ability-modifier-uses" in text:
        return text

    text = replace_once(
        text,
        'STAGE12_VERSION = "2026-10-06-stage12-roll20-resources-v1.2-rest-reset"',
        'STAGE12_VERSION = "2026-10-07-stage12-roll20-resources-v1.3-ability-modifier-uses"',
        "Stage 12 version",
    )

    old_max = '''def _max_uses(limited_use, proficiency_bonus):
    limited_use = _dict(limited_use)
    raw_max = limited_use.get("maxUses")

    if raw_max not in (None, ""):
        maximum = _as_int(raw_max, 0)
        if maximum > 0:
            return maximum

    # Some DDB limited-use definitions are proficiency-bonus based and may
    # omit maxUses. Do not guess operator arithmetic when DDB already supplied
    # a concrete maxUses; only use PB as the safe fallback when maxUses is
    # absent.
    if limited_use.get("useProficiencyBonus") is True:
        return max(0, _as_int(proficiency_bonus, 0))

    return 0
'''

    new_max = '''STAT_ID_TO_ABILITY = {
    1: "strength",
    2: "dexterity",
    3: "constitution",
    4: "intelligence",
    5: "wisdom",
    6: "charisma",
}


def _ability_modifier(score):
    score = _as_int(score, 10)
    return (score - 10) // 2


def _max_uses(limited_use, proficiency_bonus, ability_scores=None):
    limited_use = _dict(limited_use)
    ability_scores = _dict(ability_scores)
    raw_max = limited_use.get("maxUses")

    if raw_max not in (None, ""):
        maximum = _as_int(raw_max, 0)
        if maximum > 0:
            return maximum

    stat_id = _as_int(limited_use.get("statModifierUsesId"), 0)
    ability = STAT_ID_TO_ABILITY.get(stat_id)
    if ability and ability in ability_scores:
        return max(1, _ability_modifier(ability_scores.get(ability)))

    if limited_use.get("useProficiencyBonus") is True:
        return max(0, _as_int(proficiency_bonus, 0))

    return 0
'''
    text = replace_once(text, old_max, new_max, "Stage 12 max uses")

    old_header = '''def build_resource_candidates(result_payload: dict[str, Any]):
    roll20_payload = _dict(_dict(result_payload).get("roll20_payload"))
    character = _dict(roll20_payload.get("character"))
    proficiency_bonus = _as_int(character.get("proficiency_bonus"), 0)

    candidates = []
    seen = set()
'''
    new_header = '''def build_resource_candidates(result_payload: dict[str, Any]):
    roll20_payload = _dict(_dict(result_payload).get("roll20_payload"))
    character = _dict(roll20_payload.get("character"))
    proficiency_bonus = _as_int(character.get("proficiency_bonus"), 0)
    ability_scores = _dict(character.get("ability_scores"))

    candidates = []
    seen = set()
    semantic_seen = set()
'''
    text = replace_once(text, old_header, new_header, "Stage 12 candidate header")

    text = replace_once(
        text,
        '        maximum = _max_uses(limited, proficiency_bonus)\n',
        '        maximum = _max_uses(limited, proficiency_bonus, ability_scores)\n',
        "Stage 12 max uses call",
    )

    old_after_name = '''        name = _text(item.get("name") or item.get("original_name"))
        if not name:
            raise RuntimeError(f"자원 이름이 비어 있습니다: {source_key}")

        candidates.append({
'''
    new_after_name = '''        name = _text(item.get("name") or item.get("original_name"))
        if not name:
            raise RuntimeError(f"자원 이름이 비어 있습니다: {source_key}")

        semantic_key = (
            _text(item.get("kind")).casefold(),
            _text(item.get("original_name") or name).casefold(),
            maximum,
            used,
            limited.get("resetType"),
            limited.get("statModifierUsesId"),
            limited.get("useProficiencyBonus"),
            limited.get("proficiencyBonusOperator"),
            limited.get("operator"),
        )
        if semantic_key in semantic_seen:
            continue
        semantic_seen.add(semantic_key)

        candidates.append({
'''
    text = replace_once(text, old_after_name, new_after_name, "Stage 12 semantic dedupe")

    old_candidates = '''    candidates = build_resource_candidates(payload)
    output_path = (
'''
    new_candidates = '''    candidates = build_resource_candidates(payload)
    if candidates:
        summary = ", ".join(
            f"{row['name']} {row['current']}/{row['maximum']} "
            f"({row.get('roll20_reset') or '휴식 없음'})"
            for row in candidates
        )
        print(f"[시트 이동기] 자원 후보 {len(candidates)}개: {summary}")
    else:
        print("[시트 이동기] 자원 후보 0개")

    output_path = (
'''
    text = replace_once(text, old_candidates, new_candidates, "Stage 12 concise resource log")

    return text


def patch_full_run(text):
    if "stage13-full-run-v2.5-diagnostic-log" in text:
        return text

    text = replace_once(
        text,
        'FULL_RUN_VERSION = "2026-10-07-stage13-full-run-v2.3-roll20-preflight-cache"',
        'FULL_RUN_VERSION = "2026-10-07-stage13-full-run-v2.5-diagnostic-log"',
        "full-run version",
    )

    if "\nimport re\n" not in text:
        text = replace_once(
            text,
            "from pathlib import Path\n",
            "from pathlib import Path\nimport re\n",
            "full-run re import",
        )

    marker = "\ndef _emit_stdout(event: dict):\n"
    if marker not in text:
        raise RuntimeError("full_run.py의 _emit_stdout 위치를 찾지 못했습니다.")

    helpers = r'''
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
        }.get(str(event.get("status") or ""), str(event.get("status") or ""))
        index = event.get("index")
        total = event.get("total")
        name = str(event.get("name") or "").strip() or "단계"
        prefix = (
            f"[단계 {index}/{total}]"
            if index and total
            else f"[단계 {index}]" if index else "[단계]"
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


'''
    text = text.replace(
        marker,
        "\n" + helpers + "def _emit_stdout(event: dict):\n",
        1,
    )

    start = '    if args.events:\n'
    end = '    return execute(_emit_stdout)\n'
    start_index = text.find(start)
    if start_index < 0:
        raise RuntimeError("full_run.py의 worker events 블록을 찾지 못했습니다.")
    end_index = text.find(end, start_index)
    if end_index < 0:
        raise RuntimeError("full_run.py의 worker events 블록 끝을 찾지 못했습니다.")
    end_index += len(end)

    new_worker_block = '''    if args.events:
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
                f"[실행] 시작 · run_id={args.run_id or '없음'}\\n"
            )
            try:
                with redirect_stdout(stream), redirect_stderr(stream):
                    code = execute(emit)
            finally:
                stream.finish()

            raw_log.write(
                f"[실행] 종료 · exit_code={code}\\n"
            )
            raw_log.flush()
            return code

    return execute(_emit_stdout)
'''
    text = text[:start_index] + new_worker_block + text[end_index:]
    return text


def patch_ui(text):
    if "def _cleanup_old_worker_protocol_files" not in text:
        marker = "\n\nclass SheetMoverUI(tk.Tk):\n"
        if marker not in text:
            raise RuntimeError("ui.py의 SheetMoverUI 위치를 찾지 못했습니다.")

        helpers = r'''

def _cleanup_old_worker_protocol_files(workers_root):
    root = Path(workers_root)
    if not root.is_dir():
        return 0

    removed = 0
    for folder in root.iterdir():
        if not folder.is_dir():
            continue
        for name in ("events.jsonl", "settings.json"):
            path = folder / name
            try:
                if path.is_file():
                    path.unlink()
                    removed += 1
            except OSError:
                pass
    return removed


def _ui_progress_is_noisy(message):
    value = str(message or "").strip()
    return bool(
        value.startswith("번역 ")
        and value.endswith("(배치 처리)")
        and "/" in value
    )
'''
        text = text.replace(
            marker,
            helpers + "\n\nclass SheetMoverUI(tk.Tk):\n",
            1,
        )

    old_run = '''        run_id = uuid4().hex
        run_folder = data_dir() / "workers" / run_id
'''
    new_run = '''        workers_root = data_dir() / "workers"
        removed_ipc = _cleanup_old_worker_protocol_files(workers_root)
        if removed_ipc:
            self.log(
                f"이전 실행의 임시 IPC 파일 {removed_ipc}개를 정리했습니다."
            )

        run_id = uuid4().hex
        run_folder = workers_root / run_id
'''
    text = replace_once(text, old_run, new_run, "UI old IPC cleanup")

    old_finally = '''            finally:
                # events/settings are IPC files, not retained user logs.
                for transient in (event_path, snapshot_path):
                    try:
                        Path(transient).unlink(missing_ok=True)
                    except OSError:
                        pass

        threading.Thread(target=worker, daemon=True).start()
'''
    new_finally = '''            finally:
                # The worker folder retains exactly one user-facing log:
                # run.log. Everything else here is transient IPC/runtime data.
                try:
                    for transient in list(run_folder.iterdir()):
                        try:
                            if transient.resolve() == log_path.resolve():
                                continue
                            if transient.is_dir():
                                shutil.rmtree(transient, ignore_errors=True)
                            else:
                                transient.unlink(missing_ok=True)
                        except OSError:
                            pass
                except OSError:
                    pass

        threading.Thread(target=worker, daemon=True).start()
'''
    text = replace_once(text, old_finally, new_finally, "UI run folder one-log cleanup")

    old_progress = '''        if kind == "progress":
            self.progress_var.set(min(99, max(0, float(event.get("percent") or 0))))
            self.progress_text.set(str(event.get("message") or ""))
            self.log(event.get("message") or "")
'''
    new_progress = '''        if kind == "progress":
            self.progress_var.set(min(99, max(0, float(event.get("percent") or 0))))
            message = str(event.get("message") or "")
            self.progress_text.set(message)
            if not _ui_progress_is_noisy(message):
                self.log(message)
'''
    text = replace_once(text, old_progress, new_progress, "UI progress noise filter")

    old_complete = '''        elif kind == "complete":
            self.progress_text.set("완료 보고서 확인 중")
            self.log(f"완료 보고서: {event.get('report')}")
'''
    new_complete = '''        elif kind == "complete":
            self.progress_text.set("완료 보고서 확인 중")
            self.log("완료 보고서 검증 통과")
'''
    text = replace_once(text, old_complete, new_complete, "UI complete compact log")

    old_error = '''        elif kind == "error":
            self.progress_text.set("실패")
            self.log("오류: " + str(event.get("message") or ""))
            trace = event.get("traceback")
            if trace:
                self.log(trace)
            if event.get("report"):
                self.log(f"실패 기록: {event['report']}")
            for key, path in (event.get("paths") or {}).items():
                self.log(f"저장된 결과 [{key}]: {path}")
            for error in event.get("save_errors") or []:
                self.log(error)
'''
    new_error = '''        elif kind == "error":
            self.progress_text.set("실패")
            self.log("오류: " + str(event.get("message") or ""))
            trace = event.get("traceback")
            if trace:
                self.log(trace)
            for error in event.get("save_errors") or []:
                self.log("저장 오류: " + str(error))
'''
    text = replace_once(text, old_error, new_error, "UI error diagnostic log")

    return text


def generated_tests():
    resource_test = r'''import unittest

from sheet_mover.roll20_resources import build_resource_candidates


class Stage12AbilityModifierResourceV25Tests(unittest.TestCase):
    def test_bardic_inspiration_uses_charisma_modifier_and_dedupes(self):
        bardic = {
            "kind": "action:class",
            "name": "음유시인의 영감 (Bardic Inspiration)",
            "original_name": "Bardic Inspiration",
            "limited_use": {
                "statModifierUsesId": 6,
                "resetType": 1,
                "numberUsed": 0,
                "maxUses": 0,
                "operator": 1,
                "useProficiencyBonus": False,
                "proficiencyBonusOperator": 1,
            },
        }
        first = dict(bardic, source_id="9414002")
        second = dict(bardic, source_id="9414006")

        payload = {
            "translated": {"resources": [first, second]},
            "roll20_payload": {
                "source_character_id": "153714540",
                "character": {
                    "name": "견본2",
                    "proficiency_bonus": 3,
                    "ability_scores": {
                        "strength": 9,
                        "dexterity": 14,
                        "constitution": 16,
                        "intelligence": 8,
                        "wisdom": 14,
                        "charisma": 14,
                    },
                },
            },
        }

        rows = build_resource_candidates(payload)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["original_name"], "Bardic Inspiration")
        self.assertEqual(rows[0]["current"], 2)
        self.assertEqual(rows[0]["maximum"], 2)
        self.assertEqual(rows[0]["roll20_reset"], "short")

    def test_positive_concrete_max_uses_still_wins(self):
        payload = {
            "translated": {
                "resources": [{
                    "source_id": "fixed",
                    "kind": "action:class",
                    "name": "고정 자원",
                    "original_name": "Fixed Resource",
                    "limited_use": {
                        "statModifierUsesId": 6,
                        "resetType": 2,
                        "numberUsed": 1,
                        "maxUses": 4,
                        "useProficiencyBonus": False,
                    },
                }]
            },
            "roll20_payload": {
                "source_character_id": "x",
                "character": {
                    "name": "fixture",
                    "proficiency_bonus": 3,
                    "ability_scores": {"charisma": 14},
                },
            },
        }
        rows = build_resource_candidates(payload)
        self.assertEqual(rows[0]["maximum"], 4)
        self.assertEqual(rows[0]["current"], 3)


if __name__ == "__main__":
    unittest.main()
'''

    log_test = r'''import io
import unittest

from sheet_mover.full_run import (
    _DiagnosticLogStream,
    _diagnostic_event_lines,
)


class DiagnosticRunLogV25Tests(unittest.TestCase):
    def test_translation_batch_spam_is_not_mirrored(self):
        lines = _diagnostic_event_lines({
            "type": "progress",
            "message": "번역 33/155 (배치 처리)",
        })
        self.assertEqual(lines, [])

    def test_stage_and_error_diagnostics_are_kept(self):
        stage = _diagnostic_event_lines({
            "type": "stage",
            "index": 10,
            "total": 10,
            "name": "자원",
            "status": "error",
            "message": "resource failed",
        })
        self.assertTrue(any("자원" in line and "실패" in line for line in stage))

        error = _diagnostic_event_lines({
            "type": "error",
            "message": "resource failed",
            "traceback": "Traceback line 1\nTraceback line 2",
            "report": "C:/report.json",
            "save_errors": ["save failed"],
        })
        joined = "\n".join(error)
        self.assertIn("resource failed", joined)
        self.assertIn("Traceback line 2", joined)
        self.assertIn("C:/report.json", joined)
        self.assertIn("save failed", joined)

    def test_repetitive_writer_rows_are_filtered_but_summary_stays(self):
        sink = io.StringIO()
        stream = _DiagnosticLogStream(sink)
        stream.write("[시트 이동기] 주문 1/21: Shield\n")
        stream.write("[시트 이동기] 특성 1/21: Feature\n")
        stream.write("[시트 이동기] 자원 후보 1개: Bardic 2/2 (short)\n")
        stream.finish()

        value = sink.getvalue()
        self.assertNotIn("주문 1/21", value)
        self.assertNotIn("특성 1/21", value)
        self.assertIn("Bardic 2/2", value)


if __name__ == "__main__":
    unittest.main()
'''

    ui_test = r'''import tempfile
import unittest
from pathlib import Path

from sheet_mover.ui import (
    _cleanup_old_worker_protocol_files,
    _ui_progress_is_noisy,
)


class SingleRunLogV25Tests(unittest.TestCase):
    def test_cleanup_removes_stale_ipc_but_keeps_run_log(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            old = root / "old-run"
            old.mkdir()
            (old / "events.jsonl").write_text("x", encoding="utf-8")
            (old / "settings.json").write_text("x", encoding="utf-8")
            (old / "run.log").write_text("keep", encoding="utf-8")

            removed = _cleanup_old_worker_protocol_files(root)

            self.assertEqual(removed, 2)
            self.assertFalse((old / "events.jsonl").exists())
            self.assertFalse((old / "settings.json").exists())
            self.assertTrue((old / "run.log").exists())

    def test_translation_unit_progress_is_ui_noise(self):
        self.assertTrue(
            _ui_progress_is_noisy("번역 100/155 (배치 처리)")
        )
        self.assertFalse(
            _ui_progress_is_noisy("Roll20 준비 완료. 기존 번역 결과를 확인합니다.")
        )


if __name__ == "__main__":
    unittest.main()
'''

    return {
        ROOT / "tests" / "test_stage12_ability_modifier_resource_v25.py": resource_test,
        ROOT / "tests" / "test_diagnostic_run_log_v25.py": log_test,
        ROOT / "tests" / "test_single_run_log_v25.py": ui_test,
    }


def sandbox_ignore(_dir, names):
    ignored = set()
    for name in names:
        if name in {
            ".git",
            ".venv",
            "venv",
            "dist",
            "build",
            "__pycache__",
            ".idea",
            ".roll20_chrome_profile",
        }:
            ignored.add(name)
        elif name.endswith(".pyc"):
            ignored.add(name)
    return ignored


def run_sandbox_tests(patched, generated):
    with tempfile.TemporaryDirectory(
        prefix="sheetmover-runtime-v25-"
    ) as temp_dir:
        sandbox = Path(temp_dir) / "repo"
        shutil.copytree(
            ROOT,
            sandbox,
            ignore=sandbox_ignore,
        )

        for path, content in {**patched, **generated}.items():
            relative = path.relative_to(ROOT)
            target = sandbox / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")

        env = os.environ.copy()
        env["PYTHONPATH"] = str(sandbox) + os.pathsep + env.get("PYTHONPATH", "")

        completed = subprocess.run(
            [sys.executable, "-m", "unittest", "discover", "-s", "tests", "-v"],
            cwd=sandbox,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=420,
        )

        output = completed.stdout or ""
        if completed.returncode != 0:
            tail = "\n".join(output.splitlines()[-220:])
            raise RuntimeError(
                "복제본 전체 테스트가 실패했습니다. "
                "실제 프로젝트는 수정하지 않았습니다.\n"
                + tail
            )

        match = re.search(r"Ran\s+(\d+)\s+tests?", output)
        return match.group(1) if match else "전체"


def build_patch():
    required = (RESOURCES, FULL_RUN, UI)
    missing = [str(path) for path in required if not path.is_file()]
    if missing:
        raise RuntimeError("필수 파일이 없습니다: " + ", ".join(missing))

    source = {path: path.read_text(encoding="utf-8") for path in required}

    if (
        "stage12-roll20-resources-v1.2-rest-reset" not in source[RESOURCES]
        and "stage12-roll20-resources-v1.3-ability-modifier-uses" not in source[RESOURCES]
    ):
        raise RuntimeError(
            "roll20_resources.py가 검토한 Stage 12 계열과 다릅니다. "
            "실제 파일을 수정하지 않습니다."
        )

    if (
        "stage13-full-run-v2.3-roll20-preflight-cache" not in source[FULL_RUN]
        and "stage13-full-run-v2.5-diagnostic-log" not in source[FULL_RUN]
    ):
        raise RuntimeError(
            "full_run.py가 검토한 c0d390/v2.3 계열과 다릅니다. "
            "실제 파일을 수정하지 않습니다."
        )

    patched = {
        RESOURCES: patch_resources(source[RESOURCES]),
        FULL_RUN: patch_full_run(source[FULL_RUN]),
        UI: patch_ui(source[UI]),
    }
    generated = generated_tests()

    for path, content in {**patched, **generated}.items():
        compile_text(path, content)

    return patched, generated


def main():
    print("[시트 이동기] runtime-integrity v2.5 준비")
    print(f"- 검토 기준 커밋: {BASE_COMMIT}")
    print("- Bardic Inspiration: 능력 수정치 기반 사용 횟수 지원")
    print("- 동일 Bardic Inspiration action 중복 자원 제거")
    print("- run.log: 오류 진단 정보는 유지, 반복 진행 로그는 제거")
    print("- workers/<run_id>: 종료 후 run.log만 유지")
    print("- 실제 프로젝트 수정 전 복제본 전체 unittest 실행")

    patched, generated = build_patch()

    print("[시트 이동기] 실제 프로젝트를 건드리기 전에 복제본 전체 테스트를 실행합니다...")
    count = run_sandbox_tests(patched, generated)
    print(f"[시트 이동기] 복제본 전체 테스트 통과: {count}개")

    for path in patched:
        backup(path)

    for path, content in patched.items():
        atomic_write(path, content)

    for path, content in generated.items():
        atomic_write(path, content)

    for path in list(patched) + list(generated):
        compile_text(path, path.read_text(encoding="utf-8"))

    print("[시트 이동기] runtime-integrity v2.5 적용 완료")
    print("- Bardic Inspiration 자원은 CHA 수정치 기반으로 계산됩니다.")
    print("- 견본2 기준 기대값: 2/2, short")
    print("- run.log에는 단계/오류/traceback/자원 요약을 남깁니다.")
    print("- 번역 n/N, 주문 n/N, 특성 n/N 같은 반복 로그는 제거합니다.")
    print("다음: python main.py")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"[시트 이동기] runtime-integrity v2.5 실패: {exc}", file=sys.stderr)
        raise
