import json
import tempfile
import unittest
from pathlib import Path

from sheet_mover.source import normalize_character
from sheet_mover.translator import Translator


class _Message:
    def __init__(self, content):
        self.content = content
        self.thinking = ""


class _Response:
    def __init__(self, content):
        self.message = _Message(content)


class TranslationClient:
    def __init__(self):
        self.sources = []

    @staticmethod
    def convert(source):
        replacements = {
            "Strength Saving Throws": "근력 내성 굴림",
            "Acrobatics": "곡예",
            "Thrown": "투척",
            "If a weapon has the Thrown property, you can throw the weapon to make a ranged attack.":
                "무기에 투척 속성이 있으면 무기를 던져 원거리 공격을 할 수 있습니다.",
            "The damage increases by 1d8 for each spell slot level above 1.":
                "1레벨을 초과하는 주문 슬롯 레벨마다 피해가 1d8 증가합니다.",
        }
        return replacements.get(source, source)

    def chat(self, **kwargs):
        payload = json.loads(kwargs["messages"][-1]["content"])
        if "items" in payload:
            rows = []
            for item in payload["items"]:
                self.sources.append(item["source"])
                rows.append({
                    "id": item["id"],
                    "translation": self.convert(item["source"]),
                })
            return _Response(json.dumps({"translations": rows}, ensure_ascii=False))

        source = payload["source"]
        self.sources.append(source)
        return _Response(json.dumps(
            {"translation": self.convert(source)}, ensure_ascii=False
        ))


class FinalPipelineTests(unittest.TestCase):
    def _translator(self, client, glossary_data=None):
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        glossary = Path(td.name) / "glossary.json"
        glossary.write_text(
            json.dumps(glossary_data or {}, ensure_ascii=False),
            encoding="utf-8",
        )
        return Translator(glossary_path=glossary, client=client)

    def test_context_sensitive_single_words_are_not_hard_forced_in_prose(self):
        translator = self._translator(
            TranslationClient(),
            {
                "common": "공용어",
                "reach": "도달거리",
                "force": "역장",
                "uses": "사용 횟수",
                "saving throw": "내성 굴림",
            },
        )
        pairs = translator._required_glossary_pairs(
            "Common people can force a creature when they reach it; "
            "the feature uses a saving throw."
        )
        self.assertEqual(pairs, [("saving throw", "내성 굴림")])

    def test_character_translates_new_skill_save_and_weapon_property_fields(self):
        client = TranslationClient()
        translator = self._translator(client)
        source = {
            "name": "견본 캐릭터",
            "race": {}, "background": {}, "classes": [],
            "equipment": [{
                "name": "Javelin", "original_name": "Javelin", "description": "",
                "properties": [{
                    "name": "Thrown",
                    "original_name": "Thrown",
                    "description": "If a weapon has the Thrown property, you can throw the weapon to make a ranged attack.",
                    "notes": None,
                }],
            }],
            "spells": [], "features": [], "actions": [], "resources": [],
            "proficiencies": [], "languages": [],
            "saving_throw_proficiencies": ["Strength Saving Throws"],
            "skill_proficiencies": [{
                "name": "Acrobatics", "original_name": "Acrobatics", "subtype": "acrobatics"
            }],
        }
        result = translator.translate_character(source)
        self.assertEqual(result["saving_throw_proficiencies"], ["근력 내성 굴림"])
        self.assertEqual(result["skill_proficiencies"][0]["name"], "곡예")
        self.assertEqual(result["equipment"][0]["properties"][0]["name"], "투척")
        self.assertIn("원거리 공격", result["equipment"][0]["properties"][0]["description"])

    def test_numeric_spell_scaling_sentence_can_translate_as_one_sentence(self):
        client = TranslationClient()
        translator = self._translator(client)
        source = "The damage increases by 1d8 for each spell slot level above 1."
        result = translator.translate(source)
        self.assertEqual(result, "1레벨을 초과하는 주문 슬롯 레벨마다 피해가 1d8 증가합니다.")
        self.assertTrue(any("1d8" in value for value in client.sources))
        self.assertFalse(any("__SHEETMOVER_PROTECTED_" in value for value in client.sources))

    def test_source_preserves_nested_original_names(self):
        raw = {
            "id": 1,
            "name": "T",
            "race": {}, "background": {}, "classes": [],
            "inventory": [{
                "id": 10,
                "quantity": 1,
                "definition": {
                    "id": 20,
                    "name": "Javelin",
                    "description": "",
                    "properties": [{"id": 10, "name": "Thrown", "description": "x"}],
                },
            }],
            "modifiers": {
                "race": [{
                    "type": "proficiency",
                    "subType": "acrobatics",
                    "friendlySubtypeName": "Acrobatics",
                    "componentId": 65,
                }],
            },
        }
        sheet = normalize_character(raw)
        self.assertEqual(sheet.equipment[0]["properties"][0]["original_name"], "Thrown")
        self.assertEqual(sheet.skill_proficiencies[0]["original_name"], "Acrobatics")


if __name__ == "__main__":
    unittest.main()
