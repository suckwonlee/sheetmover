import json
import tempfile
import unittest
from pathlib import Path

from sheet_mover.translator import TranslationError, Translator


class _Message:
    def __init__(self, content):
        self.content = content
        self.thinking = ""


class _Response:
    def __init__(self, content):
        self.message = _Message(content)


class MissesGlossaryClient:
    def chat(self, **kwargs):
        payload = json.loads(kwargs["messages"][-1]["content"])
        if "items" in payload:
            return _Response(json.dumps(
                {
                    "translations": [
                        {
                            "id": item["id"],
                            "translation": "자연스러운 한국어 문장입니다.",
                        }
                        for item in payload["items"]
                    ]
                },
                ensure_ascii=False,
            ))
        return _Response(json.dumps(
            {"translation": "자연스러운 한국어 문장입니다."},
            ensure_ascii=False,
        ))


class ConnectionFailureClient:
    def __init__(self):
        self.calls = 0

    def chat(self, **kwargs):
        self.calls += 1
        raise RuntimeError("connection refused")


class AlwaysBadStructuredClient:
    def chat(self, **kwargs):
        payload = json.loads(kwargs["messages"][-1]["content"])
        if "items" in payload:
            return _Response("{}")
        return _Response(
            json.dumps({"translation": ""}, ensure_ascii=False)
        )


class PreflightHardeningTests(unittest.TestCase):
    def _translator(self, client, glossary=None):
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        path = Path(td.name) / "glossary.json"
        path.write_text(
            json.dumps(glossary or {}, ensure_ascii=False),
            encoding="utf-8",
        )
        return Translator(glossary_path=path, client=client)

    def test_glossary_miss_keeps_natural_sentence_not_word_salad(self):
        translator = self._translator(
            MissesGlossaryClient(),
            {"attack roll": "명중 굴림"},
        )
        result = translator.translate(
            "Make an attack roll against the creature."
        )

        self.assertEqual(result, "자연스러운 한국어 문장입니다.")
        self.assertTrue(
            any(
                "문장 구조 보존" in warning
                for warning in translator.warnings
            )
        )

    def test_connection_failure_is_not_recursively_split(self):
        client = ConnectionFailureClient()
        translator = self._translator(client)

        with self.assertRaises(TranslationError):
            translator._translate_batch_resilient(
                ["first sentence", "second sentence"]
            )

        self.assertEqual(client.calls, 1)

    def test_second_soft_failure_on_structured_item_preserves_original(self):
        translator = self._translator(AlwaysBadStructuredClient())
        source = "<p>This is a structured description.</p>"

        result = translator._translate_batch_resilient([source])

        self.assertEqual(result[source], source)
        self.assertTrue(translator.warnings)


if __name__ == "__main__":
    unittest.main()
