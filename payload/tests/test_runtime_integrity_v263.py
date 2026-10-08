# -*- coding: utf-8 -*-
import unittest

from sheet_mover.calculator import apply_stage2_calculations
from sheet_mover.models import CharacterSheet
from sheet_mover.runtime_integrity_v263 import (
    normalize_selected_option_components,
    selected_option_component_map,
)


def _stats(values):
    return [{"id": i, "value": values.get(i)} for i in range(1, 7)]


class RuntimeIntegrityV263Tests(unittest.TestCase):
    def test_selected_option_components_are_group_scoped(self):
        raw = {
            "options": {
                "race": [
                    {"componentId": 6994084, "definition": {"id": 1596852}},
                    {"componentId": 6994090, "definition": {"id": 1596862}},
                ],
                "class": [
                    {"componentId": 262, "definition": {"id": 165}},
                ],
            },
            "modifiers": {
                "race": [{
                    "id": "race-con", "componentId": 1596852,
                    "type": "bonus", "subType": "constitution-score", "value": 2,
                }],
                "class": [{
                    "id": "defense", "componentId": 165,
                    "type": "bonus", "subType": "armored-armor-class", "value": 1,
                }],
                "feat": [{
                    "id": "same-number-other-group", "componentId": 165,
                    "type": "bonus", "subType": "strength-score", "value": 1,
                }],
            },
            "actions": {
                "race": [{
                    "id": "breath", "componentId": 1596862,
                    "name": "Breath Weapon (Necrotic)",
                }]
            },
        }

        mapping = selected_option_component_map(raw)
        self.assertEqual(mapping["race"]["1596852"], "6994084")
        self.assertEqual(mapping["race"]["1596862"], "6994090")
        self.assertEqual(mapping["class"]["165"], "262")

        cleaned, report = normalize_selected_option_components(raw)
        self.assertEqual(cleaned["modifiers"]["race"][0]["componentId"], "6994084")
        self.assertEqual(cleaned["modifiers"]["class"][0]["componentId"], "262")
        self.assertEqual(cleaned["actions"]["race"][0]["componentId"], "6994090")
        self.assertEqual(cleaned["modifiers"]["feat"][0]["componentId"], 165)
        self.assertEqual(report["remapped_count"], 3)

    def test_ambiguous_option_is_left_untouched(self):
        raw = {
            "options": {
                "race": [
                    {"componentId": 100, "definition": {"id": 500}},
                    {"componentId": 101, "definition": {"id": 500}},
                ]
            },
            "modifiers": {
                "race": [{
                    "id": "x", "componentId": 500,
                    "type": "bonus", "subType": "constitution-score", "value": 2,
                }]
            },
        }
        cleaned, report = normalize_selected_option_components(raw)
        self.assertEqual(cleaned["modifiers"]["race"][0]["componentId"], 500)
        self.assertEqual(report["remapped_count"], 0)

    def test_expected_actual_character_numbers(self):
        sheet = CharacterSheet(
            classes=[{
                "source_id": "169549570", "definition_id": "4",
                "name": "Paladin", "level": 13,
            }],
            features=[
                {"kind": "racial_trait", "source_id": "6994084",
                 "definition_id": "6994084", "name": "Ability Score Increases"},
                {"kind": "class_feature", "source_id": "262",
                 "definition_id": "262", "name": "Fighting Style"},
                {"kind": "class_feature", "source_id": "275",
                 "definition_id": "275", "name": "Ability Score Improvement"},
                {"kind": "class_feature", "source_id": "276",
                 "definition_id": "276", "name": "Ability Score Improvement"},
                {"kind": "feat", "source_id": "39",
                 "definition_id": "39", "name": "Resilient"},
            ],
            equipment=[
                {
                    "source_id": "tome", "definition_id": "4782",
                    "item_type": "Wondrous Item", "equipped": True,
                    "attuned": False, "can_equip": True, "can_attune": False,
                    "is_consumable": False,
                    "granted_modifiers": [
                        {"type": "bonus", "subType": "charisma-score",
                         "value": 2, "componentId": 4782, "restriction": ""},
                        {"type": "bonus", "subType": "ability-score-maximum",
                         "value": 10, "statId": 6, "componentId": 4782,
                         "restriction": ""},
                    ],
                },
                {
                    "source_id": "belt", "definition_id": "4828",
                    "item_type": "Wondrous Item", "equipped": True,
                    "attuned": True, "can_equip": True, "can_attune": True,
                    "is_consumable": False,
                    "granted_modifiers": [
                        {"type": "set", "subType": "strength-score",
                         "value": 25, "componentId": 4828,
                         "restriction": "If not already higher"},
                    ],
                },
                {
                    "source_id": "plate", "definition_id": "plate",
                    "item_type": "Armor", "equipped": True,
                    "attuned": False, "can_equip": True, "can_attune": False,
                    "is_consumable": False, "armor_type_id": 3,
                    "armor_class": 18, "granted_modifiers": [],
                },
                {
                    "source_id": "shield", "definition_id": "5403",
                    "item_type": "Armor", "equipped": True,
                    "attuned": False, "can_equip": True, "can_attune": False,
                    "is_consumable": False, "armor_type_id": 4,
                    "armor_class": 2, "granted_modifiers": [],
                },
            ],
            spellcasting={},
            calculation_inputs={
                "stats": _stats({1: 14, 2: 8, 3: 15, 4: 8, 5: 10, 6: 15}),
                "bonus_stats": _stats({}),
                "override_stats": _stats({}),
                "modifiers": {
                    "race": [
                        {"id": "race-con", "type": "bonus",
                         "subType": "constitution-score", "value": 2,
                         "componentId": 6994084, "restriction": ""},
                        {"id": "race-cha", "type": "bonus",
                         "subType": "charisma-score", "value": 1,
                         "componentId": 6994084, "restriction": ""},
                    ],
                    "class": [
                        {"id": "defense", "type": "bonus",
                         "subType": "armored-armor-class", "value": 1,
                         "componentId": 262, "restriction": ""},
                        {"id": "a", "type": "bonus", "subType": "charisma-score",
                         "value": 1, "componentId": 275, "restriction": ""},
                        {"id": "b", "type": "bonus", "subType": "charisma-score",
                         "value": 1, "componentId": 275, "restriction": ""},
                        {"id": "c", "type": "bonus", "subType": "charisma-score",
                         "value": 1, "componentId": 276, "restriction": ""},
                        {"id": "d", "type": "bonus", "subType": "charisma-score",
                         "value": 1, "componentId": 276, "restriction": ""},
                    ],
                    "feat": [
                        {"id": "resilient-con", "type": "bonus",
                         "subType": "constitution-score", "value": 1,
                         "componentId": 39, "restriction": ""},
                    ],
                },
                "character_values": [],
                "base_hit_points": 82,
                "bonus_hit_points": None,
                "override_hit_points": None,
                "removed_hit_points": 0,
                "temporary_hit_points": 0,
                "class_levels": [{"name": "Paladin", "level": 13, "hit_die": 10}],
                "class_feature_levels": [],
                "race_movement": {"normal": {"walk": 30}},
            },
        )

        apply_stage2_calculations(sheet)

        self.assertEqual(sheet.ability_scores, {
            "strength": 25, "dexterity": 8, "constitution": 18,
            "intelligence": 8, "wisdom": 10, "charisma": 22,
        })
        self.assertEqual(sheet.max_hp, 134)
        self.assertEqual(sheet.hp, 134)
        self.assertEqual(sheet.armor_class, 21)


if __name__ == "__main__":
    unittest.main()
