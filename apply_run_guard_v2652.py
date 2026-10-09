# -*- coding: utf-8 -*-
# Apply v2.6.5.2 cross-process run guard after testing an exact GitHub clone.
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile


BASE_COMMIT = "0dd6e748fdc52e61b449108f4915b81b0b1b3f2d"
REPO_URL = "https://github.com/suckwonlee/sheetmover.git"
PATCH_VERSION = "v2.6.5.2"

RUN_GUARD_TEXT = '# -*- coding: utf-8 -*-\n"""Cross-process guard for one Sheet Mover full run at a time."""\nfrom __future__ import annotations\n\nfrom dataclasses import dataclass\nfrom datetime import datetime\nimport json\nimport os\nfrom pathlib import Path\n\n\nLOCK_VERSION = "2026-10-08-run-guard-v2.6.5.2"\nLOCK_NAME = "full-run.lock"\n\n\nclass RunAlreadyActiveError(RuntimeError):\n    code = "run_already_active"\n\n\ndef _lock_path(data_root) -> Path:\n    return Path(data_root) / "workers" / LOCK_NAME\n\n\ndef _read_json(path: Path):\n    try:\n        value = json.loads(path.read_text(encoding="utf-8"))\n    except (OSError, UnicodeDecodeError, json.JSONDecodeError):\n        return {}\n    return value if isinstance(value, dict) else {}\n\n\ndef _pid_alive(pid: int) -> bool:\n    try:\n        pid = int(pid)\n    except (TypeError, ValueError):\n        return False\n    if pid <= 0:\n        return False\n    if pid == os.getpid():\n        return True\n\n    if os.name == "nt":\n        try:\n            import ctypes\n            kernel32 = ctypes.windll.kernel32\n            PROCESS_QUERY_LIMITED_INFORMATION = 0x1000\n            handle = kernel32.OpenProcess(\n                PROCESS_QUERY_LIMITED_INFORMATION, False, pid\n            )\n            if handle:\n                kernel32.CloseHandle(handle)\n                return True\n            return int(kernel32.GetLastError()) == 5\n        except Exception:\n            return False\n\n    try:\n        os.kill(pid, 0)\n    except ProcessLookupError:\n        return False\n    except PermissionError:\n        return True\n    except OSError:\n        return False\n    return True\n\n\ndef active_run_info(data_root, *, cleanup_stale=True):\n    path = _lock_path(data_root)\n    payload = _read_json(path)\n    pid = payload.get("pid")\n    if payload and _pid_alive(pid):\n        result = dict(payload)\n        result["lock_path"] = str(path.resolve())\n        return result\n\n    if cleanup_stale and path.exists():\n        try:\n            path.unlink()\n        except OSError:\n            pass\n    return None\n\n\n@dataclass\nclass RunGuard:\n    path: Path\n    run_id: str\n    pid: int\n\n    def release(self):\n        current = _read_json(self.path)\n        if (\n            str(current.get("run_id") or "") != self.run_id\n            or int(current.get("pid") or 0) != self.pid\n        ):\n            return\n        try:\n            self.path.unlink()\n        except FileNotFoundError:\n            pass\n\n\ndef acquire_run_guard(*, data_root, run_id) -> RunGuard:\n    path = _lock_path(data_root)\n    path.parent.mkdir(parents=True, exist_ok=True)\n\n    run_id = str(run_id or "").strip() or f"pid-{os.getpid()}"\n    pid = os.getpid()\n    payload = {\n        "version": LOCK_VERSION,\n        "run_id": run_id,\n        "pid": pid,\n        "started_at": datetime.now().astimezone().isoformat(timespec="seconds"),\n    }\n    encoded = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")\n\n    for _ in range(4):\n        try:\n            fd = os.open(\n                path,\n                os.O_CREAT | os.O_EXCL | os.O_WRONLY,\n                0o600,\n            )\n        except FileExistsError:\n            active = active_run_info(data_root, cleanup_stale=True)\n            if active:\n                raise RunAlreadyActiveError(\n                    "이미 다른 시트 이동 작업이 실행 중입니다. "\n                    "기존 작업이 끝난 뒤 다시 실행하세요. "\n                    f"(PID {active.get(\'pid\')}, 작업 {active.get(\'run_id\')})"\n                )\n            continue\n\n        try:\n            with os.fdopen(fd, "wb") as stream:\n                stream.write(encoded)\n                stream.flush()\n                os.fsync(stream.fileno())\n        except Exception:\n            try:\n                path.unlink()\n            except OSError:\n                pass\n            raise\n        return RunGuard(path=path, run_id=run_id, pid=pid)\n\n    raise RuntimeError("시트 이동 실행 잠금 파일을 안전하게 만들지 못했습니다.")\n'
TEST_TEXT = '# -*- coding: utf-8 -*-\nfrom __future__ import annotations\n\nimport json\nimport os\nfrom pathlib import Path\nimport tempfile\nimport time\nimport unittest\nfrom unittest import mock\n\nfrom sheet_mover.run_guard import (\n    RunAlreadyActiveError,\n    acquire_run_guard,\n    active_run_info,\n)\nfrom sheet_mover import run_log\nfrom sheet_mover.ui import _cleanup_old_worker_protocol_files\n\n\nclass RunGuardV2652Tests(unittest.TestCase):\n    def test_second_live_run_is_rejected_and_release_allows_next(self):\n        with tempfile.TemporaryDirectory() as folder:\n            root = Path(folder)\n            first = acquire_run_guard(data_root=root, run_id="first")\n            try:\n                with self.assertRaises(RunAlreadyActiveError):\n                    acquire_run_guard(data_root=root, run_id="second")\n                info = active_run_info(root)\n                self.assertEqual(info["run_id"], "first")\n                self.assertEqual(int(info["pid"]), os.getpid())\n            finally:\n                first.release()\n\n            second = acquire_run_guard(data_root=root, run_id="second")\n            second.release()\n\n    def test_stale_lock_is_replaced(self):\n        with tempfile.TemporaryDirectory() as folder:\n            root = Path(folder)\n            lock = root / "workers" / "full-run.lock"\n            lock.parent.mkdir(parents=True)\n            lock.write_text(\n                json.dumps({"run_id": "stale", "pid": 999999999}),\n                encoding="utf-8",\n            )\n            with mock.patch("sheet_mover.run_guard._pid_alive", return_value=False):\n                guard = acquire_run_guard(data_root=root, run_id="fresh")\n            try:\n                payload = json.loads(lock.read_text(encoding="utf-8"))\n                self.assertEqual(payload["run_id"], "fresh")\n            finally:\n                guard.release()\n\n    def test_run_log_lock_conflict_does_not_call_or_consolidate(self):\n        with tempfile.TemporaryDirectory() as folder:\n            root = Path(folder)\n            owner = acquire_run_guard(data_root=root, run_id="owner")\n            called = {"original": 0, "consolidate": 0}\n\n            def original(*args, **kwargs):\n                called["original"] += 1\n                return {"run_id": kwargs.get("run_id"), "reports": {}, "paths": {}}\n\n            wrapped = run_log.install_single_current_log(original)\n\n            def fake_consolidate(**kwargs):\n                called["consolidate"] += 1\n                return kwargs["run_state"], root / "results" / "current" / "sheetmover-run-latest.json"\n\n            try:\n                with mock.patch("sheet_mover.app_config.data_dir", return_value=root), \\\n                     mock.patch("sheet_mover.run_log.consolidate_current_results", side_effect=fake_consolidate):\n                    with self.assertRaises(RunAlreadyActiveError):\n                        wrapped("url", object(), run_id="blocked")\n            finally:\n                owner.release()\n\n            self.assertEqual(called["original"], 0)\n            self.assertEqual(called["consolidate"], 0)\n\n    def test_run_log_holds_lock_through_execution_and_releases_afterward(self):\n        with tempfile.TemporaryDirectory() as folder:\n            root = Path(folder)\n            final = root / "results" / "current" / "sheetmover-run-latest.json"\n            saw_conflict = []\n\n            def original(source_url, settings, *, emit=None, run_id=None, **kwargs):\n                with self.assertRaises(RunAlreadyActiveError):\n                    acquire_run_guard(data_root=root, run_id="intruder")\n                saw_conflict.append(True)\n                return {"run_id": run_id, "reports": {}, "paths": {}}\n\n            wrapped = run_log.install_single_current_log(original)\n\n            def fake_consolidate(**kwargs):\n                final.parent.mkdir(parents=True, exist_ok=True)\n                final.write_text("{}", encoding="utf-8")\n                return kwargs["run_state"], final\n\n            with mock.patch("sheet_mover.app_config.data_dir", return_value=root), \\\n                 mock.patch("sheet_mover.run_log.consolidate_current_results", side_effect=fake_consolidate):\n                state = wrapped("url", object(), run_id="guarded")\n\n            self.assertTrue(saw_conflict)\n            self.assertEqual(state["run_id"], "guarded")\n            self.assertIsNone(active_run_info(root))\n\n    def test_ipc_cleanup_skips_active_and_recent_worker_folders(self):\n        with tempfile.TemporaryDirectory() as folder:\n            root = Path(folder)\n            workers = root / "workers"\n            active_folder = workers / "active"\n            stale_folder = workers / "stale"\n            recent_folder = workers / "recent"\n            for target in (active_folder, stale_folder, recent_folder):\n                target.mkdir(parents=True)\n                (target / "events.jsonl").write_text("x", encoding="utf-8")\n                (target / "settings.json").write_text("{}", encoding="utf-8")\n\n            old = time.time() - 8 * 60 * 60\n            for target in (active_folder, stale_folder):\n                os.utime(target / "events.jsonl", (old, old))\n                os.utime(target / "settings.json", (old, old))\n                os.utime(target, (old, old))\n\n            guard = acquire_run_guard(data_root=root, run_id="active")\n            try:\n                removed = _cleanup_old_worker_protocol_files(workers)\n            finally:\n                guard.release()\n\n            self.assertEqual(removed, 2)\n            self.assertTrue((active_folder / "events.jsonl").exists())\n            self.assertTrue((active_folder / "settings.json").exists())\n            self.assertFalse((stale_folder / "events.jsonl").exists())\n            self.assertFalse((stale_folder / "settings.json").exists())\n            self.assertTrue((recent_folder / "events.jsonl").exists())\n            self.assertTrue((recent_folder / "settings.json").exists())\n\n\nif __name__ == "__main__":\n    unittest.main()\n'


