"""Offline regressions; no browser, Ollama server or Tk windows are started."""
import json
import re
import unittest

from sheet_mover.models import CharacterSheet
from sheet_mover.mover import is_roll20_game
from sheet_mover.source import fetch_character, normalize_character, source_character_id
from sheet_mover.translator import Translator, TranslationError


class FakeClient:
    def __init__(self, output=None, error=None):
        self.output = output
        self.error = error

    def chat(self, **kwargs):
        if self.error:
            raise self.error

        # New translator versions protect HTML/numbers/dice with placeholders
        # before calling Ollama.  Old regression tests may still provide a
        # human-readable expected translation, so adapt that expected output to
        # the placeholders that were actually sent to the fake model.
        source_payload = json.loads(
            kwargs["messages"][-1]["content"]
        ).get("source", "")

        output = self.output
        if isinstance(output, str) and "__SHEETMOVER_PROTECTED_" in source_payload:
            source_tokens = re.findall(
                r"__SHEETMOVER_PROTECTED_\d{4}__",
                source_payload,
            )
            output_tokens = re.findall(
                r"__SHEETMOVER_PROTECTED_\d{4}__",
                output,
            )

            if not output_tokens:
                # Preserve the source placeholders in their original order and
                # use the provided output only as translated plain text.
                plain = re.sub(r"<[^>]*>", "", output)
                plain = re.sub(
                    r"[+-]?\d+(?:\.\d+)?(?:d\d+(?:\s*[+-]\s*\d+)?)?",
                    "",
                    plain,
                    flags=re.I,
                ).strip()

                if source_tokens:
                    if len(source_tokens) == 1:
                        output = source_tokens[0] + plain
                    elif len(source_tokens) >= 2:
                        output = (
                            source_tokens[0]
                            + plain
                            + "".join(source_tokens[1:])
                        )

        return {
            "message": {
                "content": json.dumps(
                    {"translation": output},
                    ensure_ascii=False,
                )
            }
        }


