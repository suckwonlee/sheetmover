import unittest

from sheet_mover.calculator import (
    ability_modifier,
    apply_stage2_calculations,
    proficiency_bonus_for_level,
)
from sheet_mover.models import CharacterSheet


def _stat_rows(values):
    return [
        {"id": index, "value": values.get(index)}
        for index in range(1, 7)
    ]


def _base_inputs(
    scores=None,
    *,
    level=1,
    base_hp=8,
    removed=0,
    modifiers=None,
):
    return {
        "stats": _stat_rows(
            scores
            or {1: 10, 2: 10, 3: 10, 4: 10, 5: 10, 6: 10}
        ),
        "bonus_stats": _stat_rows({}),
        "override_stats": _stat_rows({}),
        "modifiers": modifiers or {},
        "character_values": [],
        "base_hit_points": base_hp,
        "bonus_hit_points": None,
        "override_hit_points": None,
        "removed_hit_points": removed,
        "temporary_hit_points": 0,
        "class_levels": [{"name": "Test", "level": level, "hit_die": 8}],
        "class_feature_levels": [],
        "race_movement": {"normal": {"walk": 30}},
    }


class Stage2CalculatorTests(unittest.TestCase):
    def test_ability_modifier_uses_floor_for_negative_scores(self):
        self.assertEqual(ability_modifier(10), 0)
        self.assertEqual(ability_modifier(11), 0)
        self.assertEqual(ability_modifier(9), -1)
        self.assertEqual(ability_modifier(1), -5)

    def test_proficiency_bonus_progression(self):
        self.assertEqual(proficiency_bonus_for_level(1), 2)
        self.assertEqual(proficiency_bonus_for_level(4), 2)
        self.assertEqual(proficiency_bonus_for_level(5), 3)
        self.assertEqual(proficiency_bonus_for_level(9), 4)
        self.assertEqual(proficiency_bonus_for_level(13), 5)
        self.assertEqual(proficiency_bonus_for_level(17), 6)

    def test_sample_character_stage2_values(self):
        sheet = CharacterSheet(
            background={"source_id": "406479"},
            classes=[
                {
                    "source_id": "238246980",
                    "definition_id": "2190879",
                    "name": "Fighter",
                    "level": 3,
                    "subclass_id": "2190937",
                }
            ],
            features=[
                {
                    "source_id": "1789140",
                    "definition_id": "1789140",
                    "kind": "feat",
                    "name": "Farmer Ability Score Improvements",
                },
                {
                    "source_id": "1789206",
                    "definition_id": "1789206",
                    "kind": "feat",
                    "name": "Tough",
                },
            ],
            spellcasting={
                "class_abilities": [
                    {
                        "class_name": "Fighter",
                        "subclass_name": "Eldritch Knight",
                        "ability_id": 4,
                        "ability_name": "intelligence",
                    }
                ],
                "save_dc": None,
                "attack_bonus": None,
            },
            equipment=[],
            calculation_inputs={
                "stats": _stat_rows(
                    {1: 15, 2: 11, 3: 15, 4: 10, 5: 12, 6: 8}
                ),
                "bonus_stats": _stat_rows({}),
                "override_stats": _stat_rows({}),
                "modifiers": {
                    "race": [
                        {
                            "type": "bonus",
                            "subType": "choose-an-ability-score",
                            "value": 1,
                            "fixedValue": 1,
                            "componentId": 102,
                            "isGranted": True,
                        },
                        {
                            "type": "bonus",
                            "subType": "choose-an-ability-score",
                            "value": 1,
                            "fixedValue": 1,
                            "componentId": 102,
                            "isGranted": True,
                        },
                    ],
                    "feat": [
                        {
                            "type": "bonus",
                            "subType": "hit-points-per-level",
                            "value": 2,
                            "componentId": 1789206,
                            "isGranted": True,
                            "restriction": "",
                        },
                        {
                            "type": "bonus",
                            "subType": "strength-score",
                            "value": 2,
                            "componentId": 1789140,
                            "isGranted": False,
                            "restriction": "",
                        },
                        {
                            "type": "bonus",
                            "subType": "constitution-score",
                            "value": 1,
                            "componentId": 1789140,
                            "isGranted": False,
                            "restriction": "",
                        },
                    ],
                },
                "character_values": [],
                "base_hit_points": 22,
                "bonus_hit_points": None,
                "override_hit_points": None,
                "removed_hit_points": 0,
                "temporary_hit_points": 0,
                "class_levels": [
                    {"name": "Fighter", "level": 3, "hit_die": 10}
                ],
                "class_feature_levels": [],
                "race_movement": {"normal": {"walk": 30}},
            },
        )

        apply_stage2_calculations(sheet)

        self.assertEqual(sheet.total_level, 3)
        self.assertEqual(sheet.proficiency_bonus, 2)
        self.assertEqual(
            sheet.ability_scores,
            {
                "strength": 17,
                "dexterity": 11,
                "constitution": 16,
                "intelligence": 10,
                "wisdom": 12,
                "charisma": 8,
            },
        )
        self.assertEqual(sheet.max_hp, 37)
        self.assertEqual(sheet.hp, 37)
        self.assertEqual(sheet.temp_hp, 0)
        self.assertEqual(sheet.initiative, 0)
        self.assertEqual(sheet.armor_class, 10)
        self.assertEqual(sheet.spellcasting["save_dc"], 10)
        self.assertEqual(sheet.spellcasting["attack_bonus"], 2)

    def test_inactive_legacy_race_asi_is_not_applied(self):
        sheet = CharacterSheet(
            features=[],
            calculation_inputs=_base_inputs(
                {1: 12, 2: 10, 3: 10, 4: 10, 5: 10, 6: 10},
                modifiers={
                    "race": [
                        {
                            "type": "bonus",
                            "subType": "strength-score",
                            "value": 2,
                            "componentId": 999,
                            "isGranted": True,
                            "restriction": "",
                        }
                    ]
                },
            ),
        )
        apply_stage2_calculations(sheet)
        self.assertEqual(sheet.ability_scores["strength"], 12)

    def test_active_feat_modifier_applies_even_when_is_granted_false(self):
        sheet = CharacterSheet(
            features=[
                {
                    "kind": "feat",
                    "source_id": "55",
                    "definition_id": "55",
                }
            ],
            calculation_inputs=_base_inputs(
                modifiers={
                    "feat": [
                        {
                            "type": "bonus",
                            "subType": "strength-score",
                            "value": 2,
                            "componentId": 55,
                            "isGranted": False,
                            "restriction": "",
                        }
                    ]
                },
            ),
        )
        apply_stage2_calculations(sheet)
        self.assertEqual(sheet.ability_scores["strength"], 12)

    def test_explicit_ability_override_wins(self):
        inputs = _base_inputs(
            {1: 10, 2: 10, 3: 10, 4: 10, 5: 10, 6: 10},
            modifiers={
                "race": [
                    {
                        "type": "bonus",
                        "subType": "strength-score",
                        "value": 2,
                        "restriction": "",
                    }
                ]
            },
        )
        inputs["bonus_stats"] = _stat_rows({1: 1})
        inputs["override_stats"] = _stat_rows({1: 20})
        sheet = CharacterSheet(calculation_inputs=inputs)
        apply_stage2_calculations(sheet)
        self.assertEqual(sheet.ability_scores["strength"], 20)

    def test_zero_ability_override_means_no_override(self):
        inputs = _base_inputs(
            {1: 12, 2: 10, 3: 10, 4: 10, 5: 10, 6: 10}
        )
        inputs["override_stats"] = _stat_rows({1: 0})
        sheet = CharacterSheet(calculation_inputs=inputs)
        apply_stage2_calculations(sheet)
        self.assertEqual(sheet.ability_scores["strength"], 12)

    def test_normal_ability_bonus_is_capped_at_20(self):
        sheet = CharacterSheet(
            features=[{"kind": "feat", "source_id": "77", "definition_id": "77"}],
            calculation_inputs=_base_inputs(
                {1: 19, 2: 10, 3: 10, 4: 10, 5: 10, 6: 10},
                modifiers={
                    "feat": [
                        {
                            "type": "bonus",
                            "subType": "strength-score",
                            "value": 2,
                            "componentId": 77,
                            "restriction": "",
                        }
                    ]
                },
            ),
        )
        apply_stage2_calculations(sheet)
        self.assertEqual(sheet.ability_scores["strength"], 20)

    def test_unknown_conditional_ability_bonus_fails_closed_for_that_score(self):
        sheet = CharacterSheet(
            features=[{"kind": "feat", "source_id": "77", "definition_id": "77"}],
            calculation_inputs=_base_inputs(
                modifiers={
                    "feat": [
                        {
                            "type": "bonus",
                            "subType": "strength-score",
                            "value": 2,
                            "componentId": 77,
                            "restriction": "While in moonlight",
                        }
                    ]
                },
            ),
        )
        apply_stage2_calculations(sheet)
        self.assertIsNone(sheet.ability_scores["strength"])
        self.assertEqual(sheet.ability_scores["dexterity"], 10)

    def test_unresolved_active_choice_fails_closed(self):
        sheet = CharacterSheet(
            features=[
                {
                    "kind": "racial_trait",
                    "source_id": "100",
                    "definition_id": "100",
                }
            ],
            calculation_inputs=_base_inputs(
                modifiers={
                    "race": [
                        {
                            "type": "bonus",
                            "subType": "choose-an-ability-score",
                            "value": 1,
                            "componentId": 100,
                            "isGranted": True,
                            "restriction": "",
                        }
                    ]
                },
            ),
        )
        apply_stage2_calculations(sheet)
        self.assertTrue(
            all(value is None for value in sheet.ability_scores.values())
        )
        self.assertTrue(
            any("능력치 선택/보정" in warning for warning in sheet.warnings)
        )

    def test_hp_override_and_removed_hp(self):
        inputs = _base_inputs(level=5)
        inputs["override_hit_points"] = 30
        inputs["removed_hit_points"] = 12
        inputs["temporary_hit_points"] = 4
        sheet = CharacterSheet(calculation_inputs=inputs)
        apply_stage2_calculations(sheet)
        self.assertEqual(sheet.max_hp, 30)
        self.assertEqual(sheet.hp, 18)
        self.assertEqual(sheet.temp_hp, 4)

    def test_zero_hp_override_means_calculate_normally(self):
        inputs = _base_inputs(
            {1: 10, 2: 10, 3: 14, 4: 10, 5: 10, 6: 10},
            level=2,
            base_hp=12,
        )
        inputs["override_hit_points"] = 0
        sheet = CharacterSheet(calculation_inputs=inputs)
        apply_stage2_calculations(sheet)
        self.assertEqual(sheet.max_hp, 16)
        self.assertEqual(sheet.hp, 16)

    def test_bonus_hit_points_are_included_in_roll20_max(self):
        inputs = _base_inputs(level=1, base_hp=8)
        inputs["bonus_hit_points"] = 5
        sheet = CharacterSheet(calculation_inputs=inputs)
        apply_stage2_calculations(sheet)
        self.assertEqual(sheet.max_hp, 13)
        self.assertEqual(sheet.hp, 13)

    def test_class_specific_hp_per_level_uses_class_level_not_total_level(self):
        sheet = CharacterSheet(
            features=[
                {
                    "kind": "class_feature",
                    "source_id": "900",
                    "definition_id": "900",
                }
            ],
            calculation_inputs={
                **_base_inputs(level=1, base_hp=20),
                "class_levels": [
                    {"name": "A", "level": 2, "hit_die": 8},
                    {"name": "B", "level": 3, "hit_die": 8},
                ],
                "class_feature_levels": [
                    {"feature_id": "900", "class_name": "A", "class_level": 2}
                ],
                "modifiers": {
                    "class": [
                        {
                            "type": "bonus",
                            "subType": "hit-points-per-level",
                            "value": 1,
                            "componentId": 900,
                            "restriction": "",
                        }
                    ]
                },
            },
        )
        apply_stage2_calculations(sheet)
        self.assertEqual(sheet.total_level, 5)
        self.assertEqual(sheet.max_hp, 22)

    def test_dice_only_healing_rider_does_not_raise_max_hp(self):
        sheet = CharacterSheet(
            features=[{"kind": "feat", "source_id": "5", "definition_id": "5"}],
            calculation_inputs=_base_inputs(
                modifiers={
                    "feat": [
                        {
                            "type": "bonus",
                            "subType": "hit-points",
                            "value": None,
                            "fixedValue": 2,
                            "dice": {"diceString": "2d8 + 2"},
                            "componentId": 5,
                            "restriction": "",
                        }
                    ]
                },
            ),
        )
        apply_stage2_calculations(sheet)
        self.assertEqual(sheet.max_hp, 8)

    def test_standard_armor_types_and_shield(self):
        common = _base_inputs(
            {1: 10, 2: 16, 3: 10, 4: 10, 5: 10, 6: 10}
        )

        cases = (
            (1, 12, 15),
            (2, 14, 16),
            (3, 16, 16),
        )
        for armor_type, base_ac, expected in cases:
            with self.subTest(armor_type=armor_type):
                sheet = CharacterSheet(
                    equipment=[
                        {
                            "item_type": "Armor",
                            "equipped": True,
                            "attuned": False,
                            "armor_type_id": armor_type,
                            "armor_class": base_ac,
                            "can_equip": True,
                            "can_attune": False,
                            "is_consumable": False,
                            "granted_modifiers": [],
                        }
                    ],
                    calculation_inputs=deepcopy_dict(common),
                )
                apply_stage2_calculations(sheet)
                self.assertEqual(sheet.armor_class, expected)

        shielded = CharacterSheet(
            equipment=[
                {
                    "item_type": "Armor",
                    "equipped": True,
                    "attuned": False,
                    "armor_type_id": 3,
                    "armor_class": 16,
                    "can_equip": True,
                    "can_attune": False,
                    "is_consumable": False,
                    "granted_modifiers": [],
                },
                {
                    "item_type": "Armor",
                    "equipped": True,
                    "attuned": False,
                    "armor_type_id": 4,
                    "armor_class": 2,
                    "can_equip": True,
                    "can_attune": False,
                    "is_consumable": False,
                    "granted_modifiers": [],
                },
            ],
            calculation_inputs=deepcopy_dict(common),
        )
        apply_stage2_calculations(shielded)
        self.assertEqual(shielded.armor_class, 18)

    def test_magic_gear_ac_bonus_is_not_double_counted(self):
        sheet = CharacterSheet(
            equipment=[
                {
                    "source_id": "1000",
                    "definition_id": "2000",
                    "item_type": "Wondrous Item",
                    "equipped": True,
                    "attuned": True,
                    "can_equip": True,
                    "can_attune": True,
                    "is_consumable": False,
                    "granted_modifiers": [
                        {
                            "type": "bonus",
                            "subType": "armor-class",
                            "value": 1,
                            "restriction": "",
                        }
                    ],
                }
            ],
            calculation_inputs=_base_inputs(
                modifiers={
                    "item": [
                        {
                            "type": "bonus",
                            "subType": "armor-class",
                            "value": 1,
                            "componentId": 2000,
                            "restriction": "",
                        }
                    ]
                },
            ),
        )
        apply_stage2_calculations(sheet)
        self.assertEqual(sheet.armor_class, 11)

    def test_unequipped_attunable_item_does_not_change_ac(self):
        sheet = CharacterSheet(
            equipment=[
                {
                    "source_id": "1000",
                    "definition_id": "2000",
                    "item_type": "Wondrous Item",
                    "equipped": False,
                    "attuned": False,
                    "can_equip": True,
                    "can_attune": True,
                    "is_consumable": False,
                    "granted_modifiers": [
                        {
                            "type": "bonus",
                            "subType": "armor-class",
                            "value": 3,
                            "restriction": "",
                        }
                    ],
                }
            ],
            calculation_inputs=_base_inputs(),
        )
        apply_stage2_calculations(sheet)
        self.assertEqual(sheet.armor_class, 10)

    def test_unarmored_defense_set_modifier(self):
        sheet = CharacterSheet(
            features=[
                {
                    "kind": "class_feature",
                    "source_id": "300",
                    "definition_id": "300",
                }
            ],
            calculation_inputs=_base_inputs(
                {1: 10, 2: 14, 3: 10, 4: 10, 5: 16, 6: 10},
                modifiers={
                    "class": [
                        {
                            "type": "set",
                            "subType": "unarmored-armor-class",
                            "value": 0,
                            "statId": 5,
                            "componentId": 300,
                            "restriction": "",
                        }
                    ]
                },
            ),
        )
        apply_stage2_calculations(sheet)
        self.assertEqual(sheet.armor_class, 15)

    def test_character_value_ac_override_wins(self):
        inputs = _base_inputs()
        inputs["character_values"] = [{"typeId": 1, "value": 23}]
        sheet = CharacterSheet(
            equipment=[
                {
                    "item_type": "Armor",
                    "equipped": True,
                    "armor_type_id": 3,
                    "armor_class": 16,
                    "can_equip": True,
                    "can_attune": False,
                    "is_consumable": False,
                    "granted_modifiers": [],
                }
            ],
            calculation_inputs=inputs,
        )
        apply_stage2_calculations(sheet)
        self.assertEqual(sheet.armor_class, 23)

    def test_initiative_bonus_is_added_to_dexterity(self):
        sheet = CharacterSheet(
            features=[{"kind": "feat", "source_id": "50", "definition_id": "50"}],
            calculation_inputs=_base_inputs(
                {1: 10, 2: 14, 3: 10, 4: 10, 5: 10, 6: 10},
                modifiers={
                    "feat": [
                        {
                            "type": "bonus",
                            "subType": "initiative",
                            "value": 3,
                            "componentId": 50,
                            "restriction": "",
                        }
                    ]
                },
            ),
        )
        apply_stage2_calculations(sheet)
        self.assertEqual(sheet.initiative, 5)

    def test_multiclass_spellcasting_keeps_per_class_values(self):
        sheet = CharacterSheet(
            spellcasting={
                "class_abilities": [
                    {
                        "class_name": "Wizard",
                        "subclass_name": "",
                        "ability_id": 4,
                        "ability_name": "intelligence",
                    },
                    {
                        "class_name": "Cleric",
                        "subclass_name": "",
                        "ability_id": 5,
                        "ability_name": "wisdom",
                    },
                ]
            },
            calculation_inputs={
                **_base_inputs(
                    {1: 10, 2: 10, 3: 10, 4: 16, 5: 14, 6: 10},
                    level=1,
                    base_hp=30,
                ),
                "class_levels": [
                    {"name": "Wizard", "level": 3, "hit_die": 6},
                    {"name": "Cleric", "level": 2, "hit_die": 8},
                ],
            },
        )
        apply_stage2_calculations(sheet)
        self.assertEqual(sheet.total_level, 5)
        self.assertEqual(sheet.proficiency_bonus, 3)
        self.assertIsNone(sheet.spellcasting["save_dc"])
        self.assertIsNone(sheet.spellcasting["attack_bonus"])
        rows = sheet.spellcasting["class_calculations"]
        self.assertEqual(rows[0]["save_dc"], 14)
        self.assertEqual(rows[0]["attack_bonus"], 6)
        self.assertEqual(rows[1]["save_dc"], 13)
        self.assertEqual(rows[1]["attack_bonus"], 5)

    def test_spell_save_and_attack_modifiers_are_included(self):
        sheet = CharacterSheet(
            features=[{"kind": "feat", "source_id": "60", "definition_id": "60"}],
            spellcasting={
                "class_abilities": [
                    {
                        "class_name": "Wizard",
                        "subclass_name": "",
                        "ability_id": 4,
                        "ability_name": "intelligence",
                    }
                ]
            },
            calculation_inputs=_base_inputs(
                {1: 10, 2: 10, 3: 10, 4: 16, 5: 10, 6: 10},
                level=5,
                modifiers={
                    "feat": [
                        {
                            "type": "bonus",
                            "subType": "spell-save-dc",
                            "value": 1,
                            "componentId": 60,
                            "restriction": "",
                        },
                        {
                            "type": "bonus",
                            "subType": "spell-attacks",
                            "value": 2,
                            "componentId": 60,
                            "restriction": "",
                        },
                    ]
                },
            ),
        )
        apply_stage2_calculations(sheet)
        self.assertEqual(sheet.spellcasting["save_dc"], 15)
        self.assertEqual(sheet.spellcasting["attack_bonus"], 8)


def deepcopy_dict(value):
    import copy
    return copy.deepcopy(value)


if __name__ == "__main__":
    unittest.main()
