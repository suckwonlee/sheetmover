# -*- coding: utf-8 -*-
import unittest

from sheet_mover.runtime_integrity_v264 import refresh_cached_spell_metadata


class RuntimeIntegrityV264Tests(unittest.TestCase):
    def test_cache_refresh_preserves_translated_text_and_adds_new_source_spell(self):
        payload = {
            "original": {
                "source_id": "1",
                "name": "Sample",
                "spells": [
                    {
                        "source_id": "existing",
                        "definition_id": "10",
                        "name": "Existing",
                        "original_name": "Existing",
                        "description": "English old",
                        "level": 1,
                        "prepared": True,
                        "always_prepared": True,
                        "uses_spell_slot": True,
                        "grant_type": "always_prepared",
                    },
                    {
                        "source_id": "subclass-grant:1:2:20",
                        "definition_id": "20",
                        "name": "Bane",
                        "original_name": "Bane",
                        "description": "English Bane rules",
                        "level": 1,
                        "prepared": True,
                        "always_prepared": True,
                        "uses_spell_slot": True,
                        "grant_type": "always_prepared",
                    },
                ],
            },
            "translated": {
                "source_id": "1",
                "name": "Sample",
                "spells": [
                    {
                        "source_id": "existing",
                        "definition_id": "10",
                        "name": "기존 (Existing)",
                        "original_name": "Existing",
                        "description": "번역된 설명",
                        "level": 1,
                        "prepared": False,
                        "always_prepared": False,
                        "uses_spell_slot": True,
                    }
                ],
            },
        }

        refreshed, report = refresh_cached_spell_metadata(payload)
        rows = refreshed["translated"]["spells"]
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["name"], "기존 (Existing)")
        self.assertEqual(rows[0]["description"], "번역된 설명")
        self.assertTrue(rows[0]["always_prepared"])
        self.assertEqual(rows[0]["grant_type"], "always_prepared")
        self.assertEqual(rows[1]["name"], "Bane")
        self.assertEqual(report["added_count"], 1)
        self.assertEqual(report["refreshed_count"], 1)


if __name__ == "__main__":
    unittest.main()
