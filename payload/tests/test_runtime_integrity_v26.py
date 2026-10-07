# -*- coding: utf-8 -*-
import unittest
from types import SimpleNamespace

from sheet_mover.roll20_attacks import (
    build_attack_plan,
    map_weapon_attack,
)
from sheet_mover.roll20_features import build_feature_plan
from sheet_mover.calculator import _calculate_armor_class


class RuntimeIntegrityV26Tests(unittest.TestCase):
    def test_magic_plus3_and_archery_apply_to_musket(self):
        item = {
            "source_key": "equipment:musket",
            "source_id": "musket",
            "name": "머스킷, +3 (Musket, +3)",
            "original_name": "Musket, +3",
            "item_type": "Weapon",
            "damage": {"diceString": "1d12"},
            "damage_type": "Piercing",
            "properties": [],
        }
        raw_def = {
            "name": "Musket, +3",
            "magic": True,
            "categoryId": 2,
            "attackType": 2,
            "range": 40,
            "longRange": 120,
            "damage": {"diceString": "1d12"},
            "damageType": "Piercing",
            "grantedModifiers": [{
                "type": "bonus",
                "subType": "magic",
                "value": 3,
                "restriction": "",
            }],
        }
        payload = {
            "original": {"proficiencies": ["Martial Weapons"]},
            "raw_source": {
                "id": 138048699,
                "modifiers": {
                    "feat": [{
                        "type": "bonus",
                        "subType": "ranged-weapon-attacks",
                        "value": 2,
                        "restriction": "",
                        "isGranted": True,
                    }]
                },
            },
            "roll20_payload": {
                "character": {
                    "ability_scores": {
                        "strength": 9,
                        "dexterity": 20,
                    },
                    "proficiency_bonus": 6,
                },
                "features": [{
                    "original_name": "Archery",
                    "kind": "feat",
                }],
            },
        }
        row = map_weapon_attack(
            item,
            payload["roll20_payload"]["character"],
            raw_def,
            payload,
        )
        self.assertEqual(row["attack_bonus"], 16)
        self.assertEqual(row["damage_display"], "1d12+8 Piercing")
        self.assertEqual(row["magic_weapon_bonus"], 3)
        self.assertEqual(row["additional_attack_bonus"], 2)
        self.assertIn("3[MAGIC]", row["fields"]["rollbase"])
        self.assertIn("2[ATTACK]", row["fields"]["rollbase"])

    def test_dragons_wrath_text_bonus_applies_to_melee(self):
        item = {
            "source_key": "equipment:dragon",
            "source_id": "dragon",
            "name": "용의 분노 무기 (승천) (Dragon's Wrath Weapon (Ascendant))",
            "original_name": "Dragon's Wrath Weapon (Ascendant)",
            "item_type": "Weapon",
            "damage": {"diceString": "1d8"},
            "damage_type": "Slashing",
            "properties": [],
        }
        raw_def = {
            "name": "Dragon's Wrath Weapon (Ascendant)",
            "categoryId": 2,
            "attackType": 1,
            "damage": {"diceString": "1d8"},
            "damageType": "Slashing",
            "description": (
                "You gain a +3 bonus to attack and damage rolls "
                "made using the weapon."
            ),
        }
        payload = {
            "original": {"proficiencies": ["Martial Weapons"]},
            "raw_source": {"id": 138048699, "modifiers": {}},
            "roll20_payload": {
                "character": {
                    "ability_scores": {
                        "strength": 9,
                        "dexterity": 20,
                    },
                    "proficiency_bonus": 6,
                },
                "features": [],
            },
        }
        row = map_weapon_attack(
            item,
            payload["roll20_payload"]["character"],
            raw_def,
            payload,
        )
        self.assertEqual(row["attack_bonus"], 8)
        self.assertEqual(row["damage_display"], "1d8+2 Slashing")

    def test_real_ddb_payload_gets_unarmed_strike(self):
        payload = {
            "original": {"proficiencies": []},
            "raw_source": {
                "id": 138048699,
                "inventory": [],
                "modifiers": {},
            },
            "roll20_payload": {
                "source_character_id": "138048699",
                "character": {
                    "name": "총우럭 20렙",
                    "ability_scores": {
                        "strength": 9,
                        "dexterity": 20,
                    },
                    "proficiency_bonus": 6,
                },
                "equipment": [],
                "actions": [],
                "features": [],
            },
        }
        plan = build_attack_plan(payload)
        row = next(
            row for row in plan["rows"]
            if "Unarmed Strike" in row["name"]
        )
        self.assertEqual(row["attack_bonus"], 5)
        self.assertEqual(row["damage_display"], "0 Bludgeoning")

    def test_weapon_mastery_choices_and_pact_chain_are_visible(self):
        payload = {
            "raw_source": {
                "options": {
                    "class": [{
                        "componentId": "invocations",
                        "definition": {
                            "id": "pact-chain",
                            "name": "Pact of the Chain",
                            "description": (
                                "<p>You learn Find Familiar and can let "
                                "your familiar attack.</p>"
                            ),
                        },
                    }]
                },
                "actions": {
                    "class": [{
                        "componentId": "pact-chain",
                        "name": "Pact of the Chain: Attack",
                        "snippet": "Your familiar makes one attack.",
                    }],
                    "feat": [{
                        "componentId": "wm-feat",
                        "name": "Slow (Musket)",
                        "snippet": "Reduce Speed by 10 ft.",
                    }],
                },
                "modifiers": {
                    "feat": [
                        {
                            "type": "weapon-mastery",
                            "componentId": "wm-feat",
                            "friendlySubtypeName": "Slow (Musket)",
                            "isGranted": True,
                        },
                        {
                            "type": "weapon-mastery",
                            "componentId": "wm-feat",
                            "friendlySubtypeName": "Vex (Pistol)",
                            "isGranted": True,
                        },
                        {
                            "type": "weapon-mastery",
                            "componentId": "wm-feat",
                            "friendlySubtypeName": "Vex (Shortsword)",
                            "isGranted": True,
                        },
                    ]
                },
            },
            "roll20_payload": {
                "source_character_id": "138048699",
                "character": {
                    "name": "총우럭 20렙",
                    "classes": [
                        {"original_name": "Warlock", "level": 19},
                        {"original_name": "Fighter", "level": 1},
                    ],
                    "race": {"original_name": "Eladrin"},
                    "background": {"original_name": "Wayfarer"},
                },
                "features": [
                    {
                        "source_key": "feature:invocations",
                        "source_id": "invocations",
                        "definition_id": "invocations",
                        "kind": "class_feature",
                        "name": "엘드리치 인보케이션즈",
                        "original_name": "Eldritch Invocations",
                        "description": "Invocation parent.",
                        "required_level": 1,
                    },
                    {
                        "source_key": "feature:wm-feat",
                        "source_id": "wm-feat",
                        "definition_id": "wm-feat",
                        "kind": "feat",
                        "name": "무기 숙달 (Weapon Mastery)",
                        "original_name": "Weapon Mastery",
                        "description": "Choose weapon masteries.",
                        "required_level": None,
                    },
                ],
            },
        }
        plan = build_feature_plan(payload)
        pact = next(
            row for row in plan["rows"]
            if row["original_name"] == "Pact of the Chain"
        )
        self.assertIn(
            "Pact of the Chain: Attack",
            pact["fields"]["description"],
        )

        mastery = next(
            row for row in plan["rows"]
            if row["source_id"] == "wm-feat"
        )
        desc = mastery["fields"]["description"]
        self.assertIn("Musket (Slow)", desc)
        self.assertIn("Pistol (Vex)", desc)
        self.assertIn("Shortsword (Vex)", desc)

    def test_stat_based_unarmored_ac_bonus_combines_with_set_formula(self):
        sheet = SimpleNamespace(
            equipment=[
                {
                    "item_type": "Wondrous Item",
                    "equipped": False,
                    "attuned": True,
                    "can_equip": False,
                    "can_attune": True,
                    "is_consumable": False,
                    "granted_modifiers": [{
                        "type": "set",
                        "subType": "unarmored-armor-class",
                        "value": 5,
                        "restriction": "",
                    }],
                },
                {
                    "item_type": "Wondrous Item",
                    "equipped": False,
                    "attuned": True,
                    "can_equip": False,
                    "can_attune": True,
                    "is_consumable": False,
                    "granted_modifiers": [{
                        "type": "bonus",
                        "subType": "unarmored-armor-class",
                        "statId": 6,
                        "restriction": "",
                    }],
                },
            ],
            calculation_inputs={
                "character_values": [],
                "modifiers": {},
            },
            features=[],
            background={},
            classes=[],
        )
        ac, _ = _calculate_armor_class(
            sheet,
            {
                "strength": 9,
                "dexterity": 20,
                "constitution": 16,
                "intelligence": 8,
                "wisdom": 15,
                "charisma": 20,
            },
        )
        self.assertEqual(ac, 25)

    def test_custom_fixed_ac_item_is_not_discarded(self):
        sheet = SimpleNamespace(
            equipment=[{
                "item_type": "Armor",
                "equipped": True,
                "attuned": True,
                "can_equip": True,
                "can_attune": True,
                "is_consumable": False,
                "armor_class": 25,
                "armor_type_id": None,
                "granted_modifiers": [],
            }],
            calculation_inputs={
                "character_values": [],
                "modifiers": {},
            },
            features=[],
            background={},
            classes=[],
        )
        ac, _ = _calculate_armor_class(
            sheet,
            {
                "strength": 9,
                "dexterity": 20,
                "constitution": 16,
                "intelligence": 8,
                "wisdom": 15,
                "charisma": 20,
            },
        )
        self.assertEqual(ac, 25)


    def test_stale_sheetmover_duplicate_resource_rows_are_detected(self):
        from sheet_mover.runtime_integrity_v26 import (
            stale_duplicate_resource_row_ids,
        )
        state = {
            "rows": {
                "-SMold1": {
                    "resource_left_name": {
                        "current": "페이 스텝 (Fey Step)",
                        "max": "",
                    },
                    "resource_left": {"current": "6", "max": "6"},
                },
                "-SMkeep": {
                    "resource_left_name": {
                        "current": "행운 점수 (Luck Points)",
                        "max": "",
                    },
                },
                "manual-row": {
                    "resource_left_name": {
                        "current": "페이 스텝 (Fey Step)",
                        "max": "",
                    },
                },
            }
        }
        desired = [
            {"name": "페이 스텝 (Fey Step)"},
            {"name": "행운 점수 (Luck Points)"},
        ]
        stale = stale_duplicate_resource_row_ids(
            state,
            desired,
            ["-SMkeep"],
        )
        self.assertEqual(stale, ["-SMold1"])

    def test_nonstandard_active_item_can_supply_explicit_ac(self):
        from sheet_mover.runtime_integrity_v26 import (
            _explicit_ac_candidates,
        )
        sheet = SimpleNamespace(
            equipment=[{
                "item_type": "Wondrous Item",
                "equipped": False,
                "attuned": True,
                "can_equip": False,
                "can_attune": True,
                "is_consumable": False,
                "armor_class": 25,
                "armor_type_id": None,
                "granted_modifiers": [],
            }]
        )
        self.assertIn(
            25,
            _explicit_ac_candidates(
                sheet,
                {"dexterity": 20, "charisma": 20},
            ),
        )



if __name__ == "__main__":
    unittest.main()
