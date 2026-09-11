import json
import tempfile
import unittest
from pathlib import Path

from sheet_mover.translator import Translator


class M:
    def __init__(self, content):
        self.content = content
        self.thinking = ""


class R:
    def __init__(self, content):
        self.message = M(content)


class TerminologyAwareClient:
    def __init__(self):
        self.sources = []

    def chat(self, **kwargs):
        payload = json.loads(kwargs["messages"][-1]["content"])

        if "items" in payload:
            rows = []
            for item in payload["items"]:
                source = item["source"]
                self.sources.append(source)
                translated = (
                    source
                    .replace("spell slots", "주문 슬롯")
                    .replace("Spell Slots", "주문 슬롯")
                    .replace("Long Rest", "긴 휴식")
                    .replace("Fighter", "전사")
                    .replace("Martial Weapons", "군용 무기")
                    .replace("Hit Point Die", "생명 주사위")
                )
                rows.append({
                    "id": item["id"],
                    "translation": translated,
                })
            return R(json.dumps(
                {"translations": rows},
                ensure_ascii=False,
            ))

        source = payload["source"]
        self.sources.append(source)
        translated = (
            source
            .replace("spell slots", "주문 슬롯")
            .replace("Spell Slots", "주문 슬롯")
            .replace("Long Rest", "긴 휴식")
            .replace("Fighter", "전사")
            .replace("Martial Weapons", "군용 무기")
            .replace("Hit Point Die", "생명 주사위")
        )
        return R(json.dumps(
            {"translation": translated},
            ensure_ascii=False,
        ))


class Tests(unittest.TestCase):
    def t(self, client):
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        g = Path(td.name) / "glossary.json"
        g.write_text(json.dumps({
            "fighter": "전사",
            "spell slots": "주문 슬롯",
            "long rest": "긴 휴식",
            "martial weapons": "군용 무기",
            "hit point die": "생명 주사위",
            "thunderwave": "천둥파동",
            "topple": "넘어뜨리기",
            "trident": "삼지창",
        }, ensure_ascii=False), encoding="utf-8")
        return Translator(glossary_path=g, client=client)

    def test_sentence_terms_are_visible_to_model_not_opaque_tokens(self):
        client = TerminologyAwareClient()
        result = self.t(client).translate(
            "You regain all expended spell slots when you finish a Long Rest."
        )
        self.assertIn("주문 슬롯", result)
        self.assertIn("긴 휴식", result)
        self.assertTrue(client.sources)
        self.assertFalse(
            any("__SHEETMOVER_GLOSSARY_" in s for s in client.sources)
        )

    def test_longest_terms_are_required(self):
        client = TerminologyAwareClient()
        result = self.t(client).translate(
            "Fighter uses Martial Weapons and a Hit Point Die."
        )
        self.assertIn("전사", result)
        self.assertIn("군용 무기", result)
        self.assertIn("생명 주사위", result)

    def test_exact_name_skips_model(self):
        client = TerminologyAwareClient()
        result = self.t(client).translate("Thunderwave")
        self.assertEqual(result, "천둥파동")
        self.assertEqual(client.sources, [])

    def test_composite_name_skips_model(self):
        client = TerminologyAwareClient()
        result = self.t(client).translate("Topple (Trident)")
        self.assertEqual(result, "넘어뜨리기 (삼지창)")
        self.assertEqual(client.sources, [])


if __name__ == "__main__":
    unittest.main()
