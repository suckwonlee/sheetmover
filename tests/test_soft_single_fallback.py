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


class AlwaysEmptyClient:
    def chat(self, **kwargs):
        payload = json.loads(kwargs["messages"][-1]["content"])

        if "items" in payload:
            return _Response(
                json.dumps(
                    {
                        "translations": [
                            {
                                "id": item["id"],
                                "translation": "",
                            }
                            for item in payload["items"]
                        ]
                    },
                    ensure_ascii=False,
                )
            )

        return _Response(
            json.dumps(
                {"translation": ""},
                ensure_ascii=False,
            )
        )


class NetworkFailureClient:
    def chat(self, **kwargs):
        raise RuntimeError("connection refused")


class SoftSingleFallbackTests(unittest.TestCase):
    def _translator(self, client):
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        glossary = Path(td.name) / "glossary.json"
        glossary.write_text(
            json.dumps(
                {
                    "fighter": "전사",
                    "thunderwave": "천둥파동",
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        return Translator(
            glossary_path=glossary,
            client=client,
        )

    @staticmethod
    def _character():
        return {
            "name": "견본 캐릭터",
            "race": {},
            "background": {},
            "classes": [],
            "equipment": [
                {
                    "name": "Unlisted Tool Name",
                    "original_name": "Unlisted Tool Name",
                    "description": "",
                }
            ],
            "spells": [],
            "features": [],
            "actions": [],
            "resources": [],
            "proficiencies": [],
            "languages": [],
        }

    def test_character_translation_survives_repeated_empty_single_item(self):
        translator = self._translator(AlwaysEmptyClient())

        result = translator.translate_character(self._character())

        self.assertEqual(
            result["equipment"][0]["name"],
            "Unlisted Tool Name",
        )
        self.assertTrue(translator.warnings)

    def test_direct_translate_remains_strict_for_debugging(self):
        translator = self._translator(AlwaysEmptyClient())

        with self.assertRaises(TranslationError):
            translator.translate("Unlisted Tool Name")

    def test_network_failure_still_aborts(self):
        translator = self._translator(NetworkFailureClient())

        with self.assertRaises(TranslationError):
            translator.translate_character(self._character())


if __name__ == "__main__":
    unittest.main()
