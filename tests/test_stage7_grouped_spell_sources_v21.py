import unittest

from sheet_mover.roll20_spells import build_spell_plan


class GroupedSpellSourceDamageTests(unittest.TestCase):
    def test_grouped_class_spell_keeps_moonbeam_damage_and_upcast(self):
        payload = {
            "raw_source": {
                "classSpells": [],
                "spells": {
                    "race": [],
                    "class": [{
                        "id": 8180449,
                        "definition": {
                            "id": 2619134,
                            "name": "Moonbeam",
                            "level": 2,
                            "requiresSavingThrow": True,
                            "requiresAttackRoll": False,
                            "saveDcAbilityId": 3,
                            "description": (
                                "On a failed save, a creature takes 2d10 Radiant damage. "
                                "On a successful save, a creature takes half as much damage."
                            ),
                            "modifiers": [{
                                "type": "damage",
                                "subType": "radiant",
                                "friendlySubtypeName": "Radiant",
                                "restriction": "",
                                "die": {"diceCount": 2, "diceValue": 10, "diceString": "2d10"},
                                "atHigherLevels": {
                                    "higherLevelDefinitions": [{
                                        "level": 1,
                                        "typeId": 15,
                                        "dice": {
                                            "diceCount": 1,
                                            "diceValue": 10,
                                            "fixedValue": 0,
                                            "diceString": "1d10",
                                        },
                                    }]
                                },
                            }],
                        },
                    }],
                    "background": None,
                    "item": [],
                    "feat": [],
                },
            },
            "roll20_payload": {
                "source_character_id": "153714540",
                "character": {
                    "name": "견본2",
                    "spellcasting": {
                        "class_rules_source": [{
                            "spell_rules": {
                                "levelSpellKnownMaxes": [0, 1, 1],
                                "levelPreparedSpellMaxes": [],
                            }
                        }],
                        "spell_slots_source": [],
                    },
                },
                "spells": [{
                    "source_key": "spell:8180449",
                    "source_id": "8180449",
                    "definition_id": "2619134",
                    "name": "달빛 (Moonbeam)",
                    "original_name": "Moonbeam",
                    "description": "<p>달빛 설명</p>",
                    "level": 2,
                    "prepared": False,
                    "always_prepared": True,
                    "uses_spell_slot": True,
                    "casting_time": {"activationTime": 1, "activationType": 1},
                    "range": {"origin": "Ranged", "rangeValue": 120, "aoeType": "Cylinder", "aoeValue": 5},
                    "duration": {"durationInterval": 1, "durationUnit": "Minute", "durationType": "Concentration"},
                    "components": [1, 2, 3],
                    "components_description": "a moonseed leaf",
                    "school": "Evocation",
                    "ritual": False,
                    "concentration": True,
                    "save_dc_ability_id": 3,
                    "attack_type": None,
                    "counts_as_known_spell": False,
                }],
            },
        }

        row = build_spell_plan(payload)["rows"][0]
        fields = row["fields"]
        self.assertEqual(fields["spelloutput"], "ATTACK")
        self.assertEqual(fields["spellsave"], "Constitution")
        self.assertEqual(fields["spelldamage"], "2d10")
        self.assertEqual(fields["spelldamagetype"], "Radiant")
        self.assertEqual(fields["spellsavesuccess"], "성공 시 절반 피해")
        self.assertEqual(fields["spellhldie"], "1")
        self.assertEqual(fields["spellhldietype"], "d10")


if __name__ == "__main__":
    unittest.main()
