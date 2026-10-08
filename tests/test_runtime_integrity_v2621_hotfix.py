# -*- coding: utf-8 -*-
import unittest

from sheet_mover.calculator import apply_stage2_calculations
from sheet_mover.models import CharacterSheet
from sheet_mover.runtime_integrity_v262 import (
    RUNTIME_INTEGRITY_V262,
    sanitize_raw_ability_choices,
    selected_feat_replacement_components,
)


def _stat_rows(values):
    return [
        {"id": index, "value": values.get(index)}
        for index in range(1, 7)
    ]


def _raw():
    return {
        "feats": [
            {"definition": {"id": 39, "name": "Resilient"}}
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
                    "label": None,
                    "optionIds": [695, 696],
                },
                {
                    "componentId": 268,
                    "componentTypeId": 12168134,
                    "id": "6-268",
                    "parentChoiceId": "1-268",
                    "type": 6,
                    "optionValue": 39,
                    "label": "Choose a Feat",
                    "optionIds": [],
                },
            ],
            "choiceDefinitions": [
                {
                    "id": "12168134-1",
                    "options": [
                        {"id": 695, "label": "Ability Score Improvement"},
                        {"id": 696, "label": "Feat"},
                    ],
                },
                {"id": "12168134-6", "options": []},
            ],
            "definitionKeyNameMap": {},
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
                    "restriction": None,
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
                    "restriction": None,
                },
                {
                    "id": "wrong-type",
                    "type": "bonus",
                    "subType": "choose-an-ability-score",
                    "value": 1,
                    "fixedValue": 1,
                    "componentId": 268,
                    "componentTypeId": 999999,
                    "statId": None,
                    "entityId": None,
                    "isGranted": True,
                },
                {
                    "id": "1731",
                    "type": "bonus",
                    "subType": "charisma-score",
                    "value": 1,
                    "fixedValue": 1,
                    "entityId": 6,
                    "componentId": 275,
                    "componentTypeId": 12168134,
                    "isGranted": False,
                    "restriction": "",
                },
                {
                    "id": "1822",
                    "type": "bonus",
                    "subType": "charisma-score",
                    "value": 1,
                    "fixedValue": 1,
                    "entityId": 6,
                    "componentId": 275,
                    "componentTypeId": 12168134,
                    "isGranted": False,
                    "restriction": "",
                },
                {
                    "id": "1733",
                    "type": "bonus",
                    "subType": "charisma-score",
                    "value": 1,
                    "fixedValue": 1,
                    "entityId": 6,
                    "componentId": 276,
                    "componentTypeId": 12168134,
                    "isGranted": False,
                    "restriction": "",
                },
                {
                    "id": "1823",
                    "type": "bonus",
                    "subType": "charisma-score",
                    "value": 1,
                    "fixedValue": 1,
                    "entityId": 6,
                    "componentId": 276,
                    "componentTypeId": 12168134,
                    "isGranted": False,
                    "restriction": "",
                },
            ],
            "race": [
                {
                    "id": "wrong-group",
                    "type": "bonus",
                    "subType": "choose-an-ability-score",
                    "value": 1,
                    "fixedValue": 1,
                    "componentId": 268,
                    "componentTypeId": 12168134,
                    "statId": None,
                    "entityId": None,
                    "isGranted": True,
                }
            ],
            "feat": [
                {
                    "id": "43691615",
                    "type": "bonus",
                    "subType": "constitution-score",
                    "value": 1,
                    "fixedValue": 1,
                    "entityId": 3,
                    "componentId": 39,
                    "componentTypeId": 1088085227,
                    "isGranted": True,
                    "restriction": "",
                }
            ],
        },
    }


def _make_sheet(*, belt_equipped):
    raw = _raw()

    raw["modifiers"]["class"] = [
        row
        for row in raw["modifiers"]["class"]
        if row["id"] != "wrong-type"
    ]
    raw["modifiers"]["race"] = []

    cleaned, report = sanitize_raw_ability_choices(raw)
    if report["removed_count"] != 2:
        raise AssertionError(report)

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
            "equipped": belt_equipped,
            "attuned": belt_equipped,
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

    return CharacterSheet(
        classes=[
            {
                "source_id": "169549570",
                "definition_id": "4",
                "name": "Paladin",
                "level": 13,
                "subclass_id": "42",
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
            "stats": _stat_rows(
                {1: 14, 2: 8, 3: 15, 4: 8, 5: 10, 6: 15}
            ),
            "bonus_stats": _stat_rows({}),
            "override_stats": _stat_rows({}),
            "modifiers": cleaned["modifiers"],
            "character_values": [],
            "base_hit_points": 100,
            "bonus_hit_points": None,
            "override_hit_points": None,
            "removed_hit_points": 0,
            "temporary_hit_points": 0,
            "class_levels": [
                {"name": "Paladin", "level": 13, "hit_die": 10}
            ],
            "class_feature_levels": [],
            "race_movement": {"normal": {"walk": 30}},
        },
    )


class RuntimeIntegrityV2621Tests(unittest.TestCase):
    def test_nested_choice_definitions_resolve_actual_feat_branch(self):
        components, selections = selected_feat_replacement_components(
            _raw()
        )
        self.assertEqual(
            components,
            {("class", "12168134", "268")},
        )
        self.assertEqual(selections[0]["feat_id"], "39")
        self.assertEqual(selections[0]["branch_label"], "Feat")

    def test_only_1729_and_1821_are_removed(self):
        cleaned, report = sanitize_raw_ability_choices(_raw())

        removed_ids = {
            row["id"]
            for row in report[
                "removed_stale_generic_asi_modifiers"
            ]
        }
        self.assertEqual(removed_ids, {"1729", "1821"})

        class_ids = {
            row["id"] for row in cleaned["modifiers"]["class"]
        }
        race_ids = {
            row["id"] for row in cleaned["modifiers"]["race"]
        }
        self.assertIn("wrong-type", class_ids)
        self.assertIn("wrong-group", race_ids)

    def test_belt_equipped_gives_strength_25(self):
        sheet = _make_sheet(belt_equipped=True)
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

    def test_without_belt_strength_is_14_and_scores_do_not_null(self):
        sheet = _make_sheet(belt_equipped=False)
        apply_stage2_calculations(sheet)

        self.assertEqual(sheet.ability_scores["strength"], 14)
        self.assertEqual(sheet.ability_scores["dexterity"], 8)
        self.assertEqual(sheet.ability_scores["constitution"], 16)
        self.assertEqual(sheet.ability_scores["intelligence"], 8)
        self.assertEqual(sheet.ability_scores["wisdom"], 10)
        self.assertEqual(sheet.ability_scores["charisma"], 21)
        self.assertNotIn(None, sheet.ability_scores.values())

    def test_hotfix_version(self):
        self.assertIn("v2.6.2.1", RUNTIME_INTEGRITY_V262)


if __name__ == "__main__":
    unittest.main()
