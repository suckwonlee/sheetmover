from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from sheet_mover.ui import SheetMoverUI


class UIWorkerStateTests(unittest.TestCase):
    def ui(self):
        return SimpleNamespace(
            progress_var=Mock(), progress_text=Mock(), log=Mock(),
            stage_items={1: "stage1"}, stage_tree=Mock(),
            running=True, worker=Mock(), _refresh_start=Mock(),
        )

    def test_complete_event_does_not_finish_ui_before_report_verification(self):
        ui = self.ui()
        SheetMoverUI._worker_event(ui, {"type": "complete", "report": "result.json"})
        ui.progress_var.set.assert_not_called()
        self.assertTrue(ui.running)
        ui.progress_text.set.assert_called_once_with("완료 보고서 확인 중")

    def test_zero_exit_with_missing_completion_shows_error_and_marks_active_stage(self):
        ui = self.ui()
        ui.stage_tree.set.return_value = "진행 중"
        with patch("sheet_mover.ui.messagebox") as dialogs:
            SheetMoverUI._worker_done(ui, 0, "완료 메시지가 없습니다.")
        dialogs.showinfo.assert_not_called()
        dialogs.showerror.assert_called_once()
        ui.progress_var.set.assert_not_called()
        ui.stage_tree.set.assert_any_call("stage1", "status", "중단 · 확인 필요")
        self.assertFalse(ui.running)

    def test_success_after_report_verification_sets_100_percent(self):
        ui = self.ui()
        with patch("sheet_mover.ui.messagebox") as dialogs:
            SheetMoverUI._worker_done(ui, 0, None)
        dialogs.showinfo.assert_called_once()
        dialogs.showerror.assert_not_called()
        ui.progress_var.set.assert_called_once_with(100)

    def test_frozen_worker_uses_executable_and_snapshot_settings(self):
        ui = SimpleNamespace(source_var=Mock())
        ui.source_var.get.return_value = "fixture-url"
        with patch.object(sys, "frozen", True, create=True):
            command = SheetMoverUI._worker_command(ui, Path("snapshot.json"))
        self.assertEqual(command[0], sys.executable)
        self.assertIn("--worker-full-run", command)
        self.assertEqual(command[-1], "snapshot.json")

    def test_stale_connection_check_cannot_overwrite_new_settings_status(self):
        ui = self.ui()
        ui.check_generation = 2
        SheetMoverUI._apply_checks(ui, {}, generation=1)
        ui._refresh_start.assert_not_called()

