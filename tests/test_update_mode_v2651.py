# -*- coding: utf-8 -*-
import json
from pathlib import Path
import tempfile
import unittest

from sheet_mover import update_mode


class UpdateModeV2651Tests(unittest.TestCase):
    def _raw(self, level=5, extra_spell=False):
        spells = [
            {
                "id": 1,
                "definition": {
                    "id": 100,
                    "name": "Bless",
                    "level": 1,
                    "isLegacy": True,
                },
            }
        ]
        if extra_spell:
            spells.append(
                {
                    "id": 2,
                    "definition": {
                        "id": 101,
                        "name": "Aid",
                        "level": 2,
                        "isLegacy": True,
                    },
                }
            )
        return {
            "id": 123,
            "name": "Same Hero",
            "classes": [
                {
                    "level": level,
                    "definition": {"id": 3, "name": "Paladin"},
                    "subclassDefinition": {"id": 7, "name": "Oath"},
                }
            ],
            "spells": {},
            "classSpells": [{"characterClassId": 9, "spells": spells}],
            "inventory": [],
        }

    def test_level_increase_is_probable_level_up(self):
        report = update_mode.compare_raw_sources(self._raw(5), self._raw(6, True))
        self.assertTrue(report["probable_level_up"])
        self.assertEqual(report["level_delta"], 1)
        self.assertIn("Aid", report["added_spells"])
        self.assertIn("classes", report["changed_sections"])
        self.assertTrue(report["previous_raw_sha256"])
        self.assertTrue(report["current_raw_sha256"])

    def test_same_name_different_ddb_id_is_rejected(self):
        old = self._raw()
        new = self._raw()
        new["id"] = 999
        with self.assertRaisesRegex(RuntimeError, "ID"):
            update_mode.compare_raw_sources(old, new)

    def test_previous_result_requires_same_id_and_name(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            cache = root / "results" / "cache"
            cache.mkdir(parents=True)
            payload = {
                "original": {"source_id": "123", "name": "Same Hero"},
                "translated": {
                    "translation_summary": {"status": "complete"},
                },
                "translation_summary": {"status": "complete"},
                "raw_source": self._raw(),
            }
            path = cache / "sheet-result-123-20261008-120000.json"
            path.write_text(json.dumps(payload), encoding="utf-8")

            found_payload, found_path = update_mode.find_previous_result(
                data_root=root,
                source_id="123",
                character_name="Same Hero",
            )
            self.assertIsNotNone(found_payload)
            self.assertEqual(found_path, path)

            not_found, not_found_path = update_mode.find_previous_result(
                data_root=root,
                source_id="123",
                character_name="Other Hero",
            )
            self.assertIsNone(not_found)
            self.assertIsNone(not_found_path)

    def test_seed_reuses_only_exact_unchanged_source_text(self):
        previous = {
            "original": {
                "source_id": "123",
                "name": "Same Hero",
                "spells": [
                    {
                        "name": "Bane",
                        "description": "Choose a creature in range.",
                    },
                    {
                        "name": "Misty Step",
                        "description": "Briefly surrounded by silvery mist.",
                    },
                ],
            },
            "translated": {
                "source_id": "123",
                "name": "Same Hero",
                "spells": [
                    {
                        "name": "재앙 (Bane)",
                        "description": "사거리 내의 생명체를 선택합니다.",
                    },
                    {
                        # English fallback must not become a seed.
                        "name": "Misty Step",
                        "description": "은빛 안개가 잠시 감쌉니다.",
                    },
                ],
            },
        }
        current = {
            "source_id": "123",
            "name": "Same Hero",
            "spells": [
                {
                    "name": "Bane",
                    # Changed rule text: old description must not be reused.
                    "description": "Choose two creatures in range.",
                },
                {
                    "name": "Misty Step",
                    "description": "Briefly surrounded by silvery mist.",
                },
            ],
        }
        seed, report = update_mode.build_verified_translation_seed(previous, current)
        self.assertEqual(seed.get("Bane"), "재앙 (Bane)")
        self.assertNotIn("Choose a creature in range.", seed)
        self.assertNotIn("Misty Step", seed)
        self.assertEqual(
            seed.get("Briefly surrounded by silvery mist."),
            "은빛 안개가 잠시 감쌉니다.",
        )
        self.assertGreaterEqual(report["seeded_count"], 2)

    def test_same_name_two_editions_do_not_cross_reuse_descriptions(self):
        previous = {
            "original": {
                "source_id": "123",
                "name": "Same Hero",
                "spells": [
                    {"name": "Eldritch Blast", "description": "Legacy beam rule."},
                    {"name": "Eldritch Blast", "description": "Revised beam rule."},
                ],
            },
            "translated": {
                "source_id": "123",
                "name": "Same Hero",
                "spells": [
                    {"name": "섬뜩한 방출 (Eldritch Blast)", "description": "구판 광선 규칙입니다."},
                    {"name": "섬뜩한 방출 (Eldritch Blast)", "description": "개정판 광선 규칙입니다."},
                ],
            },
        }
        current = previous["original"]
        seed, _report = update_mode.build_verified_translation_seed(previous, current)
        self.assertEqual(seed["Legacy beam rule."], "구판 광선 규칙입니다.")
        self.assertEqual(seed["Revised beam rule."], "개정판 광선 규칙입니다.")
        self.assertEqual(seed["Eldritch Blast"], "섬뜩한 방출 (Eldritch Blast)")

    def test_conflicting_previous_translation_is_not_reused(self):
        previous = {
            "original": {
                "source_id": "123",
                "name": "Same Hero",
                "values": ["Shield", "Shield"],
            },
            "translated": {
                "source_id": "123",
                "name": "Same Hero",
                "values": ["방패 (Shield)", "실드 (Shield)"],
            },
        }
        current = {
            "source_id": "123",
            "name": "Same Hero",
            "values": ["Shield"],
        }
        seed, report = update_mode.build_verified_translation_seed(previous, current)
        self.assertNotIn("Shield", seed)
        self.assertEqual(report["conflict_count"], 1)

    def test_prime_translator_uses_resolved_map_without_touching_google_cache(self):
        previous = {
            "original": {
                "source_id": "123",
                "name": "Same Hero",
                "values": ["Bane"],
            },
            "translated": {
                "source_id": "123",
                "name": "Same Hero",
                "values": ["재앙 (Bane)"],
            },
        }
        current = {
            "source_id": "123",
            "name": "Same Hero",
            "values": ["Bane"],
        }

        class FakeTranslator:
            def __init__(self):
                self._equivalent_final = {}
                self.cache = {"do-not-touch": "x"}

        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "previous.json"
            path.write_text(json.dumps(previous, ensure_ascii=False), encoding="utf-8")
            translator = FakeTranslator()
            report = update_mode.prime_translator_from_previous_result(
                translator,
                path,
                current,
            )

        self.assertEqual(translator._equivalent_final["Bane"], "재앙 (Bane)")
        self.assertEqual(translator.cache, {"do-not-touch": "x"})
        self.assertEqual(report["inserted_count"], 1)

    def test_ui_and_worker_paths_are_backward_compatible(self):
        ui = (Path("sheet_mover") / "ui.py").read_text(encoding="utf-8")
        full_run = (Path("sheet_mover") / "full_run.py").read_text(encoding="utf-8")
        mover = (Path("sheet_mover") / "mover.py").read_text(encoding="utf-8")
        run_log = (Path("sheet_mover") / "run_log.py").read_text(encoding="utf-8")

        self.assertIn("기존 시트 이동 결과를 기준으로 변경사항 업데이트", ui)
        self.assertIn('getattr(self, "update_existing_var", None)', ui)
        self.assertIn('command.append("--update-existing")', ui)
        self.assertIn('parser.add_argument("--update-existing"', full_run)
        self.assertIn("update_existing=False", full_run)
        self.assertIn("translation_seed_path=previous_result_path", full_run)
        self.assertIn("translation_seed_path=None", mover)
        self.assertIn("prime_translator_from_previous_result", mover)
        self.assertIn("update_existing=False", run_log)

    def test_hybrid_translator_is_not_modified_by_update_mode(self):
        # Regression guard: v2.6.5 changed cache-only semantics and broke an
        # existing recovered-miss invariant. v2.6.5.1 must integrate outside
        # hybrid_translator.py instead.
        source = (Path("sheet_mover") / "hybrid_translator.py").read_text(encoding="utf-8")
        self.assertNotIn("def _previous_result_seed_enabled", source)


if __name__ == "__main__":
    unittest.main()
