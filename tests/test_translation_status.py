import contextlib
import io
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from sheet_mover import __main__ as cli
from sheet_mover.mover import PreparationResult, SheetMover, translation_summary


FALLBACK_WARNING = (
    "번역 서비스가 해당 설명 조각을 안정적으로 반환하지 못해 "
    "원문으로 유지했습니다 (검증 실패): Thunderwave source preview"
)


class _Original:
    name = "견본 캐릭터"
    warnings = ["원본 경고"]

    def to_dict(self):
        return {
            "name": self.name,
            "warnings": list(self.warnings),
        }


class _Translator:
    def translate_character(self, original, on_progress=None):
        if on_progress:
            on_progress(1, 1)
        result = dict(original)
        result["warnings"] = [*original.get("warnings", []), FALLBACK_WARNING]
        result["translation_summary"] = {
            "status": "partial",
            "original_preserved_count": 1,
            "original_preserved": [
                {
                    "reason": "검증 실패",
                    "source_preview": "Thunderwave source preview",
                }
            ],
        }
        return result

    def translation_summary(self):
        return {
            "status": "partial",
            "original_preserved_count": 1,
            "original_preserved": [],
        }


class TranslationStatusTests(unittest.IsolatedAsyncioTestCase):
    def test_summary_can_derive_legacy_fallback_warning(self):
        summary = translation_summary({"warnings": [FALLBACK_WARNING]})
        self.assertEqual(summary["status"], "partial")
        self.assertEqual(summary["original_preserved_count"], 1)
        self.assertEqual(
            summary["original_preserved"][0]["source_preview"],
            "Thunderwave source preview",
        )

    async def test_prepare_merges_translation_warning_and_reports_partial(self):
        progress = []
        with mock.patch("sheet_mover.mover.fetch_character", return_value={}), mock.patch(
            "sheet_mover.mover.normalize_character", return_value=_Original()
        ), mock.patch("sheet_mover.mover.Translator", return_value=_Translator()):
            result = await SheetMover(on_progress=lambda p, m: progress.append((p, m))).prepare()

        self.assertIn(FALLBACK_WARNING, result.warnings)
        payload = result.to_dict()
        self.assertEqual(payload["translation_summary"]["status"], "partial")
        self.assertEqual(payload["translation_summary"]["original_preserved_count"], 1)
        self.assertIn("부분 완료", progress[-1][1])
        self.assertIn("원문 유지 1개", progress[-1][1])


class CliTranslationStatusTests(unittest.TestCase):
    def test_cli_prints_partial_completion_and_item_summary(self):
        result = PreparationResult(
            source_url="https://www.dndbeyond.com/characters/170892133",
            original={"source_id": "170892133", "name": "견본 캐릭터"},
            translated={
                "name": "견본 캐릭터",
                "translation_summary": {
                    "status": "partial",
                    "original_preserved_count": 1,
                    "original_preserved": [
                        {
                            "reason": "검증 실패",
                            "source_preview": "Thunderwave source preview",
                        }
                    ],
                },
            },
            warnings=[FALLBACK_WARNING],
            roll20_tabs=[],
            raw_source={},
        )

        with TemporaryDirectory() as td:
            output = Path(td) / "result.json"
            stderr = io.StringIO()
            stdout = io.StringIO()
            argv = ["sheet_mover", "--cli", "--output", str(output)]
            with mock.patch.object(sys, "argv", argv), mock.patch(
                "sheet_mover.__main__.run", return_value=result
            ), contextlib.redirect_stderr(stderr), contextlib.redirect_stdout(stdout):
                cli.main()

        text = stderr.getvalue()
        self.assertIn("번역 부분 완료", text)
        self.assertIn("원문 유지 1개", text)
        self.assertIn("Thunderwave source preview", text)


if __name__ == "__main__":
    unittest.main()
