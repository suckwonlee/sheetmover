import unittest

from sheet_mover.roll20_spells import (
    ROLLCONTENT,
    SPELL_FIELDS,
    build_spell_plan,
    map_spell_row,
    spell_row_id,
)


class Stage7SpellCombatFieldTests(unittest.TestCase):
    def _character(self):
        return {
            "name": "견본 캐릭터",
            "spellcasting": {
                "class_rules_source": [{
                    "spell_rules": {
                        "levelSpellKnownMaxes": [0, 0, 0, 3],
                        "levelPreparedSpellMaxes": [],
                    },
                    "slots_at_level": [{"level": 1, "available": 2}],
                }],
                "spell_slots_source": [{"level": 1, "used": 0, "available": 0}],
            },
        }

    def _spell(self, source_id, name, level, save=None, attack=None):
        return {
            "source_key": f"spell:{source_id}",
            "source_id": str(source_id),
            "definition_id": str(source_id),
            "name": name,
            "original_name": name.split(" (")[-1].rstrip(")") if " (" in name else name,
            "description": f"<p>{name} 설명</p>",
            "level": level,
            "prepared": False,
            "always_prepared": False,
            "uses_spell_slot": level > 0,
            "casting_time": {"activationTime": 1, "activationType": 1},
            "range": {"origin": "Ranged", "rangeValue": 60, "aoeType": None, "aoeValue": None},
            "duration": {"durationInterval": 0, "durationUnit": None, "durationType": "Instantaneous"},
            "components": [1, 2],
            "components_description": "",
            "school": "Evocation",
            "ritual": False,
            "concentration": False,
            "save_dc_ability_id": save,
            "attack_type": attack,
            "counts_as_known_spell": True,
        }

    def _payload(self):
        spells = [
            self._spell("bb", "부밍 블레이드 (Booming Blade)", 0, attack=1),
            self._spell("toll", "톨 더 데드 (Toll the Dead)", 0, save=5),
            self._spell("fam", "사역마 찾기 (Find Familiar)", 1),
            self._spell("sleep", "수면 (Sleep)", 1, save=5),
            self._spell("wave", "천둥파동 (Thunderwave)", 1, save=3),
        ]
        return {
            "raw_source": {
                "classSpells": [{
                    "spells": [
                        {"id": "bb", "definition": {
                            "name": "Booming Blade",
                            "asPartOfWeaponAttack": True,
                            "requiresAttackRoll": True,
                            "requiresSavingThrow": False,
                            "attackType": 1,
                            "modifiers": [],
                        }},
                        {"id": "toll", "definition": {
                            "name": "Toll the Dead",
                            "asPartOfWeaponAttack": False,
                            "requiresAttackRoll": False,
                            "requiresSavingThrow": True,
                            "saveDcAbilityId": 5,
                            "description": "The target must succeed on a Wisdom saving throw or take damage.",
                            "modifiers": [{
                                "type": "damage",
                                "subType": "necrotic",
                                "friendlySubtypeName": "Necrotic",
                                "restriction": "The die type becomes a d12 if injured.",
                                "die": {"diceString": "1d8"},
                                "atHigherLevels": {"higherLevelDefinitions": [
                                    {"level": 5, "dice": {"diceString": "2d8"}},
                                    {"level": 11, "dice": {"diceString": "3d8"}},
                                    {"level": 17, "dice": {"diceString": "4d8"}},
                                ]},
                            }],
                        }},
                        {"id": "fam", "definition": {
                            "name": "Find Familiar",
                            "requiresAttackRoll": False,
                            "requiresSavingThrow": False,
                            "modifiers": [],
                        }},
                        {"id": "sleep", "definition": {
                            "name": "Sleep",
                            "requiresAttackRoll": False,
                            "requiresSavingThrow": True,
                            "saveDcAbilityId": 5,
                            "description": "Each creature must succeed on a Wisdom saving throw.",
                            "modifiers": [],
                        }},
                        {"id": "wave", "definition": {
                            "name": "Thunderwave",
                            "requiresAttackRoll": False,
                            "requiresSavingThrow": True,
                            "saveDcAbilityId": 3,
                            "description": "On a successful save, a creature takes half as much damage.",
                            "modifiers": [{
                                "type": "damage",
                                "subType": "thunder",
                                "friendlySubtypeName": "Thunder",
                                "restriction": "",
                                "die": {"diceString": "2d8"},
                                "atHigherLevels": {"higherLevelDefinitions": [
                                    {"level": 1, "dice": {
                                        "diceCount": 1,
                                        "diceValue": 8,
                                        "fixedValue": 0,
                                        "diceString": "1d8",
                                    }}
                                ]},
                            }],
                        }},
                    ]
                }]
            },
            "roll20_payload": {
                "source_character_id": "170892133",
                "character": self._character(),
                "spells": spells,
            },
        }

    def test_sample_classification(self):
        plan = build_spell_plan(self._payload())
        outputs = {r["name"]: r["fields"]["spelloutput"] for r in plan["rows"]}
        self.assertEqual(outputs["부밍 블레이드 (Booming Blade)"], "SPELLCARD")
        self.assertEqual(outputs["사역마 찾기 (Find Familiar)"], "SPELLCARD")
        self.assertEqual(outputs["톨 더 데드 (Toll the Dead)"], "ATTACK")
        self.assertEqual(outputs["수면 (Sleep)"], "ATTACK")
        self.assertEqual(outputs["천둥파동 (Thunderwave)"], "ATTACK")

    def test_toll_the_dead_fields(self):
        plan = build_spell_plan(self._payload())
        row = next(r for r in plan["rows"] if "Toll the Dead" in r["name"])
        self.assertEqual(row["fields"]["spellattack"], "None")
        self.assertEqual(row["fields"]["spellsave"], "Wisdom")
        self.assertEqual(row["fields"]["spelldamage"], "1d8")
        self.assertEqual(row["fields"]["spelldamagetype"], "Necrotic")
        self.assertEqual(row["fields"]["spell_damage_progression"], "Cantrip Dice")
        self.assertEqual(row["fields"]["includedesc"], "on")

    def test_thunderwave_fields(self):
        plan = build_spell_plan(self._payload())
        row = next(r for r in plan["rows"] if "Thunderwave" in r["name"])
        self.assertEqual(row["fields"]["spellsave"], "Constitution")
        self.assertEqual(row["fields"]["spellsavesuccess"], "성공 시 절반 피해")
        self.assertEqual(row["fields"]["spelldamage"], "2d8")
        self.assertEqual(row["fields"]["spellhldie"], "1")
        self.assertEqual(row["fields"]["spellhldietype"], "d8")

    def test_sleep_is_save_only_attack_output(self):
        plan = build_spell_plan(self._payload())
        row = next(r for r in plan["rows"] if "Sleep" in r["name"])
        self.assertEqual(row["fields"]["spelloutput"], "ATTACK")
        self.assertEqual(row["fields"]["spellsave"], "Wisdom")
        self.assertEqual(row["fields"]["spelldamage"], "")
        self.assertEqual(row["fields"]["spellsavesuccess"], "성공 시 효과 없음")

    def test_booming_blade_does_not_become_wrong_spell_attack(self):
        plan = build_spell_plan(self._payload())
        row = next(r for r in plan["rows"] if "Booming Blade" in r["name"])
        self.assertTrue(row["combat"]["as_part_of_weapon_attack"])
        self.assertEqual(row["fields"]["spelloutput"], "SPELLCARD")
        self.assertEqual(row["fields"]["spellattackid"], "")
        self.assertEqual(row["fields"]["rollcontent"], ROLLCONTENT)

    def test_spell_damage_progression_is_managed_field(self):
        self.assertIn("spell_damage_progression", SPELL_FIELDS)

    def test_row_id_still_stable(self):
        self.assertEqual(
            spell_row_id("spell:abc"),
            spell_row_id("spell:abc"),
        )


if __name__ == "__main__":
    unittest.main()
