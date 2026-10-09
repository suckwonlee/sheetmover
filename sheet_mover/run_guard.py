# -*- coding: utf-8 -*-
"""Cross-process guard for one Sheet Mover full run at a time."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import json
import os
from pathlib import Path


LOCK_VERSION = "2026-10-08-run-guard-v2.6.5.2"
LOCK_NAME = "full-run.lock"


class RunAlreadyActiveError(RuntimeError):
    code = "run_already_active"


def _lock_path(data_root) -> Path:
    return Path(data_root) / "workers" / LOCK_NAME


def _read_json(path: Path):
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _pid_alive(pid: int) -> bool:
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return False
    if pid <= 0:
        return False
    if pid == os.getpid():
        return True

    if os.name == "nt":
        try:
            import ctypes
            kernel32 = ctypes.windll.kernel32
            PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
            handle = kernel32.OpenProcess(
                PROCESS_QUERY_LIMITED_INFORMATION, False, pid
            )
            if handle:
                kernel32.CloseHandle(handle)
                return True
            return int(kernel32.GetLastError()) == 5
        except Exception:
            return False

    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def active_run_info(data_root, *, cleanup_stale=True):
    path = _lock_path(data_root)
    payload = _read_json(path)
    pid = payload.get("pid")
    if payload and _pid_alive(pid):
        result = dict(payload)
        result["lock_path"] = str(path.resolve())
        return result

    if cleanup_stale and path.exists():
        try:
            path.unlink()
        except OSError:
            pass
    return None


@dataclass
class RunGuard:
    path: Path
    run_id: str
    pid: int

    def release(self):
        current = _read_json(self.path)
        if (
            str(current.get("run_id") or "") != self.run_id
            or int(current.get("pid") or 0) != self.pid
        ):
            return
        try:
            self.path.unlink()
        except FileNotFoundError:
            pass


def acquire_run_guard(*, data_root, run_id) -> RunGuard:
    path = _lock_path(data_root)
    path.parent.mkdir(parents=True, exist_ok=True)

    run_id = str(run_id or "").strip() or f"pid-{os.getpid()}"
    pid = os.getpid()
    payload = {
        "version": LOCK_VERSION,
        "run_id": run_id,
        "pid": pid,
        "started_at": datetime.now().astimezone().isoformat(timespec="seconds"),
    }
    encoded = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")

    for _ in range(4):
        try:
            fd = os.open(
                path,
                os.O_CREAT | os.O_EXCL | os.O_WRONLY,
                0o600,
            )
        except FileExistsError:
            active = active_run_info(data_root, cleanup_stale=True)
            if active:
                raise RunAlreadyActiveError(
                    "이미 다른 시트 이동 작업이 실행 중입니다. "
                    "기존 작업이 끝난 뒤 다시 실행하세요. "
                    f"(PID {active.get('pid')}, 작업 {active.get('run_id')})"
                )
            continue

        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(encoded)
                stream.flush()
                os.fsync(stream.fileno())
        except Exception:
            try:
                path.unlink()
            except OSError:
                pass
            raise
        return RunGuard(path=path, run_id=run_id, pid=pid)

    raise RuntimeError("시트 이동 실행 잠금 파일을 안전하게 만들지 못했습니다.")
