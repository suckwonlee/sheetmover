# -*- coding: utf-8 -*-
import unittest
from stage5_basic_writer_v2 import build_plan


class Stage5CorrectnessV31Tests(unittest.TestCase):
    def _payload(self):
        return {
            "original": {
                "classes": [{
                    "source_id": "169549570", "definition_id": "4",
                    "name": "Paladin", "original_name": "Paladin",
                    "level": 13, "is_starting_class": True,
                    "hit_die": 10, "hit_dice_used": 0,
                    "subclass_name": "Oath of Vengeance",
                    "original_subclass_name": "Oath of Vengeance",
                    "spellcasting_ability_name": "charisma",
                }],
                "equipment": [{
                    "name": "Sentinel Shield",
                    "equipped": True, "attuned": False,
                    "can_equip": True, "can_attune": False,
                    "is_consumable": False,
                    "granted_modifiers": [{
                        "type": "bonus", "subType": "passive-perception",
                        "fixedValue": 5, "value": 5, "restriction": "",
                    }],
                }],
            },
            "roll20_payload": {"character": {
                "source_id": "125047838", "name": "바리언트 드라칸",
                "classes": [{
                    "source_id": "169549570", "definition_id": "4",
                    "name": "Paladin", "original_name": "Paladin",
                    "level": 13, "is_starting_class": True, "hit_die": 10,
                    "subclass_name": "복수의 맹세 (Oath of Vengeance)",
                    "original_subclass_name": "Oath of Vengeance",
                    "spellcasting_ability_name": "charisma",
                }],
                "total_level": 13, "proficiency_bonus": 5,
                "race": {
                    "name": "젬 드래곤본 (Gem Dragonborn)",
                    "original_name": "Gem Dragonborn", "subrace_name": "Gem",
                },
                "background": {
                    "name": "아웃랜더 (Outlander)",
                    "original_name": "Outlander",
                },
                "alignment": "", "experience": 14000,
                "ability_scores": {
                    "strength": 25, "dexterity": 8, "constitution": 18,
                    "intelligence": 8, "wisdom": 10, "charisma": 22,
                },
                "hp": 134, "max_hp": 134, "temp_hp": 0,
                "armor_class": 21, "initiative": -1,
                "speed": {"normal": {"walk": 30}},
                "currencies": {"cp": 0, "sp": 0, "ep": 0, "gp": 10, "pp": 0},
                "spellcasting": {
                    "save_dc": 20, "attack_bonus": 12,
                    "class_calculations": [{
                        "class_name": "Paladin", "ability_name": "charisma",
                        "save_dc": 20, "attack_bonus": 12,
                    }],
                },
            }},
        }

    def test_actual_character_core_fields(self):
        attrs = build_plan(self._payload())["attributes"]
        self.assertEqual(attrs["constitution"]["current"], "18")
        self.assertEqual(attrs["charisma"]["current"], "22")
        self.assertEqual(attrs["hp"]["current"], "134")
        self.assertEqual(attrs["hp"]["max"], "134")
        self.assertEqual(attrs["ac"]["current"], "21")
        self.assertEqual(
            attrs["race_display"]["current"],
            "젬 드래곤본 (Gem Dragonborn)",
        )
        self.assertTrue(attrs["class_display"]["current"].startswith("Paladin 13"))
        self.assertIn("복수의 맹세", attrs["class_display"]["current"])
        self.assertEqual(attrs["gp"]["current"], "10")
        self.assertEqual(attrs["hit_dice"]["current"], "13")
        self.assertEqual(attrs["hit_dice_max"]["current"], "13")
        self.assertEqual(attrs["passiveperceptionmod"]["current"], "5")
        self.assertEqual(attrs["spellclass"]["current"], "Paladin")

    def test_mixed_hit_dice_are_not_collapsed(self):
        payload = self._payload()
        payload["original"]["classes"].append({
            "source_id": "other", "name": "Wizard", "original_name": "Wizard",
            "level": 1, "hit_die": 6, "hit_dice_used": 0,
            "is_starting_class": False,
        })
        payload["roll20_payload"]["character"]["classes"].append({
            "source_id": "other", "name": "Wizard", "original_name": "Wizard",
            "level": 1, "hit_die": 6, "is_starting_class": False,
        })
        payload["roll20_payload"]["character"]["total_level"] = 14
        attrs = build_plan(payload)["attributes"]
        self.assertNotIn("hit_dice", attrs)
        self.assertNotIn("hit_dice_max", attrs)


if __name__ == "__main__":
    unittest.main()