def _run(args, *, cwd, check=True):
    return subprocess.run(
        args,
        cwd=str(cwd),
        text=True,
        encoding="utf-8",
        errors="replace",
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=check,
    )


def _sha256_text(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _read(path):
    return Path(path).read_text(encoding="utf-8")


def _write_atomic(path, text):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + ".v2652.tmp")
    temp.write_text(text, encoding="utf-8")
    os.replace(temp, path)


def _replace_once(text, old, new, label):
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label} 패치 위치가 {count}개입니다. 기준 코드와 달라 적용을 중단합니다.")
    return text.replace(old, new, 1)


def _patch_run_log(text):
    text = _replace_once(
        text,
        'SINGLE_LOG_VERSION = "2026-10-08-single-current-log-v1.1"',
        'SINGLE_LOG_VERSION = "2026-10-08-single-current-log-v1.2-run-guard"',
        "run_log 버전",
    )

    start = text.find("    def run_full_move_single_log(")
    end_marker = "\n    run_full_move_single_log._sheetmover_single_current_log_v1 = True"
    end = text.find(end_marker, start)
    if start < 0 or end < 0:
        raise RuntimeError("run_log 실행 래퍼 위치를 찾지 못했습니다.")

    replacement = r'''    def run_full_move_single_log(
        source_url,
        settings,
        *,
        emit=None,
        run_id=None,
        update_existing=False,
    ):
        from uuid import uuid4
        from .app_config import data_dir
        from .run_guard import acquire_run_guard

        effective_run_id = str(run_id or uuid4().hex)
        guard = acquire_run_guard(
            data_root=data_dir(),
            run_id=effective_run_id,
        )

        actual_emit = emit or (lambda _event: None)
        held_complete = None

        def proxy_emit(event):
            nonlocal held_complete
            if isinstance(event, dict) and event.get("type") == "complete":
                held_complete = dict(event)
                return
            actual_emit(event)

        try:
            try:
                if update_existing:
                    state = original_run_full_move(
                        source_url,
                        settings,
                        emit=proxy_emit,
                        run_id=effective_run_id,
                        update_existing=True,
                    )
                else:
                    state = original_run_full_move(
                        source_url,
                        settings,
                        emit=proxy_emit,
                        run_id=effective_run_id,
                    )
            except Exception as exc:
                failure_state = getattr(exc, "failure_report", None)
                if not isinstance(failure_state, dict):
                    raise

                try:
                    consolidated, final_path = consolidate_current_results(
                        data_root=data_dir(),
                        run_state=failure_state,
                        run_id=effective_run_id,
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
                run_id=effective_run_id,
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
        finally:
            guard.release()
'''
    return text[:start] + replacement + text[end:]


