import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from sheet_mover.__main__ import _default_output_path, _save_success_payload


class CliOutputSaveTests(unittest.TestCase):
    def test_default_output_name_uses_source_id_and_timestamp(self):
        payload = {"original": {"source_id": "170892133"}}
        path = _default_output_path(
            payload,
            now=datetime(2026, 9, 11, 16, 33, 4),
        )
        self.assertEqual(
            path.name,
            "sheet-result-170892133-20260911-163304.json",
        )

    def test_success_payload_is_written_as_utf8_json(self):
        payload = {
            "original": {
                "source_id": "170892133",
                "name": "견본 캐릭터",
            },
            "translated": {"name": "견본 캐릭터"},
        }

        with tempfile.TemporaryDirectory() as td:
            target = Path(td) / "saved.json"
            saved = _save_success_payload(payload, target)

            self.assertEqual(saved, target.resolve())
            loaded = json.loads(target.read_text(encoding="utf-8"))
            self.assertEqual(loaded, payload)


if __name__ == "__main__":
    unittest.main()
