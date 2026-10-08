import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from sheet_mover import update_mode


class UpdateModeV265Tests(unittest.TestCase):
    def _raw(self, level=5, extra_spell=False):
        spells = [{"id": 1, "definition": {"id": 100, "name": "Bless", "level": 1, "isLegacy": True}}]
        if extra_spell:
            spells.append({"id": 2, "definition": {"id": 101, "name": "Aid", "level": 2, "isLegacy": True}})
        return {
            "id": 123,
            "name": "Same Hero",
            "classes": [{
                "level": level,
                "definition": {"id": 3, "name": "Paladin"},
                "subclassDefinition": {"id": 7, "name": "Oath"},
            }],
            "spells": {},
            "classSpells": [{"characterClassId": 9, "spells": spells}],
            "inventory": [],
        }

    def test_level_increase_is_reported_as_probable_level_up(self):
        report = update_mode.compare_raw_sources(self._raw(5), self._raw(6, True))
        self.assertTrue(report["probable_level_up"])
        self.assertEqual(report["level_delta"], 1)
        self.assertIn("Aid", report["added_spells"])
        self.assertIn("classes", report["changed_sections"])

    def test_same_name_different_id_is_rejected(self):
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
            good = {
                "original": {"source_id": "123", "name": "Same Hero"},
                "translated": {"translation_summary": {"status": "complete"}},
                "translation_summary": {"status": "complete"},
                "raw_source": self._raw(),
            }
            path = cache / "sheet-result-123-20261008-120000.json"
            path.write_text(json.dumps(good), encoding="utf-8")
            payload, found = update_mode.find_previous_result(
                data_root=root, source_id="123", character_name="Same Hero"
            )
            self.assertIsNotNone(payload)
            self.assertEqual(found, path)
            payload2, found2 = update_mode.find_previous_result(
                data_root=root, source_id="123", character_name="Other Hero"
            )
            self.assertIsNone(payload2)
            self.assertIsNone(found2)

    def test_ui_contains_update_checkbox_and_worker_flag(self):
        source = (Path("sheet_mover") / "ui.py").read_text(encoding="utf-8")
        self.assertIn("기존 시트 이동 결과를 기준으로 변경사항 업데이트", source)
        self.assertIn("--update-existing", source)

    def test_full_run_has_update_cli_flag_and_seed_context(self):
        source = (Path("sheet_mover") / "full_run.py").read_text(encoding="utf-8")
        self.assertIn('parser.add_argument("--update-existing"', source)
        self.assertIn("prepare_update_context", source)
        self.assertIn("activate_previous_translation_seed", source)

    def test_online_translator_can_reuse_explicit_previous_direct_seed(self):
        import sheet_mover.hybrid_translator as hybrid

        class FakeGoogle:
            def __init__(self):
                self.requests = []

            def translate_text(self, request=None, timeout=None):
                self.requests.append(request)
                raise AssertionError("explicit previous seed should avoid Google for an exact reused value")

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            glossary = root / "glossary.json"
            glossary.write_text("{}", encoding="utf-8")
            with mock.patch.dict(
                "os.environ",
                {
                    "SHEETMOVER_TRANSLATION_CACHE": str(root / "google-cache.json"),
                    "SHEETMOVER_REVIEW_CACHE": str(root / "review-cache.json"),
                },
            ):
                translator = hybrid.Translator(
                    glossary_path=glossary,
                    client=FakeGoogle(),
                    project_id="test-project",
                    review_client=mock.Mock(),
                    cache_only=False,
                )
            translator._cache_only_direct_seed["Bane"] = "재앙"
            with mock.patch.object(hybrid, "SEMANTIC_SEED_RESULT", "previous.json"), \
                 mock.patch.object(translator, "_ensure_ollama_ready", return_value=None), \
                 mock.patch.object(translator, "_review_translation_if_needed", side_effect=lambda _s, t: t):
                resolved, failed = translator._translate_batch_once(["Bane"])
            self.assertEqual(resolved["Bane"], "재앙")
            self.assertEqual(failed, [])


if __name__ == "__main__":
    unittest.main()
