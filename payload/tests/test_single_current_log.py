# -*- coding: utf-8 -*-
import json
from pathlib import Path
import tempfile
import unittest

from sheet_mover.run_log import (
    FINAL_LOG_NAME,
    consolidate_current_results,
    previous_stage_report,
)


class SingleCurrentLogTests(unittest.TestCase):
    def test_consolidates_reports_backups_target_and_sheet_result(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            current = root / "results" / "current"
            current.mkdir(parents=True)

            sheet = current / "sheet-result-125-20261008-150000.json"
            target = current / "roll20-target-125.json"
            basic = current / "roll20-basic-write-v3-125.json"
            backup = current / "roll20-stage5-backup-v3-125-20261008-150001.json"
            full = current / "full-run-run123.json"
            old_backup = current / "roll20-stage6-inventory-backup-OLD.json"

            sheet.write_text(
                json.dumps(
                    {
                        "original": {"source_id": "125"},
                        "translation_summary": {"status": "complete"},
                        "roll20_payload": {},
                        "raw_source": {"id": 125},
                    }
                ),
                encoding="utf-8",
            )
            target.write_text(json.dumps({"target": True}), encoding="utf-8")
            backup.write_text(json.dumps({"before": {"hp": 10}}), encoding="utf-8")
            basic_report = {
                "status": "pass",
                "backup_path": str(backup),
                "managed": ["hp"],
            }
            basic.write_text(json.dumps(basic_report), encoding="utf-8")
            full.write_text(json.dumps({"status": "pass"}), encoding="utf-8")
            old_backup.write_text(json.dumps({"old": True}), encoding="utf-8")

            state = {
                "run_id": "run123",
                "status": "pass",
                "source_character_id": "125",
                "character_name": "테스트",
                "paths": {
                    "sheet_result": str(sheet),
                    "target": str(target),
                    "basic": str(basic),
                },
                "reports": {
                    "basic": basic_report,
                    "resources": {
                        "status": "pass",
                        "managed_repeating_row_ids": ["abc"],
                    },
                },
                "full_run_report": str(full),
            }

            payload, final_path = consolidate_current_results(
                data_root=root,
                run_state=state,
                run_id="run123",
            )

            self.assertEqual(final_path.name, FINAL_LOG_NAME)
            self.assertTrue(final_path.is_file())

            current_json = sorted(p.name for p in current.glob("*.json"))
            self.assertEqual(current_json, [FINAL_LOG_NAME])

            cache_sheet = root / "results" / "cache" / sheet.name
            self.assertTrue(cache_sheet.is_file())

            self.assertIn(sheet.name, payload["artifacts"])
            self.assertIn(target.name, payload["artifacts"])
            self.assertIn(basic.name, payload["artifacts"])
            self.assertIn(backup.name, payload["artifacts"])
            self.assertIn(full.name, payload["artifacts"])
            self.assertFalse(old_backup.exists())

    def test_previous_stage_report_survives_without_individual_stage_file(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            current = root / "results" / "current"
            current.mkdir(parents=True)

            final = current / FINAL_LOG_NAME
            final.write_text(
                json.dumps(
                    {
                        "last_known_stage_reports": {
                            "proficiencies": {
                                "managed_repeating_rows": {
                                    "tool": ["t1"],
                                    "proficiencies": ["p1"],
                                }
                            },
                            "resources": {
                                "fixed_slot_assignments": {
                                    "class_resource": "r1"
                                },
                                "managed_repeating_row_ids": ["row1"],
                            },
                        }
                    }
                ),
                encoding="utf-8",
            )

            prof = previous_stage_report(current, "proficiencies")
            resources = previous_stage_report(current, "resources")

            self.assertEqual(
                prof["managed_repeating_rows"]["tool"],
                ["t1"],
            )
            self.assertEqual(
                resources["fixed_slot_assignments"]["class_resource"],
                "r1",
            )

    def test_last_known_stage_report_is_carried_across_earlier_failure(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            current = root / "results" / "current"
            current.mkdir(parents=True)

            (current / FINAL_LOG_NAME).write_text(
                json.dumps(
                    {
                        "last_known_stage_reports": {
                            "resources": {
                                "status": "pass",
                                "managed_repeating_row_ids": ["old-row"],
                            }
                        }
                    }
                ),
                encoding="utf-8",
            )

            state = {
                "run_id": "failed",
                "status": "error",
                "reports": {
                    "target": {"status": "pass"},
                },
                "paths": {},
            }
            consolidate_current_results(
                data_root=root,
                run_state=state,
                run_id="failed",
            )

            resources = previous_stage_report(current, "resources")
            self.assertEqual(
                resources["managed_repeating_row_ids"],
                ["old-row"],
            )

    def test_full_run_shape_stays_top_level_for_worker_compatibility(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            current = root / "results" / "current"
            current.mkdir(parents=True)

            full = current / "full-run-run1.json"
            full.write_text(
                json.dumps({"status": "error", "error": "boom"}),
                encoding="utf-8",
            )

            state = {
                "run_id": "run1",
                "status": "error",
                "error": "boom",
                "reports": {"target": {"status": "pass"}},
                "paths": {},
                "full_run_report": str(full),
            }

            payload, final_path = consolidate_current_results(
                data_root=root,
                run_state=state,
                run_id="run1",
            )

            self.assertEqual(payload["status"], "error")
            self.assertEqual(payload["error"], "boom")
            self.assertIn("reports", payload)
            self.assertEqual(
                payload["full_run_report"],
                str(final_path.resolve()),
            )



if __name__ == "__main__":
    unittest.main()
