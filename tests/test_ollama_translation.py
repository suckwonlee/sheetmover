import json
import tempfile
import unittest
from pathlib import Path

from sheet_mover.translator import TranslationError, Translator


class _Message:
    def __init__(self, content="", thinking=""):
        self.content = content
        self.thinking = thinking


class _Response:
    def __init__(self, content="", thinking=""):
        self.message = _Message(content, thinking)


class CaptureClient:
    def __init__(self, content, thinking=""):
        self.content = content
        self.thinking = thinking
        self.kwargs = None

    def chat(self, **kwargs):
        self.kwargs = kwargs
        return _Response(self.content, self.thinking)


class OllamaTranslationRegressionTests(unittest.TestCase):
    def _translator(self, client):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        glossary = Path(temp.name) / "glossary.json"
        glossary.write_text("{}", encoding="utf-8")
        return Translator(glossary_path=glossary, client=client)

    def test_thinking_is_disabled_for_translation(self):
        client = CaptureClient(
            json.dumps({"translation": "마법 방패"}, ensure_ascii=False)
        )
        result = self._translator(client).translate("Arcane Shield")
        self.assertEqual(result, "마법 방패")
        self.assertIs(client.kwargs["think"], False)
        self.assertIs(client.kwargs["stream"], False)
        self.assertEqual(client.kwargs["options"]["temperature"], 0)
        self.assertGreaterEqual(client.kwargs["options"]["num_predict"], 4096)

    def test_empty_content_has_clear_error(self):
        client = CaptureClient("", thinking="reasoning only")
        with self.assertRaisesRegex(
            TranslationError,
            "최종 번역문을 비워서 반환",
        ):
            self._translator(client).translate("A custom long description")


if __name__ == "__main__":
    unittest.main()