class PreparationTests(unittest.TestCase):
    def test_url_validation(self):
        self.assertEqual(
            source_character_id(
                "https://www.dndbeyond.com/characters/123"
            ),
            "123",
        )
        for url in (
            "https://evil.test/characters/123",
            "https://www.dndbeyond.com/profile",
            "http://www.dndbeyond.com/characters/123",
        ):
            with self.assertRaises(ValueError):
                source_character_id(url)
        self.assertTrue(
            is_roll20_game("https://app.roll20.net/editor/")
        )
        self.assertFalse(
            is_roll20_game("https://evil.test/?roll20.net/editor")
        )
        self.assertFalse(
            is_roll20_game(
                "https://app.roll20.net/campaigns/details/123"
            )
        )

    def test_unresolved_stats_are_not_base_scores(self):
        sheet = normalize_character(
            {
                "name": "Hero",
                "stats": [{"id": 1, "value": 12}],
                "modifiers": {
                    "race": [
                        {
                            "type": "bonus",
                            "subType": "strength-score",
                            "value": 2,
                        }
                    ]
                },
            }
        )
        self.assertIsNone(sheet.ability_scores["strength"])
        self.assertIsNone(sheet.hp)
        self.assertTrue(sheet.warnings)
        self.assertEqual(
            sheet.calculation_inputs["stats"],
            [{"id": 1, "value": 12}],
        )

    def test_zero_hp_is_not_missing(self):
        sheet = normalize_character(
            {
                "name": "Hero",
                "overrideHitPoints": 20,
                "removedHitPoints": 20,
            }
        )
        self.assertEqual(sheet.hp, 0)
        self.assertEqual(sheet.max_hp, 20)

    def test_inventory_identity_and_quantity_preserved(self):
        sheet = normalize_character(
            {
                "name": "Hero",
                "inventory": [
                    {
                        "id": 101,
                        "quantity": 0,
                        "definition": {
                            "id": 44,
                            "name": "Shield",
                            "description": "",
                        },
                    }
                ],
            }
        )
        source = sheet.to_dict()
        translated = Translator(
            client=FakeClient()
        ).translate_character(source)
        self.assertEqual(translated["name"], "Hero")
        self.assertEqual(
            translated["equipment"][0]["name"],
            "방패 (Shield)",
        )
        self.assertEqual(
            translated["equipment"][0]["source_id"],
            "101",
        )
        self.assertEqual(
            translated["equipment"][0]["original_name"],
            "Shield",
        )
        self.assertEqual(
            translated["equipment"][0]["quantity"],
            0,
        )
        self.assertEqual(
            source["equipment"][0]["name"],
            "Shield",
        )

    def test_extended_model_preserves_source_identity_and_biography(self):
        sheet = normalize_character(
            {
                "id": 999,
                "name": "견본",
                "currentXp": 6500,
                "temporaryHitPoints": 7,
                "currencies": {"gp": 123, "sp": 4},
                "race": {
                    "id": 1,
                    "fullName": "High Elf",
                    "baseRaceName": "Elf",
                    "subRaceShortName": "High Elf",
                    "weightSpeeds": {
                        "normal": {"walk": 30}
                    },
                },
                "background": {
                    "definition": {
                        "id": 2,
                        "name": "Sage",
                        "featureName": "Researcher",
                        "featureDescription": "Find lore.",
                    }
                },
                "classes": [
                    {
                        "id": 10,
                        "level": 5,
                        "definition": {
                            "id": 20,
                            "name": "Wizard",
                            "hitDice": 6,
                            "spellCastingAbilityId": 4,
                        },
                        "subclassDefinition": {
                            "id": 30,
                            "name": "School of Evocation",
                        },
                    }
                ],
            }
        )
        self.assertEqual(sheet.source_id, "999")
        self.assertEqual(sheet.name, "견본")
        self.assertEqual(sheet.race["name"], "High Elf")
        self.assertEqual(sheet.background["name"], "Sage")
        self.assertEqual(sheet.classes[0]["name"], "Wizard")
        self.assertEqual(sheet.classes[0]["level"], 5)
        self.assertIsNone(sheet.total_level)
        self.assertEqual(sheet.temp_hp, 7)
        self.assertEqual(sheet.currencies["gp"], 123)
        self.assertEqual(
            sheet.calculation_inputs["class_levels"][0]["level"],
            5,
        )

    def test_character_name_is_never_translated(self):
        source = CharacterSheet(
            name="견본",
            race={"name": "Elf"},
        ).to_dict()
        translated = Translator(
            client=FakeClient("엘프")
        ).translate_character(source)
        self.assertEqual(translated["name"], "견본")
        self.assertEqual(translated["race"]["name"], "엘프")

    def test_old_saved_json_still_loads(self):
        old = CharacterSheet.from_json(
            {
                "name": "Old Hero",
                "ability_scores": {"strength": 10},
                "hp": 3,
                "max_hp": 9,
                "unknown_future_field": "ignored",
            }
        )
        self.assertEqual(old.name, "Old Hero")
        self.assertEqual(old.hp, 3)
        self.assertEqual(old.resources, [])

    def test_network_error_is_not_silent_original_fallback(self):
        with self.assertRaises(TranslationError):
            Translator(
                client=FakeClient(error=TimeoutError("offline"))
            ).translate("A custom feature")

    def test_dice_and_signed_numbers_are_hidden_from_model_and_restored(self):
        class InspectingClient:
            def __init__(self):
                self.sources = []

            def chat(self, **kwargs):
                source = json.loads(
                    kwargs["messages"][-1]["content"]
                )["source"]
                self.sources.append(source)

                # The model only translates plain text. Mechanical values must
                # already be replaced by protected placeholders.
                translated = (
                    source.replace("Deal ", "")
                    .replace(" damage", " 피해")
                    .replace("Gain ", "")
                    .replace(" bonus", " 보너스")
                )

                return {
                    "message": {
                        "content": json.dumps(
                            {"translation": translated},
                            ensure_ascii=False,
                        )
                    }
                }

        client = InspectingClient()
        translator = Translator(client=client)

        first = translator.translate("Deal 1d6 damage")
        second = translator.translate("Gain +3 bonus")

        self.assertEqual(first, "1d6 피해")
        self.assertEqual(second, "+3 보너스")

        self.assertNotIn("1d6", client.sources[0])
        self.assertNotIn("+3", client.sources[1])
        self.assertIn("__SHEETMOVER_PROTECTED_", client.sources[0])
        self.assertIn("__SHEETMOVER_PROTECTED_", client.sources[1])

    def test_html_and_dice_preserved(self):
        class PlaceholderAwareClient:
            def chat(self, **kwargs):
                source = json.loads(
                    kwargs["messages"][-1]["content"]
                )["source"]
                translated = source.replace(
                    "Deal ",
                    "",
                ).replace(
                    " damage",
                    " 피해",
                )
                return {
                    "message": {
                        "content": json.dumps(
                            {"translation": translated},
                            ensure_ascii=False,
                        )
                    }
                }

        result = Translator(
            client=PlaceholderAwareClient()
        ).translate("<p>Deal 1d6 damage</p>")
        self.assertEqual(result, "<p>1d6 피해</p>")

    def test_fetch_character_uses_character_service_without_browser(self):
        class FakeResponse:
            status = 200

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def read(self):
                return json.dumps(
                    {"data": {"id": 170892133, "name": "견본"}}
                ).encode("utf-8")

        captured = {}

        def opener(request, timeout):
            captured["url"] = request.full_url
            captured["timeout"] = timeout
            return FakeResponse()

        data = fetch_character(
            "https://www.dndbeyond.com/characters/170892133",
            opener=opener,
        )
        self.assertEqual(data["name"], "견본")
        self.assertIn(
            "/character/v5/character/170892133",
            captured["url"],
        )
        self.assertIn(
            "includeCustomItems=true",
            captured["url"],
        )

    def test_fetch_character_rejects_wrong_response_id(self):
        class FakeResponse:
            status = 200

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def read(self):
                return json.dumps(
                    {"data": {"id": 999, "name": "Wrong"}}
                ).encode("utf-8")

        with self.assertRaises(RuntimeError):
            fetch_character(
                "https://www.dndbeyond.com/characters/170892133",
                opener=lambda request, timeout: FakeResponse(),
            )


if __name__ == "__main__":
    unittest.main()
