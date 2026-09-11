import json
import tempfile
import unittest
from pathlib import Path

from sheet_mover.translator import Translator


class _Message:
    def __init__(self, content):
        self.content = content
        self.thinking = ""


class _Response:
    def __init__(self, content):
        self.message = _Message(content)


class EchoClient:
    def chat(self, **kwargs):
        payload = json.loads(kwargs["messages"][-1]["content"])
        if "items" in payload:
            return _Response(json.dumps({
                "translations": [
                    {"id": item["id"], "translation": item["source"]}
                    for item in payload["items"]
                ]
            }, ensure_ascii=False))
        return _Response(json.dumps(
            {"translation": payload["source"]},
            ensure_ascii=False,
        ))


class BilingualNameTests(unittest.TestCase):
    def _translator(self):
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        glossary = Path(td.name) / "glossary.json"
        glossary.write_text(json.dumps({
            "variant human": "변형 인간",
            "farmer": "농부",
            "fighter": "전사",
            "eldritch knight": "엘드리치 나이트",
            "greatsword": "대검",
            "thunderwave": "천둥파동",
            "action surge": "행동 연쇄",
            "second wind": "재기의 바람",
            "tough": "강인함",
        }, ensure_ascii=False), encoding="utf-8")
        return Translator(glossary_path=glossary, client=EchoClient())

    def test_character_name_remains_exact_matching_key(self):
        t = self._translator()
        result = t.translate_character({
            "name": "견본 캐릭터",
            "race": {"name": "Variant Human", "original_name": "Variant Human"},
            "background": {},
            "classes": [],
            "equipment": [],
            "spells": [],
            "features": [],
            "actions": [],
            "resources": [],
            "proficiencies": [],
            "languages": [],
        })
        self.assertEqual(result["name"], "견본 캐릭터")
        self.assertEqual(result["race"]["name"], "변형 인간 (Variant Human)")

    def test_equipment_spell_feature_action_resource_names_show_original(self):
        t = self._translator()
        result = t.translate_character({
            "name": "견본 캐릭터",
            "race": {},
            "background": {},
            "classes": [],
            "equipment": [{"name": "Greatsword", "original_name": "Greatsword", "description": ""}],
            "spells": [{"name": "Thunderwave", "original_name": "Thunderwave", "description": ""}],
            "features": [{"name": "Action Surge", "original_name": "Action Surge", "description": ""}],
            "actions": [{"name": "Second Wind", "original_name": "Second Wind", "description": ""}],
            "resources": [{"name": "Second Wind", "original_name": "Second Wind"}],
            "proficiencies": [],
            "languages": [],
        })
        self.assertEqual(result["equipment"][0]["name"], "대검 (Greatsword)")
        self.assertEqual(result["spells"][0]["name"], "천둥파동 (Thunderwave)")
        self.assertEqual(result["features"][0]["name"], "행동 연쇄 (Action Surge)")
        self.assertEqual(result["actions"][0]["name"], "재기의 바람 (Second Wind)")
        self.assertEqual(result["resources"][0]["name"], "재기의 바람 (Second Wind)")

    def test_class_subclass_and_background_names_are_bilingual(self):
        t = self._translator()
        result = t.translate_character({
            "name": "견본 캐릭터",
            "race": {},
            "background": {
                "name": "Farmer",
                "original_name": "Farmer",
                "feature_name": "Tough",
                "original_feature_name": "Tough",
            },
            "classes": [{
                "name": "Fighter",
                "original_name": "Fighter",
                "subclass_name": "Eldritch Knight",
                "original_subclass_name": "Eldritch Knight",
            }],
            "equipment": [],
            "spells": [],
            "features": [],
            "actions": [],
            "resources": [],
            "proficiencies": [],
            "languages": [],
        })
        self.assertEqual(result["background"]["name"], "농부 (Farmer)")
        self.assertEqual(result["background"]["feature_name"], "강인함 (Tough)")
        self.assertEqual(result["classes"][0]["name"], "전사 (Fighter)")
        self.assertEqual(
            result["classes"][0]["subclass_name"],
            "엘드리치 나이트 (Eldritch Knight)",
        )

    def test_original_name_field_is_never_modified(self):
        t = self._translator()
        result = t.translate_character({
            "name": "견본 캐릭터",
            "race": {},
            "background": {},
            "classes": [],
            "equipment": [{"name": "Greatsword", "original_name": "Greatsword", "description": ""}],
            "spells": [],
            "features": [],
            "actions": [],
            "resources": [],
            "proficiencies": [],
            "languages": [],
        })
        self.assertEqual(result["equipment"][0]["original_name"], "Greatsword")


if __name__ == "__main__":
    unittest.main()
