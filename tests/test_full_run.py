import unittest
import json
from contextlib import ExitStack
from pathlib import Path
import tempfile
from types import SimpleNamespace
from unittest.mock import Mock, patch

from sheet_mover import full_run
from sheet_mover.full_run import STAGES
from sheet_mover.app_config import AppSettings
from sheet_mover.result_store import latest_complete_result
from sheet_mover.translator import TranslationError


class FullRunStructureTests(unittest.TestCase):
    def test_generic_stage9_action_writer_is_not_in_pipeline(self):
        joined = " ".join(STAGES)
        self.assertNotIn("generic", joined.casefold())
        self.assertNotIn("행동 카드", joined)

    def test_pipeline_contains_actual_combat_and_resource_stages(self):
        self.assertIn("무기 공격", STAGES)
        self.assertIn("주문 공격", STAGES)
        self.assertIn("숙련", STAGES)
        self.assertIn("자원", STAGES)

    def test_stage_order(self):
        self.assertEqual(
            STAGES,
            (
                "D&D Beyond 수집 · 번역 · 계산",
                "Roll20 대상 확인",
                "기본 능력치",
                "인벤토리",
                "주문",
                "특성",
                "무기 공격",
                "주문 공격",
                "숙련",
                "자원",
            ),
        )


class FullRunFailureTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.root = Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        self.stack.enter_context(patch.object(full_run, "data_dir", return_value=self.root))
        self.stack.enter_context(patch.object(full_run, "apply_runtime_environment", side_effect=lambda s: s))
        self.stack.enter_context(patch.object(full_run, "check_google", return_value={"ok": True}))
        self.stack.enter_context(patch.object(full_run, "check_ollama", return_value={"ok": True}))
        self.settings = AppSettings(google_project_id="fixture")
        self.events = []
        self.fetch_character = self.stack.enter_context(
            patch("sheet_mover.source.fetch_character")
        )
        self.fetch_character.return_value = {"id": 1, "name": "fixture"}
        self.payload = {"original": {"source_id": "1", "name": "fixture"},
                        "translated": {"features": [{"name": "translated"}]},
                        "translation_summary": {"status": "complete"}}
        self.prepare = self.stack.enter_context(patch("sheet_mover.mover.run"))
        self.prepare.return_value.to_dict.return_value = self.payload
        self.target = self.stack.enter_context(patch("sheet_mover.roll20_connection.check_roll20_target"))
        self.target.return_value = (
            SimpleNamespace(character_name="fixture", sheet_type="ogl5e"),
            self.root / "target.json",
        )
        self.writers = []
        for path in (
            "stage5_basic_writer_v3.run", "sheet_mover.roll20_inventory.apply_inventory",
            "sheet_mover.roll20_spells.apply_spells", "sheet_mover.roll20_features.apply_features",
            "sheet_mover.roll20_attacks.apply_attacks", "sheet_mover.roll20_spell_attacks.apply_spell_attacks",
            "sheet_mover.roll20_proficiencies.apply_proficiencies", "sheet_mover.roll20_resources.apply_resources",
        ):
            writer = self.stack.enter_context(patch(path))
            writer.return_value = ({"status": "pass"}, self.root / "stage.json")
            self.writers.append(writer)

    def run_move(self):
        return full_run.run_full_move("https://example.invalid/characters/1", self.settings,
                                     emit=self.events.append, run_id="fixture-run")

    def report(self):
        path = self.root / "results/current/full-run-fixture-run.json"
        return json.loads(path.read_text(encoding="utf-8"))

    def test_success_saves_report_before_complete_and_uses_exact_prepared_result(self):
        def check_event(event):
            self.events.append(event)
            if event["type"] == "complete":
                self.assertEqual(self.report()["status"], "pass")
        full_run.run_full_move("fixture-url", self.settings, emit=check_event, run_id="fixture-run")
        report = self.report()
        self.assertTrue(all(s == "pass" for s in report["stage_statuses"].values()))
        self.assertEqual(self.writers[0].call_args.kwargs["result_path"].resolve(),
                         Path(report["paths"]["sheet_result"]))
        self.assertEqual(self.events[-1]["type"], "complete")

    def test_roll20_preflight_happens_before_translation(self):
        order = []

        self.fetch_character.side_effect = lambda _url: (
            order.append("source") or {"id": 1, "name": "fixture"}
        )
        self.target.side_effect = lambda **_kwargs: (
            order.append("roll20")
            or (
                SimpleNamespace(character_name="fixture", sheet_type="ogl5e"),
                self.root / "target.json",
            )
        )
        original_prepare = self.prepare.return_value

        def prepared(*_args, **_kwargs):
            order.append("translate")
            return original_prepare

        self.prepare.side_effect = prepared
        self.run_move()
        self.assertLess(order.index("roll20"), order.index("translate"))

    def test_roll20_preflight_failure_stops_before_translation(self):
        self.target.side_effect = RuntimeError("Roll20 target unavailable")
        with self.assertRaises(RuntimeError):
            self.run_move()
        self.prepare.assert_not_called()
        report = self.report()
        self.assertEqual(report["stage_statuses"]["2"], "error")
        self.assertEqual(report["stage_statuses"]["1"], "pending")

    def test_partial_return_continues_with_exact_original_fallback(self):
        self.payload["translation_summary"] = {
            "status": "partial",
            "original_preserved_count": 1,
            "original_preserved": [
                {
                    "reason": "unsafe translation; exact source preserved",
                    "source_preview": "The original rule text",
                }
            ],
        }
        result = self.run_move()
        report = self.report()

        self.assertEqual(result["status"], "pass")
        self.assertEqual(report["stage_statuses"]["1"], "pass")
        self.assertEqual(
            report["translation_summary"]["original_preserved_count"],
            1,
        )
        self.target.assert_called_once()
        self.assertTrue(all(writer.called for writer in self.writers))
        self.assertTrue(any(e["type"] == "complete" for e in self.events))

        sheet_result = Path(report["paths"]["sheet_result"])
        saved = json.loads(sheet_result.read_text(encoding="utf-8"))
        self.assertEqual(saved["translated"], self.payload["translated"])
        self.assertEqual(saved["translation_summary"]["status"], "partial")

        # A partial translation is still not advertised as a fully translated
        # standalone result. The one-click run can use it because it passes the
        # exact result_path directly to every Roll20 writer.
        self.assertIsNone(latest_complete_result(root=self.root))

    def test_translation_exception_preserves_partial_payload_and_error_details(self):
        exc = TranslationError("translation interrupted")
        exc.partial_payload = dict(self.payload, error={"failed_text": "rule text"})
        self.prepare.side_effect = exc
        with self.assertRaises(TranslationError):
            self.run_move()
        report = self.report()
        partial = json.loads(Path(report["paths"]["partial_result"]).read_text(encoding="utf-8"))
        self.assertEqual(partial["error"]["failed_text"], "rule text")
        self.assertEqual(partial["translated"], self.payload["translated"])
        self.assertFalse(partial["applied"])
        self.target.assert_called_once()
        self.assertIsNone(latest_complete_result(root=self.root))

    def test_middle_stage_failure_keeps_completed_stages_and_does_not_continue(self):
        self.writers[1].side_effect = RuntimeError("inventory interrupted")
        with self.assertRaises(RuntimeError):
            self.run_move()
        report = self.report()
        self.assertEqual(report["status"], "error")
        self.assertEqual(report["stage_statuses"]["3"], "pass")
        self.assertEqual(report["stage_statuses"]["4"], "error")
        self.assertEqual(report["stage_statuses"]["5"], "pending")
        self.assertTrue(Path(report["paths"]["sheet_result"]).exists())
        self.writers[2].assert_not_called()

    def test_failed_writer_evidence_and_save_errors_are_in_full_report(self):
        from sheet_mover.roll20_read import PersistedReadError
        exc = PersistedReadError("server verification failed")
        exc.stage_report = {"status": "error", "mutated": True,
                            "verification": {"status": "unconfirmed"}}
        exc.stage_report_path = str(self.root / "failed-stage.json")
        exc.save_errors = ["stage report save failed"]
        self.writers[1].side_effect = exc
        with self.assertRaises(PersistedReadError):
            self.run_move()
        report = self.report()
        self.assertEqual(report["reports"]["inventory"], exc.stage_report)
        self.assertEqual(report["paths"]["inventory"], exc.stage_report_path)
        self.assertEqual(report["save_errors"], exc.save_errors)
        self.assertEqual(report["error"]["code"], "verification_unconfirmed")

    def test_error_report_returned_by_stage_is_not_treated_as_success(self):
        self.writers[0].return_value = ({"status": "error"}, self.root / "failed-stage.json")
        with self.assertRaises(RuntimeError):
            self.run_move()
        self.assertEqual(self.report()["stage_statuses"]["3"], "error")
        self.assertEqual(self.report()["reports"]["basic"]["status"], "error")
        self.writers[1].assert_not_called()

    def test_partial_file_save_failure_is_explicit_and_preserves_original_error(self):
        exc = TranslationError("translation interrupted")
        exc.partial_payload = self.payload
        self.prepare.side_effect = exc
        real_write = full_run._write_json
        def write(path, payload):
            if path.name.startswith("sheet-partial"):
                raise PermissionError("disk locked")
            return real_write(path, payload)
        with patch.object(full_run, "_write_json", side_effect=write):
            with self.assertRaises(TranslationError) as caught:
                self.run_move()
        self.assertIs(caught.exception, exc)
        self.assertIn("부분 결과 저장 실패", exc.save_errors[0])
        self.assertTrue(self.report()["save_errors"])
        self.assertNotIn("partial_result", self.report()["paths"])

    def test_report_save_failure_stops_before_external_calls(self):
        with patch.object(full_run, "_write_json", side_effect=OSError("disk full")):
            with self.assertRaises(OSError) as caught:
                self.run_move()
        self.prepare.assert_not_called()
        self.target.assert_not_called()
        self.assertTrue(caught.exception.save_errors)

    def test_result_save_failure_keeps_translation_as_partial_and_does_not_apply(self):
        real_write = full_run._write_json
        def write(path, payload):
            if path.name.startswith("sheet-result"):
                raise OSError("disk full")
            return real_write(path, payload)
        with patch.object(full_run, "_write_json", side_effect=write):
            with self.assertRaises(OSError):
                self.run_move()
        self.assertTrue(Path(self.report()["paths"]["partial_result"]).exists())
        self.target.assert_called_once()

    def test_final_report_save_failure_never_emits_complete(self):
        real_write = full_run._write_json
        def write(path, payload):
            if path.name.startswith("full-run") and payload["status"] == "pass":
                raise OSError("final report locked")
            return real_write(path, payload)
        with patch.object(full_run, "_write_json", side_effect=write):
            with self.assertRaises(OSError):
                self.run_move()
        self.assertFalse(any(e["type"] == "complete" for e in self.events))
        self.assertEqual(self.report()["status"], "error")


if __name__ == "__main__":
    unittest.main()