def _patch_ui(text):
    if "\nimport time\n" not in text:
        text = _replace_once(
            text,
            "import threading\nimport tkinter as tk\n",
            "import threading\nimport time\nimport tkinter as tk\n",
            "UI time import",
        )

    start = text.find("def _cleanup_old_worker_protocol_files(workers_root):")
    end = text.find("\n\ndef _ui_progress_is_noisy", start)
    if start < 0 or end < 0:
        raise RuntimeError("UI IPC 정리 함수 위치를 찾지 못했습니다.")

    cleanup = r'''def _cleanup_old_worker_protocol_files(workers_root, minimum_age_seconds=6 * 60 * 60):
    root = Path(workers_root)
    if not root.is_dir():
        return 0

    active_run_id = ""
    try:
        from .run_guard import active_run_info
        active = active_run_info(root.parent)
        if active:
            active_run_id = str(active.get("run_id") or "")
    except Exception:
        active_run_id = ""

    now = time.time()
    removed = 0
    for folder in root.iterdir():
        if not folder.is_dir():
            continue
        if active_run_id and folder.name == active_run_id:
            continue

        try:
            age = max(0.0, now - folder.stat().st_mtime)
        except OSError:
            continue
        if age < float(minimum_age_seconds):
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
'''
    text = text[:start] + cleanup + text[end:]

    needle = '''        workers_root = data_dir() / "workers"
        removed_ipc = _cleanup_old_worker_protocol_files(workers_root)
'''
    insert = '''        try:
            from .run_guard import active_run_info
            active = active_run_info(data_dir())
        except Exception:
            active = None
        if active:
            messagebox.showwarning(
                "시트 이동기",
                "이미 다른 시트 이동 작업이 실행 중입니다.\\n\\n"
                "기존 작업이 끝난 뒤 다시 실행하세요. "
                f"(PID {active.get('pid')}, 작업 {active.get('run_id')})",
            )
            return

        workers_root = data_dir() / "workers"
        removed_ipc = _cleanup_old_worker_protocol_files(workers_root)
'''
    text = _replace_once(text, needle, insert, "UI 실행 중 작업 확인")
    return text


