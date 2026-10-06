import unittest

from sheet_mover.roll20_spell_attacks import (
    build_spell_attack_plan,
    map_spell_attack_row,
    spell_attack_row_id,
)


class Stage10BSpellAttackTests(unittest.TestCase):
    def _payload(self):
        def spell(source_id, name, level, save=None, attack=None):
            return {
                "source_key": f"spell:{source_id}",
                "source_id": source_id,
                "definition_id": source_id,
                "name": name,
                "original_name": name.split(" (")[-1].rstrip(")") if " (" in name else name,
                "description": f"<p>{name} 전체 설명</p>",
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

        spells = [
            spell("bb", "부밍 블레이드 (Booming Blade)", 0, attack=1),
            spell("toll", "톨 더 데드 (Toll the Dead)", 0, save=5),
            spell("fam", "사역마 찾기 (Find Familiar)", 1),
            spell("sleep", "수면 (Sleep)", 1, save=5),
            spell("wave", "천둥파동 (Thunderwave)", 1, save=3),
        ]

        raw_defs = [
            {"id": "bb", "definition": {
                "asPartOfWeaponAttack": True,
                "requiresAttackRoll": True,
                "requiresSavingThrow": False,
                "attackType": 1,
                "modifiers": [],
            }},
            {"id": "toll", "definition": {
                "asPartOfWeaponAttack": False,
                "requiresAttackRoll": False,
                "requiresSavingThrow": True,
                "saveDcAbilityId": 5,
                "description": "must succeed or take damage",
                "modifiers": [{
                    "type": "damage",
                    "friendlySubtypeName": "Necrotic",
                    "restriction": "alternate d12",
                    "die": {"diceString": "1d8"},
                    "atHigherLevels": {"higherLevelDefinitions": [
                        {"level": 5, "dice": {"diceString": "2d8"}},
                        {"level": 11, "dice": {"diceString": "3d8"}},
                        {"level": 17, "dice": {"diceString": "4d8"}},
                    ]},
                }],
            }},
            {"id": "fam", "definition": {
                "requiresAttackRoll": False,
                "requiresSavingThrow": False,
                "modifiers": [],
            }},
            {"id": "sleep", "definition": {
                "requiresAttackRoll": False,
                "requiresSavingThrow": True,
                "saveDcAbilityId": 5,
                "description": "must succeed on a Wisdom saving throw",
                "modifiers": [],
            }},
            {"id": "wave", "definition": {
                "requiresAttackRoll": False,
                "requiresSavingThrow": True,
                "saveDcAbilityId": 3,
                "description": "On a successful save, half damage.",
                "modifiers": [{
                    "type": "damage",
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

        return {
            "raw_source": {"classSpells": [{"spells": raw_defs}]},
            "roll20_payload": {
                "source_character_id": "170892133",
                "character": {
                    "name": "견본 캐릭터",
                    "spellcasting": {
                        "class_rules_source": [{
                            "spell_rules": {
                                "levelSpellKnownMaxes": [0, 0, 0, 3],
                                "levelPreparedSpellMaxes": [],
                            },
                            "slots_at_level": [{"level": 1, "available": 2}],
                        }],
                        "spell_slots_source": [],
                    },
                },
                "spells": spells,
            },
        }

    def test_only_roll20_attack_output_spells_get_attack_rows(self):
        plan = build_spell_attack_plan(self._payload())
        self.assertEqual(plan["attack_count"], 3)
        names = [r["name"] for r in plan["attack_rows"]]
        self.assertEqual(
            names,
            [
                "톨 더 데드 (Toll the Dead)",
                "수면 (Sleep)",
                "천둥파동 (Thunderwave)",
            ],
        )
        self.assertNotIn("Booming Blade", " ".join(names))
        self.assertNotIn("Find Familiar", " ".join(names))

    def test_toll_attack_row_is_save_damage_not_to_hit(self):
        plan = build_spell_attack_plan(self._payload())
        row = next(r for r in plan["attack_rows"] if "Toll the Dead" in r["name"])
        f = row["fields"]
        self.assertEqual(f["atkflag"], "0")
        self.assertEqual(f["saveattr"], "Wisdom")
        self.assertTrue(f["dmgbase"].endswith("d8"))
        self.assertEqual(f["dmgtype"], "Necrotic")
        self.assertEqual(f["spellid"], row["spell_row_id"])

    def test_sleep_attack_row_is_save_only(self):
        plan = build_spell_attack_plan(self._payload())
        row = next(r for r in plan["attack_rows"] if "Sleep" in r["name"])
        f = row["fields"]
        self.assertEqual(f["atkflag"], "0")
        self.assertEqual(f["dmgflag"], "0")
        self.assertEqual(f["saveattr"], "Wisdom")
        self.assertEqual(f["saveeffect"], "성공 시 효과 없음")

    def test_thunderwave_has_upcast_damage(self):
        plan = build_spell_attack_plan(self._payload())
        row = next(r for r in plan["attack_rows"] if "Thunderwave" in r["name"])
        f = row["fields"]
        self.assertEqual(f["dmgbase"], "2d8")
        self.assertEqual(f["dmgtype"], "Thunder")
        self.assertIn("hldmg", f["hldmg"])
        self.assertEqual(f["saveattr"], "Constitution")

    def test_spell_attack_ids_are_stable(self):
        self.assertEqual(
            spell_attack_row_id("spell:toll"),
            spell_attack_row_id("spell:toll"),
        )
        self.assertEqual(len(spell_attack_row_id("spell:toll")), 20)


if __name__ == "__main__":
    unittest.main()
