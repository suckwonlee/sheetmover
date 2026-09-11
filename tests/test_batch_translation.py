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


class BatchClient:
    def __init__(self):
        self.batch_calls = 0
        self.single_calls = 0

    def chat(self, **kwargs):
        payload = json.loads(kwargs["messages"][-1]["content"])

        if "items" in payload:
            self.batch_calls += 1
            rows = [
                {
                    "id": item["id"],
                    "translation": item["source"],
                }
                for item in payload["items"]
            ]
            return _Response(
                json.dumps(
                    {"translations": rows},
                    ensure_ascii=False,
                )
            )

        self.single_calls += 1
        return _Response(
            json.dumps(
                {"translation": payload["source"]},
                ensure_ascii=False,
            )
        )


class BatchFailureClient(BatchClient):
    def chat(self, **kwargs):
        payload = json.loads(kwargs["messages"][-1]["content"])
        if "items" in payload and len(payload["items"]) > 1:
            self.batch_calls += 1
            raise RuntimeError("simulated batch failure")
        return super().chat(**kwargs)


class BatchTranslationTests(unittest.TestCase):
    def _translator(self, client):
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        glossary = Path(td.name) / "glossary.json"
        glossary.write_text("{}", encoding="utf-8")
        return Translator(
            glossary_path=glossary,
            client=client,
        )

    @staticmethod
    def _character(names):
        return {
            "name": "견본 캐릭터",
            "race": {},
            "background": {},
            "classes": [],
            "equipment": [
                {
                    "name": name,
                    "description": "",
                    "original_name": name,
                }
                for name in names
            ],
            "spells": [],
            "features": [],
            "actions": [],
            "resources": [],
            "proficiencies": [],
            "languages": [],
        }

    def test_many_short_items_are_batched(self):
        client = BatchClient()
        translator = self._translator(client)

        result = translator.translate_character(
            self._character(
                [f"Unique Item {index} Alpha" for index in range(25)]
            )
        )

        self.assertEqual(len(result["equipment"]), 25)
        self.assertLessEqual(client.batch_calls, 3)
        self.assertEqual(client.single_calls, 0)

    def test_duplicate_sources_are_translated_once(self):
        client = BatchClient()
        translator = self._translator(client)

        result = translator.translate_character(
            self._character(["Repeated Alpha"] * 20)
        )

        self.assertEqual(len(result["equipment"]), 20)
        # One unique source falls through the safe single-item path once.
        self.assertEqual(client.single_calls, 1)

    def test_failed_batch_splits_and_finishes(self):
        client = BatchFailureClient()
        translator = self._translator(client)

        result = translator.translate_character(
            self._character(
                [f"Split Item {index} Alpha" for index in range(6)]
            )
        )

        self.assertEqual(len(result["equipment"]), 6)
        self.assertGreater(client.batch_calls, 0)
        self.assertEqual(client.single_calls, 6)


if __name__ == "__main__":
    unittest.main()
