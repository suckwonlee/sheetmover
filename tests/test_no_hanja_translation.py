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


class HanjaThenKoreanClient:
    def __init__(self):
        self.calls = 0

    def chat(self, **kwargs):
        self.calls += 1
        payload = json.loads(kwargs["messages"][-1]["content"])

        if "items" in payload:
            return _Response(
                json.dumps(
                    {
                        "translations": [
                            {
                                "id": item["id"],
                                "translation": (
                                    "如图所示"
                                    if item["id"] == 0
                                    else "정상 번역"
                                ),
                            }
                            for item in payload["items"]
                        ]
                    },
                    ensure_ascii=False,
                )
            )

        return _Response(
            json.dumps(
                {
                    "translation": (
                        "如图所示"
                        if self.calls == 1
                        else "그림에 표시된 대로"
                    )
                },
                ensure_ascii=False,
            )
        )


class AlwaysHanjaClient:
    def chat(self, **kwargs):
        payload = json.loads(kwargs["messages"][-1]["content"])
        if "items" in payload:
            return _Response(
                json.dumps(
                    {
                        "translations": [
                            {
                                "id": item["id"],
                                "translation": "你和",
                            }
                            for item in payload["items"]
                        ]
                    },
                    ensure_ascii=False,
                )
            )
        return _Response(
            json.dumps(
                {"translation": "你和"},
                ensure_ascii=False,
            )
        )


class NoHanjaTranslationTests(unittest.TestCase):
    def _translator(self, client):
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        glossary = Path(td.name) / "glossary.json"
        glossary.write_text("{}", encoding="utf-8")
        return Translator(glossary_path=glossary, client=client)

    def test_single_hanja_response_is_rejected_and_retried(self):
        translator = self._translator(HanjaThenKoreanClient())
        result = translator._safe_fragment_model_translate(
            "as shown in the table",
            strict_plain=True,
        )
        self.assertEqual(result, "그림에 표시된 대로")

    def test_repeated_hanja_fragment_falls_back_without_hanja(self):
        translator = self._translator(AlwaysHanjaClient())
        result = translator._translate_plain_segment(
            "you and all secondary casters"
        )
        self.assertEqual(result, "you and all secondary casters")
        self.assertNotRegex(
            result,
            r"[\u3400-\u4DBF\u4E00-\u9FFF\uF900-\uFAFF]",
        )
        self.assertTrue(translator.warnings)


if __name__ == "__main__":
    unittest.main()
