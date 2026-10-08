# -*- coding: utf-8 -*-
import unittest

from sheet_mover.calculator import apply_stage2_calculations
from sheet_mover.models import CharacterSheet
from sheet_mover.runtime_integrity_v262 import (
    _refresh_translated_numbers,
    sanitize_raw_ability_choices,
    selected_feat_replacement_components,
)


def _stat_rows(values):
    return [
        {"id": index, "value": values.get(index)}
        for index in range(1, 7)
    ]


class RuntimeIntegrityV262Tests(unittest.TestCase):
    def _raw(self):
        return {
            "feats": [
                {"definition": {"id": 39, "name": "Resilient"}}
            ],
            "choiceDefinitions": [
                {
                    "id": "12168134-1",
                    "options": [
                        {"id": 695, "label": "Ability Score Improvement"},
                        {"id": 696, "label": "Feat"},
                    ],
                },
                {
                    "id": "12168134-2",
                    "options": [
                        {"id": 3525, "label": "Charisma Score"},
                    ],
                },
                {"id": "12168134-6", "options": []},
            ],
            "choices": {
                "class": [
                    {
                        "componentId": 268,
                        "componentTypeId": 12168134,
                        "id": "1-268",
                        "parentChoiceId": None,
                        "type": 1,
                        "optionValue": 696,
                    },
                    {
                        "componentId": 268,
                        "componentTypeId": 12168134,
                        "id": "6-268",
                        "parentChoiceId": "1-268",
                        "type": 6,
                        "optionValue": 39,
                        "label": "Choose a Feat",
                    },
                    {
                        "componentId": 275,
                        "componentTypeId": 12168134,
                        "id": "1-275",
                        "parentChoiceId": None,
                        "type": 1,
                        "optionValue": 695,
                    },
                    {
                        "componentId": 275,
                        "componentTypeId": 12168134,
                        "id": "2-275",
                        "parentChoiceId": "1-275",
                        "type": 2,
                        "optionValue": 3525,
                    },
                    {
                        "componentId": 276,
                        "componentTypeId": 12168134,
                        "id": "1-276",
                        "parentChoiceId": None,
                        "type": 1,
                        "optionValue": 695,
                    },
                    {
                        "componentId": 276,
                        "componentTypeId": 12168134,
                        "id": "2-276",
                        "parentChoiceId": "1-276",
                        "type": 2,
                        "optionValue": 3525,
                    },
                ]
            },
            "modifiers": {
                "class": [
                    {
                        "id": "1729",
                        "type": "bonus",
                        "subType": "choose-an-ability-score",
                        "value": 1,
                        "fixedValue": 1,
                        "componentId": 268,
                        "componentTypeId": 12168134,
                        "statId": None,
                        "entityId": None,
                        "isGranted": True,
                    },
                    {
                        "id": "1821",
                        "type": "bonus",
                        "subType": "choose-an-ability-score",
                        "value": 1,
                        "fixedValue": 1,
                        "componentId": 268,
                        "componentTypeId": 12168134,
                        "statId": None,
                        "entityId": None,
                        "isGranted": True,
                    },
                    {
                        "id": "same-id-wrong-type",
                        "type": "bonus",
                        "subType": "choose-an-ability-score",
                        "value": 1,
                        "componentId": 268,
                        "componentTypeId": 999999,
                        "statId": None,
                        "entityId": None,
                        "isGranted": True,
                    },
                    {
                        "id": "275-cha-a",
                        "type": "bonus",
                        "subType": "charisma-score",
                        "value": 1,
                        "entityId": 6,
                        "componentId": 275,
                        "componentTypeId": 12168134,
                        "isGranted": False,
                        "restriction": "",
                    },
                    {
                        "id": "275-cha-b",
                        "type": "bonus",
                        "subType": "charisma-score",
                        "value": 1,
                        "entityId": 6,
                        "componentId": 275,
                        "componentTypeId": 12168134,
                        "isGranted": False,
                        "restriction": "",
                    },
                    {
                        "id": "276-cha-a",
                        "type": "bonus",
                        "subType": "charisma-score",
                        "value": 1,
                        "entityId": 6,
                        "componentId": 276,
                        "componentTypeId": 12168134,
                        "isGranted": False,
                        "restriction": "",
                    },
                    {
                        "id": "276-cha-b",
                        "type": "bonus",
                        "subType": "charisma-score",
                        "value": 1,
                        "entityId": 6,
                        "componentId": 276,
                        "componentTypeId": 12168134,
                        "isGranted": False,
                        "restriction": "",
                    },
                ],
                "race": [
                    {
                        "id": "same-component-other-group",
                        "type": "bonus",
                        "subType": "choose-an-ability-score",
                        "value": 1,
                        "componentId": 268,
                        "componentTypeId": 12168134,
                        "statId": None,
                        "entityId": None,
                        "isGranted": True,
                    }
                ],
                "feat": [
                    {
                        "id": "resilient-con",
                        "type": "bonus",
                        "subType": "constitution-score",
                        "value": 1,
                        "entityId": 3,
                        "componentId": 39,
                        "isGranted": True,
                        "restriction": "",
                    }
                ],
                "item": [
                    {
                        "id": "belt-top-level-copy",
                        "type": "set",
                        "subType": "strength-score",
                        "value": 25,
                        "fixedValue": 25,
                        "componentId": 4828,
                        "componentTypeId": 112130694,
                        "isGranted": True,
                    }
                ],
            },
        }

    def test_feat_branch_uses_group_type_and_component_identity(self):
        components, selections = selected_feat_replacement_components(self._raw())
        self.assertEqual(components, {("class", "12168134", "268")})
        self.assertEqual(selections[0]["feat_id"], "39")
        self.assertEqual(selections[0]["branch_label"], "Feat")

    def test_only_proven_stale_generic_asi_modifiers_are_removed(self):
        cleaned, report = sanitize_raw_ability_choices(self._raw())
        class_ids = [row["id"] for row in cleaned["modifiers"]["class"]]
        race_ids = [row["id"] for row in cleaned["modifiers"]["race"]]

        self.assertEqual(report["removed_count"], 2)
        self.assertNotIn("1729", class_ids)
        self.assertNotIn("1821", class_ids)
        self.assertIn("same-id-wrong-type", class_ids)
        self.assertIn("same-component-other-group", race_ids)

        for wanted in ("275-cha-a", "275-cha-b", "276-cha-a", "276-cha-b"):
            self.assertIn(wanted, class_ids)

        self.assertIn(
            "resilient-con",
            [row["id"] for row in cleaned["modifiers"]["feat"]],
        )
        self.assertIn(
            "belt-top-level-copy",
            [row["id"] for row in cleaned["modifiers"]["item"]],
        )

    def test_missing_choice_definition_keeps_fail_closed_source_data(self):
        raw = self._raw()
        raw["choiceDefinitions"] = []
        cleaned, report = sanitize_raw_ability_choices(raw)
        class_ids = [row["id"] for row in cleaned["modifiers"]["class"]]
        self.assertEqual(report["removed_count"], 0)
        self.assertIn("1729", class_ids)
        self.assertIn("1821", class_ids)

    def test_feat_parent_without_concrete_feat_child_is_not_enough(self):
        raw = self._raw()
        raw["choices"]["class"] = [
            row for row in raw["choices"]["class"] if row["id"] != "6-268"
        ]
        cleaned, report = sanitize_raw_ability_choices(raw)
        class_ids = [row["id"] for row in cleaned["modifiers"]["class"]]
        self.assertEqual(report["removed_count"], 0)
        self.assertIn("1729", class_ids)
        self.assertIn("1821", class_ids)

    def test_actual_shape_recalculates_expected_ability_scores(self):
        raw = self._raw()
        raw["modifiers"]["class"] = [
            row
            for row in raw["modifiers"]["class"]
            if row["id"] != "same-id-wrong-type"
        ]
        raw["modifiers"]["race"] = []

        cleaned, report = sanitize_raw_ability_choices(raw)
        self.assertEqual(report["removed_count"], 2)

        equipment = [
            {
                "source_id": "782361561",
                "definition_id": "4782",
                "kind": "equipment",
                "name": "Tome of Leadership and Influence",
                "original_name": "Tome of Leadership and Influence",
                "quantity": 1,
                "equipped": True,
                "attuned": False,
                "can_equip": True,
                "can_attune": False,
                "is_consumable": False,
                "granted_modifiers": [
                    {
                        "id": "545",
                        "type": "bonus",
                        "subType": "charisma-score",
                        "value": 2,
                        "fixedValue": 2,
                        "entityId": 6,
                        "componentId": 4782,
                        "componentTypeId": 112130694,
                        "restriction": "",
                        "isGranted": True,
                    },
                    {
                        "id": "7197",
                        "type": "bonus",
                        "subType": "ability-score-maximum",
                        "value": 10,
                        "fixedValue": 10,
                        "statId": 6,
                        "componentId": 4782,
                        "componentTypeId": 112130694,
                        "restriction": "",
                        "isGranted": True,
                    },
                ],
            },
            {
                "source_id": "912291191",
                "definition_id": "4828",
                "kind": "equipment",
                "name": "Belt of Fire Giant Strength",
                "original_name": "Belt of Fire Giant Strength",
                "quantity": 1,
                "equipped": True,
                "attuned": True,
                "can_equip": True,
                "can_attune": True,
                "is_consumable": False,
                "granted_modifiers": [
                    {
                        "id": "409",
                        "type": "set",
                        "subType": "strength-score",
                        "value": 25,
                        "fixedValue": 25,
                        "entityId": 1,
                        "componentId": 4828,
                        "componentTypeId": 112130694,
                        "restriction": "",
                        "isGranted": True,
                    }
                ],
            },
        ]

        sheet = CharacterSheet(
            classes=[
                {
                    "source_id": "paladin-class",
                    "definition_id": "paladin-class",
                    "name": "Paladin",
                    "level": 12,
                }
            ],
            features=[
                {
                    "source_id": "268",
                    "definition_id": "268",
                    "kind": "class_feature",
                    "name": "Ability Score Improvement",
                },
                {
                    "source_id": "275",
                    "definition_id": "275",
                    "kind": "class_feature",
                    "name": "Ability Score Improvement",
                },
                {
                    "source_id": "276",
                    "definition_id": "276",
                    "kind": "class_feature",
                    "name": "Ability Score Improvement",
                },
                {
                    "source_id": "39",
                    "definition_id": "39",
                    "kind": "feat",
                    "name": "Resilient",
                },
            ],
            equipment=equipment,
            spellcasting={},
            calculation_inputs={
                "stats": _stat_rows({1: 14, 2: 8, 3: 15, 4: 8, 5: 10, 6: 15}),
                "bonus_stats": _stat_rows({}),
                "override_stats": _stat_rows({}),
                "modifiers": cleaned["modifiers"],
                "character_values": [],
                "base_hit_points": 100,
                "bonus_hit_points": None,
                "override_hit_points": None,
                "removed_hit_points": 0,
                "temporary_hit_points": 0,
                "class_levels": [{"name": "Paladin", "level": 12, "hit_die": 10}],
                "class_feature_levels": [],
                "race_movement": {"normal": {"walk": 30}},
            },
        )

        apply_stage2_calculations(sheet)

        self.assertEqual(
            sheet.ability_scores,
            {
                "strength": 25,
                "dexterity": 8,
                "constitution": 16,
                "intelligence": 8,
                "wisdom": 10,
                "charisma": 21,
            },
        )

    def test_translation_text_is_preserved_when_numbers_refresh(self):
        translated = {
            "name": "번역된 캐릭터 이름",
            "features": [{"name": "번역된 특성", "description": "번역문 유지"}],
            "ability_scores": {"strength": None},
        }
        fresh_original = {
            "ability_scores": {"strength": 25, "dexterity": 8},
            "total_level": 12,
        }

        refreshed = _refresh_translated_numbers(translated, fresh_original)

        self.assertEqual(refreshed["features"][0]["description"], "번역문 유지")
        self.assertEqual(refreshed["ability_scores"]["strength"], 25)
        self.assertEqual(refreshed["total_level"], 12)


if __name__ == "__main__":
    unittest.main()
