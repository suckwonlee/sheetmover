import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from sheet_mover.translator import Translator


class GemmaModelSwitchTests(unittest.TestCase):
    def _glossary(self):
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        path = Path(td.name) / "glossary.json"
        path.write_text("{}", encoding="utf-8")
        return path

    def test_default_model_is_gemma3_4b(self):
        with patch.dict(os.environ, {}, clear=True):
            translator = Translator(
                glossary_path=self._glossary(),
                client=object(),
            )
            self.assertEqual(translator.model, "gemma3:4b")

    def test_environment_can_override_model(self):
        with patch.dict(
            os.environ,
            {"SHEETMOVER_MODEL": "exaone3.5:2.4b"},
            clear=False,
        ):
            translator = Translator(
                glossary_path=self._glossary(),
                client=object(),
            )
            self.assertEqual(translator.model, "exaone3.5:2.4b")

    def test_explicit_model_beats_environment(self):
        with patch.dict(
            os.environ,
            {"SHEETMOVER_MODEL": "exaone3.5:2.4b"},
            clear=False,
        ):
            translator = Translator(
                glossary_path=self._glossary(),
                model="gemma3:4b",
                client=object(),
            )
            self.assertEqual(translator.model, "gemma3:4b")


if __name__ == "__main__":
    unittest.main()
