import json
import tempfile
import unittest
from pathlib import Path

from sheet_mover.translator import (
    TranslationError,
    Translator,
    protect_text,
    restore_text,
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

    def chat(self, **kwargs):
        self.kwargs = kwargs
        payload = json.loads(kwargs["messages"][-1]["content"])
        translated = self.transform(payload["source"])
        return _Response(
            json.dumps(
                {"translation": translated},
                ensure_ascii=False,
            )
        )


class ProtectedTokenTests(unittest.TestCase):
    def _translator(self, client):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        glossary = Path(temp.name) / "glossary.json"
        glossary.write_text("{}", encoding="utf-8")
        return Translator(glossary_path=glossary, client=client)

    def test_protect_restore_round_trip(self):
        source = (
            '<p class="x">Deal 1d6 + 3 damage.</p>'
            ' <strong>Range 30</strong> [[1d20+5]]'
        )
        protected, replacements = protect_text(source)
        self.assertNotIn("<p", protected)
        self.assertNotIn("1d6", protected)
        self.assertNotIn("[[1d20+5]]", protected)
        self.assertEqual(
            restore_text(protected, replacements),
            source,
        )

    def test_translation_preserves_html_numbers_dice_exactly(self):
        source = (
            '<p class="characters-statblock" style="font-family: Roboto Condensed;">'
            'Humans gain +1 and deal 1d6 damage.</p>'
        )

        client = SmartFakeClient(
            lambda value: value.replace(
                "Humans gain ",
                "인간은 ",
            ).replace(
                " and deal ",
                "을 얻고 ",
            ).replace(
                " damage.",
                " 피해를 줍니다.",
            )
        )

        result = self._translator(client).translate(source)

        self.assertIn(
            '<p class="characters-statblock" style="font-family: Roboto Condensed;">',
            result,
        )
        self.assertIn("</p>", result)
        self.assertIn("+1", result)
        self.assertIn("1d6", result)

    def test_changed_placeholder_falls_back_to_fragment_translation(self):
        source = "<p>Deal 1d6 damage.</p>"

        def transform(value):
            if "__SHEETMOVER_PROTECTED_" in value:
                # Simulate the real Qwen failure: placeholder corruption.
                return value.replace(
                    "__SHEETMOVER_PROTECTED_0000__",
                    "__BROKEN__",
                )

            # Fallback receives only plain English fragments; protected HTML and
            # dice never reach the model.
            if value == "Deal":
                return "가함"
            if value == "damage.":
                return "피해."
            return value

        client = SmartFakeClient(transform)
        result = self._translator(client).translate(source)

        self.assertEqual(result, "<p>가함 1d6 피해.</p>")


if __name__ == "__main__":
    unittest.main()
