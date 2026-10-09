import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

from sheet_mover import full_run
from sheet_mover.worker_protocol import EventReader, EventWriter, completion_error, monitor_worker


class WorkerProtocolTests(unittest.TestCase):
    def test_malformed_payloads_fail_before_reaching_ui(self):
        payloads = (
            {"type": "progress", "percent": "bad"},
            {"type": "progress", "percent": float("nan")},
            {"type": "stage", "index": "1", "status": "running"},
            {"type": "error", "save_errors": "not a list"},
            {"type": "complete", "report": None},
        )
        for payload in payloads:
            with self.subTest(payload=payload), tempfile.TemporaryDirectory() as temp:
                path = Path(temp) / "events.jsonl"
                EventWriter(path, "run1").emit(payload)
                proc = Mock()
                proc.poll.return_value = 0
                proc.wait.return_value = 0
                on_event = Mock()
                code, error = monitor_worker(proc, path, "run1", on_event)
                self.assertEqual(code, 0)
                self.assertIn("작업 통신 오류", error)
                on_event.assert_not_called()

    def test_invalid_stage_status_collection_cannot_crash_report_check(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "report.json"
            path.write_text(json.dumps({"run_id": "run1", "status": "pass",
                                        "stage_statuses": [str(i) for i in range(1, 11)]}))
            self.assertIsNotNone(completion_error(0, {"report": str(path)}, "run1"))

    def test_successful_real_child_emits_complete_only_with_report(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            script = (
                "import sys; from pathlib import Path; "
                "from sheet_mover.worker_protocol import EventWriter; "
                "from sheet_mover.result_store import write_json_atomic; "
                "report=Path(sys.argv[2]); "
                "write_json_atomic(report, {'run_id':'run1','status':'pass',"
                "'stage_statuses':{str(i):'pass' for i in range(1,11)}}); "
                "EventWriter(sys.argv[1], 'run1').emit({'type':'complete','report':str(report)})"
            )
            proc = subprocess.Popen([sys.executable, "-c", script, str(root / "events.jsonl"),
                                     str(root / "report.json")],
                                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            code, error = monitor_worker(proc, root / "events.jsonl", "run1", lambda e: None)
            self.assertEqual(code, 0)
            self.assertIsNone(error)

    def test_split_utf8_message_waits_for_complete_line(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "events.jsonl"
            reader = EventReader(path, "run1")
            encoded = (json.dumps({"run_id": "run1", "sequence": 1,
                                   "type": "progress", "percent": 12, "message": "번역 중"},
                                  ensure_ascii=False) + "\n").encode("utf-8")
            split = encoded.index("번".encode("utf-8")) + 1
            path.write_bytes(encoded[:split])
            self.assertEqual(reader.read(), [])
            with path.open("ab") as file:
                file.write(encoded[split:])
            self.assertEqual(reader.read()[0]["message"], "번역 중")

    def test_truncated_message_is_failure_when_worker_exits(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "events.jsonl"
            path.write_bytes(b'{"type":"complete"')
            reader = EventReader(path, "run1")
            self.assertEqual(reader.read(), [])
            with self.assertRaises(ValueError):
                reader.read(final=True)

    def test_rejects_stale_or_out_of_order_events(self):
        for run_id, sequence in (("other-run", 1), ("run1", 2)):
            with self.subTest(run_id=run_id, sequence=sequence), tempfile.TemporaryDirectory() as temp:
                path = Path(temp) / "events.jsonl"
                path.write_text(json.dumps({"run_id": run_id, "sequence": sequence}) + "\n")
                with self.assertRaises(ValueError):
                    EventReader(path, "run1").read()

    def test_success_requires_current_report_and_all_stages(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "report.json"
            event = {"report": str(path)}
            self.assertIsNotNone(completion_error(0, None, "run1"))
            self.assertIsNotNone(completion_error(0, event, "run1"))
            report = {"run_id": "run1", "status": "pass",
                      "stage_statuses": {str(i): "pass" for i in range(1, 11)}}
            path.write_text(json.dumps(report))
            self.assertIsNone(completion_error(0, event, "run1"))
            self.assertIsNotNone(completion_error(1, event, "run1"))
            self.assertIsNotNone(completion_error(0, event, "another-run"))
            report["stage_statuses"]["10"] = "running"
            path.write_text(json.dumps(report))
            self.assertIsNotNone(completion_error(0, event, "run1"))

    def test_worker_cli_with_no_console_saves_events_and_logs(self):
        with tempfile.TemporaryDirectory() as temp:
            event_path, log_path = Path(temp) / "events.jsonl", Path(temp) / "worker.log"
            def fake_run(source, settings, emit, run_id):
                print("library log")
                emit({"type": "progress", "percent": 12, "message": "번역 중"})
                raise RuntimeError("simulated failure")
            with patch.object(sys, "stdout", None), patch.object(sys, "stderr", None), \
                    patch.object(full_run, "run_full_move", side_effect=fake_run):
                code = full_run.cli_main(["--source", "fixture", "--events", str(event_path),
                                          "--log", str(log_path), "--run-id", "run1"])
            self.assertEqual(code, 1)
            events = EventReader(event_path, "run1").read(final=True)
            self.assertEqual([e["type"] for e in events], ["progress", "error"])
            self.assertIn("simulated failure", events[-1]["message"])
            self.assertIn("library log", log_path.read_text(encoding="utf-8"))

    def test_cli_reports_save_errors_in_failure_event(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "events.jsonl"
            exc = RuntimeError("translation failed")
            exc.save_errors = ["partial save failed"]
            with patch.object(full_run, "run_full_move", side_effect=exc):
                code = full_run.cli_main(["--source", "fixture", "--events", str(path),
                                          "--log", str(Path(temp) / "worker.log"), "--run-id", "run1"])
            self.assertEqual(code, 1)
            self.assertEqual(EventReader(path, "run1").read(final=True)[0]["save_errors"],
                             ["partial save failed"])

    def test_abrupt_real_child_exit_cannot_show_success(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "events.jsonl"
            script = (
                "import os,sys; from sheet_mover.worker_protocol import EventWriter; "
                "EventWriter(sys.argv[1], 'run1').emit({'type':'stage','index':1,'status':'running'}); "
                "os._exit(7)"
            )
            proc = subprocess.Popen([sys.executable, "-c", script, str(path)],
                                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            events = []
            code, error = monitor_worker(proc, path, "run1", events.append)
            self.assertEqual(code, 7)
            self.assertIsNotNone(error)
            self.assertEqual(events[0]["status"], "running")

    def test_worker_result_paths_use_applied_runtime_directory_before_first_checkpoint(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            script = (
                "import sys; from pathlib import Path; "
                "from sheet_mover.full_run import run_full_move; "
                "from sheet_mover.app_config import AppSettings; "
                "\ntry: run_full_move('fixture', AppSettings(), run_id='run1')"
                "\nexcept RuntimeError: pass"
                "\nfrom sheet_mover.result_store import CURRENT_RESULT_DIR"
                "\nassert CURRENT_RESULT_DIR.resolve() == Path(sys.argv[1]).resolve()"
            )
            env = dict(__import__("os").environ, LOCALAPPDATA=str(root), APPDATA=str(root))
            result = subprocess.run([sys.executable, "-c", script,
                                     str(root / "SheetMover/results/current")],
                                    env=env, capture_output=True, timeout=20)
            self.assertEqual(result.returncode, 0, result.stderr.decode("utf-8", errors="replace"))

    @unittest.skipUnless(sys.platform == "win32", "Windows windowed worker")
    def test_pythonw_worker_without_stdio_returns_failure_events(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            settings = root / "settings.json"
            settings.write_text('{"google_project_id":""}', encoding="utf-8")
            env = dict(__import__("os").environ, LOCALAPPDATA=str(root), APPDATA=str(root))
            pythonw = Path(sys.executable).with_name("pythonw.exe")
            if not pythonw.exists():
                self.skipTest("pythonw unavailable")
            proc = subprocess.Popen([
                str(pythonw), "main.py", "--worker-full-run", "--source", "fixture",
                "--settings", str(settings), "--events", str(root / "events.jsonl"),
                "--log", str(root / "worker.log"), "--run-id", "run1",
            ], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            events = []
            code, error = monitor_worker(proc, root / "events.jsonl", "run1", events.append)
            self.assertEqual(code, 1)
            self.assertIsNotNone(error)
            self.assertEqual(events[-1]["type"], "error")
            report = json.loads((root / "SheetMover/results/current/sheetmover-run-latest.json").read_text(encoding="utf-8"))
            self.assertEqual(report["status"], "error")
