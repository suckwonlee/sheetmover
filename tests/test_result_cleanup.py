import json
import tempfile
import unittest
from pathlib import Path

from sheet_mover.result_cleanup import execute_cleanup, plan_cleanup


def _write_result(path, status="complete"):
    path.write_text(
        json.dumps({"translation_summary": {"status": status}}),
        encoding="utf-8",
    )


class ResultCleanupTests(unittest.TestCase):
    def test_keeps_latest_two_complete_results_in_current_and_archives_rest(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            names = [
                "sheet-result-123-20261001-010101.json",
                "sheet-result-123-20261002-010101.json",
                "sheet-result-123-20261003-010101.json",
            ]
            for name in names:
                _write_result(root / name)
            _write_result(
                root / "sheet-partial-20261003-020202.json",
                "partial",
            )

            plan = plan_cleanup(root, 2)
            self.assertEqual(
                {p.name for p in plan.move_to_current},
                set(names[-2:]),
            )
            self.assertIn(names[0], {p.name for p in plan.archive})
            self.assertIn(
                "sheet-partial-20261003-020202.json",
                {p.name for p in plan.archive},
            )

    def test_dry_run_moves_nothing(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            old = root / "sheet-result-123-20261001-010101.json"
            new = root / "sheet-result-123-20261002-010101.json"
            _write_result(old)
            _write_result(new)

            result = execute_cleanup(
                root,
                keep_complete_per_character=1,
                dry_run=True,
            )

            self.assertTrue(old.exists())
            self.assertTrue(new.exists())
            self.assertEqual(result["moved_current"], [])
            self.assertEqual(result["moved_archive"], [])

    def test_execute_moves_latest_to_current_and_old_to_archive(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            old = root / "sheet-result-123-20261001-010101.json"
            new = root / "sheet-result-123-20261002-010101.json"
            _write_result(old)
            _write_result(new)

            result = execute_cleanup(
                root,
                keep_complete_per_character=1,
            )

            self.assertFalse(old.exists())
            self.assertFalse(new.exists())
            self.assertTrue(
                (root / "output_archive" / old.name).is_file()
            )
            self.assertTrue(
                (root / "results" / "current" / new.name).is_file()
            )
            self.assertEqual(len(result["moved_archive"]), 1)
            self.assertEqual(len(result["moved_current"]), 1)


if __name__ == "__main__":
    unittest.main()