def _apply_tree(root):
    root = Path(root)
    _write_atomic(
        root / "sheet_mover" / "run_log.py",
        _patch_run_log(_read(root / "sheet_mover" / "run_log.py")),
    )
    _write_atomic(
        root / "sheet_mover" / "ui.py",
        _patch_ui(_read(root / "sheet_mover" / "ui.py")),
    )
    _write_atomic(root / "sheet_mover" / "run_guard.py", RUN_GUARD_TEXT)
    _write_atomic(root / "tests" / "test_run_guard_v2652.py", TEST_TEXT)


def _verify_local_matches_base(root, clone):
    for rel in ("sheet_mover/run_log.py", "sheet_mover/ui.py"):
        local_text = _read(Path(root) / rel)
        base_text = _read(Path(clone) / rel)
        if _sha256_text(local_text) != _sha256_text(base_text):
            raise RuntimeError(
                f"{rel}에 커밋 이후 로컬 변경이 있습니다. 덮어쓰지 않고 중단합니다."
            )


def _test_clone(clone):
    compile_result = _run(
        [sys.executable, "-m", "compileall", "-q", "sheet_mover", "tests"],
        cwd=clone,
        check=False,
    )
    if compile_result.returncode:
        raise RuntimeError("복제본 compileall 실패:\n" + compile_result.stdout)

    result = _run(
        [sys.executable, "-m", "unittest", "discover", "-s", "tests", "-q"],
        cwd=clone,
        check=False,
    )
    if result.returncode:
        raise RuntimeError("복제본 전체 unittest 실패:\n" + result.stdout)

    match = re.search(r"Ran\s+(\d+)\s+tests?", result.stdout)
    return int(match.group(1)) if match else None


