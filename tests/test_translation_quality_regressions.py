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


class RecordingClient:
    def __init__(self):
        self.sources = []

    def chat(self, **kwargs):
        payload = json.loads(kwargs["messages"][-1]["content"])
        if "items" in payload:
            rows = []
            for item in payload["items"]:
                self.sources.append(item["source"])
                rows.append({
                    "id": item["id"],
                    "translation": item["source"]
                        .replace("Proficiency Bonus", "숙련 보너스")
                        .replace("proficiency bonus", "숙련 보너스")
                        .replace("attack roll", "명중 굴림")
                        .replace("Greatsword", "대검")
                        .replace("weapon", "무기")
                        .replace("damage", "피해"),
                })
            return R(json.dumps({"translations": rows}, ensure_ascii=False))

        source = payload["source"]
        self.sources.append(source)
        translated = (
            source
            .replace("Proficiency Bonus", "숙련 보너스")
            .replace("proficiency bonus", "숙련 보너스")
            .replace("attack roll", "명중 굴림")
            .replace("Greatsword", "대검")
            .replace("weapon", "무기")
            .replace("damage", "피해")
        )
        return R(json.dumps({"translation": translated}, ensure_ascii=False))


class QualityRegressionTests(unittest.TestCase):
    def translator(self, client):
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        g = Path(td.name) / "glossary.json"
        g.write_text(json.dumps({
            "proficiency bonus": "숙련 보너스",
            "attack roll": "명중 굴림",
            "greatsword": "대검",
            "weapon": "무기",
            "damage": "피해",
            "topple": "넘어뜨리기",
            "trident": "삼지창",
        }, ensure_ascii=False), encoding="utf-8")
        return Translator(glossary_path=g, client=client)

    def test_prose_keeps_english_context_visible_to_model(self):
        client = RecordingClient()
        translator = self.translator(client)
        translator.translate(
            "Proficiency with a Greatsword lets you add your "
            "proficiency bonus to the attack roll."
        )
        self.assertTrue(client.sources)
        joined = "\n".join(client.sources)
        self.assertIn("Greatsword", joined)
        self.assertIn("attack roll", joined)
        self.assertNotIn("__SHEETMOVER_GLOSSARY_", joined)

    def test_topple_trident_never_calls_model(self):
        client = RecordingClient()
        translator = self.translator(client)
        self.assertEqual(
            translator.translate("Topple (Trident)"),
            "넘어뜨리기 (삼지창)",
        )
        self.assertEqual(client.sources, [])


if __name__ == "__main__":
    unittest.main()
