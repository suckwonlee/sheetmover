# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest import mock

from sheet_mover.run_guard import (
    RunAlreadyActiveError,
    acquire_run_guard,
    active_run_info,
)
from sheet_mover import run_log
from sheet_mover.ui import _cleanup_old_worker_protocol_files


class RunGuardV2652Tests(unittest.TestCase):
    def test_second_live_run_is_rejected_and_release_allows_next(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            first = acquire_run_guard(data_root=root, run_id="first")
            try:
                with self.assertRaises(RunAlreadyActiveError):
                    acquire_run_guard(data_root=root, run_id="second")
                info = active_run_info(root)
                self.assertEqual(info["run_id"], "first")
                self.assertEqual(int(info["pid"]), os.getpid())
            finally:
                first.release()

            second = acquire_run_guard(data_root=root, run_id="second")
            second.release()

    def test_stale_lock_is_replaced(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            lock = root / "workers" / "full-run.lock"
            lock.parent.mkdir(parents=True)
            lock.write_text(
                json.dumps({"run_id": "stale", "pid": 999999999}),
                encoding="utf-8",
            )
            with mock.patch("sheet_mover.run_guard._pid_alive", return_value=False):
                guard = acquire_run_guard(data_root=root, run_id="fresh")
            try:
                payload = json.loads(lock.read_text(encoding="utf-8"))
                self.assertEqual(payload["run_id"], "fresh")
            finally:
                guard.release()

    def test_run_log_lock_conflict_does_not_call_or_consolidate(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            owner = acquire_run_guard(data_root=root, run_id="owner")
            called = {"original": 0, "consolidate": 0}

            def original(*args, **kwargs):
                called["original"] += 1
                return {"run_id": kwargs.get("run_id"), "reports": {}, "paths": {}}

            wrapped = run_log.install_single_current_log(original)

            def fake_consolidate(**kwargs):
                called["consolidate"] += 1
                return kwargs["run_state"], root / "results" / "current" / "sheetmover-run-latest.json"

            try:
                with mock.patch("sheet_mover.app_config.data_dir", return_value=root), \
                     mock.patch("sheet_mover.run_log.consolidate_current_results", side_effect=fake_consolidate):
                    with self.assertRaises(RunAlreadyActiveError):
                        wrapped("url", object(), run_id="blocked")
            finally:
                owner.release()

            self.assertEqual(called["original"], 0)
            self.assertEqual(called["consolidate"], 0)

    def test_run_log_holds_lock_through_execution_and_releases_afterward(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            final = root / "results" / "current" / "sheetmover-run-latest.json"
            saw_conflict = []

            def original(source_url, settings, *, emit=None, run_id=None, **kwargs):
                with self.assertRaises(RunAlreadyActiveError):
                    acquire_run_guard(data_root=root, run_id="intruder")
                saw_conflict.append(True)
                return {"run_id": run_id, "reports": {}, "paths": {}}

            wrapped = run_log.install_single_current_log(original)

            def fake_consolidate(**kwargs):
                final.parent.mkdir(parents=True, exist_ok=True)
                final.write_text("{}", encoding="utf-8")
                return kwargs["run_state"], final

            with mock.patch("sheet_mover.app_config.data_dir", return_value=root), \
                 mock.patch("sheet_mover.run_log.consolidate_current_results", side_effect=fake_consolidate):
                state = wrapped("url", object(), run_id="guarded")

            self.assertTrue(saw_conflict)
            self.assertEqual(state["run_id"], "guarded")
            self.assertIsNone(active_run_info(root))

    def test_ipc_cleanup_skips_active_and_recent_worker_folders(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            workers = root / "workers"
            active_folder = workers / "active"
            stale_folder = workers / "stale"
            recent_folder = workers / "recent"
            for target in (active_folder, stale_folder, recent_folder):
                target.mkdir(parents=True)
                (target / "events.jsonl").write_text("x", encoding="utf-8")
                (target / "settings.json").write_text("{}", encoding="utf-8")

            old = time.time() - 8 * 60 * 60
            for target in (active_folder, stale_folder):
                os.utime(target / "events.jsonl", (old, old))
                os.utime(target / "settings.json", (old, old))
                os.utime(target, (old, old))

            guard = acquire_run_guard(data_root=root, run_id="active")
            try:
                removed = _cleanup_old_worker_protocol_files(workers)
            finally:
                guard.release()

            self.assertEqual(removed, 2)
            self.assertTrue((active_folder / "events.jsonl").exists())
            self.assertTrue((active_folder / "settings.json").exists())
            self.assertFalse((stale_folder / "events.jsonl").exists())
            self.assertFalse((stale_folder / "settings.json").exists())
            self.assertTrue((recent_folder / "events.jsonl").exists())
            self.assertTrue((recent_folder / "settings.json").exists())


if __name__ == "__main__":
    unittest.main()
