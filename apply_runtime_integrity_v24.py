# -*- coding: utf-8 -*-
from __future__ import annotations

import argparse
import ast
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile

ROOT = Path.cwd()
FULL_RUN = ROOT / "sheet_mover" / "full_run.py"
UI = ROOT / "sheet_mover" / "ui.py"
TEST_FULL_RUN = ROOT / "tests" / "test_full_run.py"

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
    dst = path.with_suffix(path.suffix + ".pre-runtime-v2.4.bak")
    if not dst.exists():
        shutil.copy2(path, dst)


def atomic_write(path, text):
    temp = path.with_name(path.name + ".runtime-v24.tmp")
    temp.write_text(text, encoding="utf-8")
    os.replace(temp, path)


def patch_full_run(text):
    if "stage13-full-run-v2.4-final-ui-refresh" in text:
        return text

    text = replace_once(
        text,
        'FULL_RUN_VERSION = "2026-10-07-stage13-full-run-v2.3-roll20-preflight-cache"',
        'FULL_RUN_VERSION = "2026-10-07-stage13-full-run-v2.4-final-ui-refresh"',
        "full-run version",
    )

    if "\nimport time\n" not in text:
        text = replace_once(
            text,
            "import sys\n",
            "import sys\nimport time\n",
            "full-run time import",
        )

    old_report = (
        '    report_path = data_dir() / "results" / "current" / '
        'f"full-run-{run_id}.json"\n'
    )
    new_report = (
        '    report_path = data_dir() / "workers" / run_id / "full-run.json"\n'
    )
    text = replace_once(
        text,
        old_report,
        new_report,
        "full-run report path",
    )

    marker = "\ndef _emit_stdout(event: dict):\n"
    if marker not in text:
        raise RuntimeError("full_run.py의 _emit_stdout 위치를 찾지 못했습니다.")

    helper = r'''
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


'''
    text = text.replace(
        marker,
        "\n" + helper + "def _emit_stdout(event: dict):\n",
        1,
    )

    old_success = '''        state["status"] = "pass"
        state["active_stage"] = None
        state["finished_at"] = datetime.now().astimezone().isoformat(timespec="seconds")
        checkpoint()
'''
    new_success = '''        overall(99, "Roll20 화면을 저장된 서버 상태로 다시 불러옵니다.")
        state["roll20_ui_refresh"] = _refresh_roll20_views(
            settings.roll20_cdp_url
        )
        state["status"] = "pass"
        state["active_stage"] = None
        state["finished_at"] = datetime.now().astimezone().isoformat(timespec="seconds")
        checkpoint()
'''
    text = replace_once(
        text,
        old_success,
        new_success,
        "final Roll20 UI refresh",
    )

    old_error = '''            emit({"type": "error", "message": str(exc),
                  "traceback": traceback.format_exc(),
                  "report": getattr(exc, "failure_report_path", None),
                  "paths": getattr(exc, "failure_report", {}).get("paths", {}),
                  "save_errors": getattr(exc, "save_errors", [])})
'''
    new_error = '''            emit({"type": "error", "message": str(exc),
                  "traceback": traceback.format_exc(),
                  "report": getattr(exc, "failure_report_path", None),
                  "save_errors": getattr(exc, "save_errors", [])})
'''
    text = replace_once(
        text,
        old_error,
        new_error,
        "compact error event",
    )

    return text


def patch_ui(text):
    if "Keep only run.log for one GUI execution." in text:
        return text

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
                # Keep only run.log for one GUI execution.
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
    text = replace_once(
        text,
        old_finally,
        new_finally,
        "UI one-log cleanup",
    )

    old_complete = '''        elif kind == "complete":
            self.progress_text.set("완료 보고서 확인 중")
            self.log(f"완료 보고서: {event.get('report')}")
'''
    new_complete = '''        elif kind == "complete":
            self.progress_text.set("완료 보고서 확인 중")
            self.log("완료 보고서 검증 통과")
'''
    text = replace_once(
        text,
        old_complete,
        new_complete,
        "UI complete log",
    )

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
                self.log(error)
