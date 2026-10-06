import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from sheet_mover.result_cleanup import execute_cleanup, plan_cleanup
from sheet_mover.result_store import default_result_path, latest_complete_result


def _write_result(path, source_id="170892133", status="complete"):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "original": {"source_id": source_id, "name": "견본 캐릭터"},
        "translation_summary": {"status": status},
    }, ensure_ascii=False), encoding="utf-8")


class Stage4ResultTests(unittest.TestCase):
    def test_default_path_is_results_current(self):
        path = default_result_path("170892133", now=datetime(2026, 10, 6, 13, 27, 14))
        self.assertEqual(path.as_posix(), "results/current/sheet-result-170892133-20261006-132714.json")

    def test_latest_prefers_newer_current_over_legacy_root(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            old = root / "sheet-result-170892133-20261006-120034.json"
            new = root / "results/current/sheet-result-170892133-20261006-132714.json"
            _write_result(old)
            _write_result(new)
            self.assertEqual(latest_complete_result(root), new)

    def test_cleanup_keeps_two_newest_in_current(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            p1 = root / "sheet-result-170892133-20261006-120034.json"
            p2 = root / "sheet-result-170892133-20261006-132651.json"
            p3 = root / "sheet-result-170892133-20261006-132714.json"
            for p in (p1, p2, p3):
                _write_result(p)
            plan = plan_cleanup(root, 2)
            self.assertEqual({p.name for p in plan.move_to_current}, {p2.name, p3.name})
            self.assertEqual({p.name for p in plan.archive}, {p1.name})
            execute_cleanup(root, keep_complete_per_character=2)
            current = root / "results/current"
            self.assertTrue((current / p2.name).is_file())
            self.assertTrue((current / p3.name).is_file())
            self.assertTrue((root / "output_archive" / p1.name).is_file())

    def test_partial_goes_to_archive(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            partial = root / "sheet-partial-20261006-130000.json"
            partial.write_text("{}", encoding="utf-8")
            plan = plan_cleanup(root, 2)
            self.assertIn(partial, plan.archive)


if __name__ == "__main__":
    unittest.main()