def main():
    root = Path(__file__).resolve().parent
    if not (root / ".git").exists():
        raise RuntimeError("이 파일을 E:\\sheet_mover 프로젝트 루트에서 실행하세요.")

    head = _run(["git", "rev-parse", "HEAD"], cwd=root).stdout.strip()
    if head != BASE_COMMIT:
        raise RuntimeError(
            "현재 프로젝트 HEAD가 검증 기준 커밋과 다릅니다. "
            f"현재={head}, 기준={BASE_COMMIT}"
        )

    with tempfile.TemporaryDirectory(prefix="sheetmover-v2652-") as folder:
        clone = Path(folder) / "repo"
        print("[시트 이동기] 최신 커밋 복제본 검증을 시작합니다.")
        cloned = _run(
            ["git", "clone", "--quiet", REPO_URL, str(clone)],
            cwd=root,
            check=False,
        )
        if cloned.returncode:
            raise RuntimeError("GitHub 복제 실패:\n" + cloned.stdout)

        checked = _run(
            ["git", "checkout", "--quiet", BASE_COMMIT],
            cwd=clone,
            check=False,
        )
        if checked.returncode:
            raise RuntimeError("기준 커밋 checkout 실패:\n" + checked.stdout)

        _verify_local_matches_base(root, clone)
        _apply_tree(clone)
        count = _test_clone(clone)

        backups = {}
        touched = [
            root / "sheet_mover" / "run_log.py",
            root / "sheet_mover" / "ui.py",
            root / "sheet_mover" / "run_guard.py",
            root / "tests" / "test_run_guard_v2652.py",
        ]
        for path in touched:
            backups[path] = path.read_bytes() if path.exists() else None

        try:
            _apply_tree(root)
        except Exception:
            for path, data in backups.items():
                if data is None:
                    path.unlink(missing_ok=True)
                else:
                    path.write_bytes(data)
            raise

    if count is None:
        print(f"[시트 이동기] {PATCH_VERSION} 전체 테스트 통과")
    else:
        print(f"[시트 이동기] {PATCH_VERSION} 전체 테스트 통과: {count}개")
    print("[시트 이동기] 동시 실행 방지 패치 적용 완료")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"[시트 이동기] 패치 실패: {exc}")
        raise SystemExit(1)
