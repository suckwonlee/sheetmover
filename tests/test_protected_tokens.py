import json
import tempfile
import unittest
from pathlib import Path

from sheet_mover.translator import (
    Translator,
    protect_text,
    restore_text,
    mechanical_tokens,
)


class _Message:
    def __init__(self, content="", thinking=""):
        self.content = content
        self.thinking = thinking


class _Response:
    def __init__(self, content="", thinking=""):
        self.message = _Message(content, thinking)


class SmartFakeClient:
    def __init__(self, transform):
        self.transform = transform
        self.kwargs = None
        self.seen = []

    def chat(self, **kwargs):
        self.kwargs = kwargs
        payload = json.loads(kwargs["messages"][-1]["content"])
        source = payload["source"]
        self.seen.append(source)
        translated = self.transform(source)
        return _Response(json.dumps({"translation": translated}, ensure_ascii=False))


class ProtectedTokenTests(unittest.TestCase):
    def _translator(self, client):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        glossary = Path(temp.name) / "glossary.json"
        glossary.write_text("{}", encoding="utf-8")
        return Translator(glossary_path=glossary, client=client)

    def test_legacy_protect_restore_round_trip_still_available(self):
        source = (
            '<p class="x">Deal 1d6 + 3 damage.</p>'
            ' <strong>Range 30</strong> [[1d20+5]]'
        )
        protected, replacements = protect_text(source)
        self.assertNotIn("<p", protected)
        self.assertNotIn("1d6", protected)
        self.assertNotIn("[[1d20+5]]", protected)
        self.assertEqual(restore_text(protected, replacements), source)

    def test_live_translation_keeps_numbers_and_dice_visible(self):
        source = '<p>Deal 1d6 + 3 damage at 30 feet.</p>'

        client = SmartFakeClient(
            lambda value: value.replace("Deal", "가함").replace(
                "damage at", "피해를 사거리"
            ).replace("feet.", "피트에서 줍니다.")
        )
        result = self._translator(client).translate(source)

        self.assertEqual(mechanical_tokens(source), mechanical_tokens(result))
        self.assertTrue(client.seen)
        joined = "\n".join(client.seen)
        self.assertIn("1d6", joined)
        self.assertIn("+ 3", joined)
        self.assertIn("30", joined)
        self.assertNotIn("__SHEETMOVER_PROTECTED_", joined)

    def test_roll20_formula_is_still_protected(self):
        source = "Roll [[1d20+5]] and deal 1d6 damage."

        client = SmartFakeClient(
            lambda value: value.replace("Roll", "굴리고").replace(
                "and deal", "그리고"
            ).replace("damage.", "피해를 줍니다.")
        )
        result = self._translator(client).translate(source)

        self.assertIn("[[1d20+5]]", result)
        self.assertIn("1d6", result)
        self.assertTrue(any("__SHEETMOVER_PROTECTED_" in s for s in client.seen))


if __name__ == "__main__":
    unittest.main()
