"""Offline regressions; no browser, Ollama server or Tk windows are started."""
import json
import unittest

from sheet_mover.mover import is_roll20_game
from sheet_mover.source import normalize_character, source_character_id
from sheet_mover.translator import Translator, TranslationError


class FakeClient:
    def __init__(self, output=None, error=None):
        self.output = output
        self.error = error

    def chat(self, **kwargs):
        if self.error:
            raise self.error
        return {"message": {"content": json.dumps({"translation": self.output})}}


class PreparationTests(unittest.TestCase):
    def test_url_validation(self):
        self.assertEqual(source_character_id("https://www.dndbeyond.com/characters/123"), "123")
        for url in ("https://evil.test/characters/123", "https://www.dndbeyond.com/profile", "http://www.dndbeyond.com/characters/123"):
            with self.assertRaises(ValueError):
                source_character_id(url)
        self.assertTrue(is_roll20_game("https://app.roll20.net/editor/"))
        self.assertFalse(is_roll20_game("https://evil.test/?roll20.net/editor"))
        self.assertFalse(is_roll20_game("https://app.roll20.net/campaigns/details/123"))

    def test_unresolved_stats_are_not_base_scores(self):
        sheet = normalize_character({"name": "Hero", "stats": [{"id": 1, "value": 12}],
                                     "modifiers": {"race": [{"type": "bonus", "subType": "strength-score", "value": 2}]}})
        self.assertIsNone(sheet.ability_scores["strength"])
        self.assertIsNone(sheet.hp)
        self.assertTrue(sheet.warnings)

    def test_zero_hp_is_not_missing(self):
        sheet = normalize_character({"name": "Hero", "overrideHitPoints": 20, "removedHitPoints": 20})
        self.assertEqual(sheet.hp, 0)
        self.assertEqual(sheet.max_hp, 20)

    def test_inventory_identity_and_quantity_preserved(self):
        sheet = normalize_character({"name": "Hero", "inventory": [{"id": 101, "quantity": 0,
                                     "definition": {"id": 44, "name": "Shield", "description": ""}}]})
        source = sheet.to_dict()
        translated = Translator(client=FakeClient()).translate_character(source)
        self.assertEqual(translated["name"], "Hero")
        self.assertEqual(translated["equipment"][0]["name"], "방패")
        self.assertEqual(translated["equipment"][0]["source_id"], "101")
        self.assertEqual(translated["equipment"][0]["original_name"], "Shield")
        self.assertEqual(translated["equipment"][0]["quantity"], 0)
        self.assertEqual(source["equipment"][0]["name"], "Shield")

    def test_network_error_is_not_silent_original_fallback(self):
        with self.assertRaises(TranslationError):
            Translator(client=FakeClient(error=TimeoutError("offline"))).translate("A custom feature")

    def test_changed_dice_or_sign_rejected(self):
        for original, translated in (("Deal 1d6 damage", "2d6 피해"), ("Gain +3 bonus", "-3 보너스")):
            with self.assertRaises(TranslationError):
                Translator(client=FakeClient(translated)).translate(original)

    def test_html_and_dice_preserved(self):
        result = Translator(client=FakeClient("<p>1d6 피해</p>")).translate("<p>Deal 1d6 damage</p>")
        self.assertEqual(result, "<p>1d6 피해</p>")


if __name__ == "__main__":
    unittest.main()