'''
    text = replace_once(
        text,
        old_error,
        new_error,
        "UI compact error log",
    )

    return text


def patch_full_run_tests(text):
    if "self.refresh_roll20" not in text:
        anchor = '''        self.settings = AppSettings(google_project_id="fixture")
        self.events = []
'''
        replacement = '''        self.settings = AppSettings(google_project_id="fixture")
        self.events = []
        self.refresh_roll20 = self.stack.enter_context(
            patch.object(
                full_run,
                "_refresh_roll20_views",
                return_value={
                    "status": "pass",
                    "refreshed_view_count": 1,
                    "editor_ready": True,
                },
            )
        )
'''
        text = replace_once(
            text,
            anchor,
            replacement,
            "full-run refresh mock",
        )

    old_report = '''    def report(self):
        path = self.root / "results/current/full-run-fixture-run.json"
        return json.loads(path.read_text(encoding="utf-8"))
'''
    new_report = '''    def report(self):
        path = self.root / "workers/fixture-run/full-run.json"
        return json.loads(path.read_text(encoding="utf-8"))
'''
    if old_report in text:
        text = text.replace(old_report, new_report, 1)

    if "test_success_refreshes_roll20_before_complete" not in text:
        marker = '''    def test_roll20_preflight_happens_before_translation(self):
'''
        addition = '''    def test_success_refreshes_roll20_before_complete(self):
        self.run_move()
        self.refresh_roll20.assert_called_once_with(
            self.settings.roll20_cdp_url
        )
        report = self.report()
        self.assertEqual(
            report["roll20_ui_refresh"]["status"],
            "pass",
        )

'''
        if marker not in text:
            raise RuntimeError("full-run test 삽입 위치를 찾지 못했습니다.")
        text = text.replace(marker, addition + marker, 1)

    return text


def generated_tests():
    ui_test = r'''import unittest
from pathlib import Path

import sheet_mover.ui as ui


class SingleRunLogV24Tests(unittest.TestCase):
    def test_ui_worker_folder_retains_only_run_log(self):
        source = Path(ui.__file__).read_text(encoding="utf-8")
        self.assertIn(
            "Keep only run.log for one GUI execution.",
            source,
        )
        self.assertIn(
            "if transient.resolve() == log_path.resolve()",
            source,
        )
        self.assertNotIn(
            'self.log(f"저장된 결과 [{key}]: {path}")',
            source,
        )
        self.assertNotIn(
            'self.log(f"실패 기록: {event[\\'report\\']}")',
            source,
        )


if __name__ == "__main__":
    unittest.main()
'''

    refresh_test = r'''import unittest
from unittest.mock import patch

from sheet_mover import full_run


class Roll20FinalRefreshV24Tests(unittest.TestCase):
    def test_refresh_all_roll20_views_and_require_editor_ready(self):
        class Switch:
            def __init__(self, driver):
                self.driver = driver

            def window(self, handle):
                self.driver.current_window_handle = handle

        class Driver:
            def __init__(self):
                self.window_handles = ["editor", "sheet", "other"]
                self.current_window_handle = "editor"
                self.switch_to = Switch(self)
                self.urls = {
                    "editor": "https://app.roll20.net/editor/",
                    "sheet": "https://app.roll20.net/characters/abc",
                    "other": "https://example.com/",
                }
                self.refreshed = []

            @property
            def current_url(self):
                return self.urls[self.current_window_handle]

            def refresh(self):
                self.refreshed.append(self.current_window_handle)

            def execute_script(self, script):
                if "document.readyState" in script:
                    return "complete"
                return True

        driver = Driver()

        with patch(
            "sheet_mover.roll20_connection._ensure_cdp"
        ), patch(
            "sheet_mover.roll20_connection._attach_driver",
            return_value=driver,
        ), patch(
            "sheet_mover.roll20_connection._disconnect_driver"
        ):
            result = full_run._refresh_roll20_views(
                "http://127.0.0.1:9222",
                timeout=1,
            )

        self.assertEqual(result["status"], "pass")
        self.assertEqual(
            set(driver.refreshed),
            {"editor", "sheet"},
        )
        self.assertEqual(result["refreshed_view_count"], 2)


