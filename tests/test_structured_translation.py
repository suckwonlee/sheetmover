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


class StructureAwareClient:
    def __init__(self, empty_for=None):
        self.empty_for = empty_for
        self.seen_sources = []

    @staticmethod
    def _translate_text(text):
        mapping = {
            "At the beginning of the ": "시작할 때 ",
            "Northlands Sagas": "북방 사가",
            " adventure path, each player should draw one Runestone randomly.":
                " 모험에서 각 플레이어는 무작위로 룬스톤 하나를 뽑습니다.",
            "You take the ": "당신은 ",
            "Attack": "공격",
            " action.": " 행동을 합니다.",
            "You have learned to cast spells.": "주문 시전을 배웠습니다.",
            "Fighter Level": "파이터 레벨",
            "Spells Prepared": "준비된 주문",
        }
        return mapping.get(text, text)

    def chat(self, **kwargs):
        payload = json.loads(kwargs["messages"][-1]["content"])

        if "items" in payload:
            rows = []
            for item in payload["items"]:
                source = item["source"]
                self.seen_sources.append(source)
                value = (
                    ""
                    if self.empty_for and self.empty_for in source
                    else self._translate_text(source)
                )
                rows.append(
                    {"id": item["id"], "translation": value}
                )
            return _Response(
                json.dumps(
                    {"translations": rows},
                    ensure_ascii=False,
                )
            )

        source = payload["source"]
        self.seen_sources.append(source)
        value = (
            ""
            if self.empty_for and self.empty_for in source
            else self._translate_text(source)
        )
        return _Response(
            json.dumps({"translation": value}, ensure_ascii=False)
        )


class StructuredTranslationTests(unittest.TestCase):
    def _translator(self, client):
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        glossary = Path(td.name) / "glossary.json"
        glossary.write_text("{}", encoding="utf-8")
        return Translator(glossary_path=glossary, client=client)

    def test_html_text_slots_cannot_move_across_tags(self):
        client = StructureAwareClient()
        translator = self._translator(client)
        source = (
            "<p>At the beginning of the "
            "<em>Northlands Sagas</em>"
            " adventure path, each player should draw one Runestone randomly."
            "</p>"
        )
        translated = translator.translate(source)

        self.assertEqual(
            translated,
            "<p>시작할 때 <em>북방 사가</em>"
            " 모험에서 각 플레이어는 무작위로 룬스톤 하나를 뽑습니다.</p>",
        )
        self.assertFalse(
            any("<p>" in value or "<em>" in value for value in client.seen_sources)
        )

    def test_ddb_inline_tags_are_preserved(self):
        client = StructureAwareClient()
        translator = self._translator(client)

        translated = translator.translate(
            "You take the [action]Attack[/action] action."
        )

        self.assertEqual(
            translated,
            "당신은 [action]공격[/action] 행동을 합니다.",
        )

    def test_long_html_table_never_goes_to_model_as_one_blob(self):
        client = StructureAwareClient()
        translator = self._translator(client)

        rows = "".join(
            f"<tr><td>{i}</td><td>Fighter Level</td>"
            f"<td>Spells Prepared</td></tr>"
            for i in range(1, 80)
        )
        source = (
            "<p>You have learned to cast spells.</p>"
            "<table><tbody>"
            + rows
            + "</tbody></table>"
        )
        translated = translator.translate(source)

        self.assertIn("주문 시전을 배웠습니다.", translated)
        self.assertIn("전사 레벨", translated)
        self.assertIn("준비된 주문", translated)
        self.assertEqual(translated.count("<tr>"), 79)
        self.assertFalse(
            any("<table>" in value or "<tr>" in value for value in client.seen_sources)
        )

    def test_empty_structured_fragment_keeps_original_and_continues(self):
        client = StructureAwareClient(empty_for="Spells Prepared")
        translator = self._translator(client)
        source = (
            "<table><tr><td>Fighter Level</td>"
            "<td>Spells Prepared</td></tr></table>"
        )
        translated = translator.translate(source)

        self.assertIn("전사 레벨", translated)
        self.assertIn("Spells Prepared", translated)
        self.assertTrue(translator.warnings)


if __name__ == "__main__":
    unittest.main()