if __name__ == "__main__":
    unittest.main()
'''

    return {
        ROOT / "tests" / "test_single_run_log_v24.py": ui_test,
        ROOT / "tests" / "test_roll20_final_refresh_v24.py": refresh_test,
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
        prefix="sheetmover-runtime-v24-"
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
        env["PYTHONPATH"] = (
            str(sandbox)
            + os.pathsep
            + env.get("PYTHONPATH", "")
        )

        completed = subprocess.run(
            [
                sys.executable,
                "-m",
                "unittest",
                "discover",
                "-s",
                "tests",
                "-v",
            ],
            cwd=sandbox,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=300,
        )

        output = completed.stdout or ""
        if completed.returncode != 0:
            tail = "\n".join(output.splitlines()[-160:])
            raise RuntimeError(
                "복제본 전체 테스트가 실패했습니다. "
                "실제 프로젝트는 수정하지 않았습니다.\n"
                + tail
            )

        match = re.search(r"Ran\s+(\d+)\s+tests?", output)
        return match.group(1) if match else "전체"


def build_patch():
    required = (FULL_RUN, UI, TEST_FULL_RUN)
    missing = [str(path) for path in required if not path.is_file()]
    if missing:
        raise RuntimeError(
            "필수 파일이 없습니다: " + ", ".join(missing)
        )

    source = {
        path: path.read_text(encoding="utf-8")
        for path in required
    }

    if (
        "stage13-full-run-v2.3-roll20-preflight-cache"
        not in source[FULL_RUN]
        and "stage13-full-run-v2.4-final-ui-refresh"
        not in source[FULL_RUN]
    ):
        raise RuntimeError(
            "full_run.py가 검토한 c0d390/v2.3 계열과 다릅니다. "
            "현재 프로젝트를 수정하지 않습니다."
        )

    patched = {
        FULL_RUN: patch_full_run(source[FULL_RUN]),
        UI: patch_ui(source[UI]),
        TEST_FULL_RUN: patch_full_run_tests(
            source[TEST_FULL_RUN]
        ),
    }
    generated = generated_tests()

    for path, content in {**patched, **generated}.items():
        compile_text(path, content)

    return patched, generated


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--no-tests",
        action="store_true",
        help="복제본 전체 테스트를 생략합니다.",
    )
    args = parser.parse_args()

    print("[시트 이동기] runtime-integrity v2.4 준비")
    print(f"- 검토 기준 커밋: {BASE_COMMIT}")
    print("- 완료 직전 열려 있는 Roll20 화면 전체 재로딩")
    print("- workers/<run_id>/에는 종료 후 run.log 하나만 유지")
    print("- UI에서 단계별 결과 JSON 경로 나열 제거")
    print("- 실제 프로젝트 수정 전 복제본 전체 unittest 실행")

    patched, generated = build_patch()

    if not args.no_tests:
        print(
            "[시트 이동기] 실제 프로젝트를 건드리기 전에 "
            "복제본 전체 테스트를 실행합니다..."
        )
        count = run_sandbox_tests(patched, generated)
        print(
            f"[시트 이동기] 복제본 전체 테스트 통과: {count}개"
        )

    for path in patched:
        backup(path)

    for path, content in patched.items():
        atomic_write(path, content)

    for path, content in generated.items():
        atomic_write(path, content)

    for path in list(patched) + list(generated):
        compile_text(
            path,
            path.read_text(encoding="utf-8"),
        )

    print("[시트 이동기] runtime-integrity v2.4 적용 완료")
    print("- 다음 실행부터 worker 실행 폴더에는 run.log만 남습니다.")
    print("- Roll20 최종 화면 재로딩이 성공해야 전체 완료 처리됩니다.")
    print("- 이제 바로: python main.py")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(
            f"[시트 이동기] runtime-integrity v2.4 실패: {exc}",
            file=sys.stderr,
        )
        raise
