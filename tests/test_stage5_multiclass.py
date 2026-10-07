import unittest
from stage5_basic_writer_v2 import build_plan

def payload():
    return {
        "roll20_payload": {
            "character": {
                "source_id": "153714540",
                "name": "견본2",
                "classes": [
                    {
                        "name": "소서러 (Sorcerer)",
                        "original_name": "Sorcerer",
                        "level": 1,
                        "is_starting_class": True,
                        "subclass_name": "",
                        "original_subclass_name": "",
                        "spellcasting_ability_name": "charisma",
                    },
                    {
                        "name": "음유시인 (Bard)",
                        "original_name": "Bard",
                        "level": 6,
                        "is_starting_class": False,
                        "subclass_name": "달의 대학 (College of the Moon)",
                        "original_subclass_name": "College of the Moon",
                        "spellcasting_ability_name": "charisma",
                    },
                ],
                "total_level": 7,
                "proficiency_bonus": 3,
                "race": {"name": "인간 (Human)", "original_name": "Human"},
                "background": {"name": "하우스 요원", "original_name": "House Agent"},
                "alignment": "",
                "experience": 0,
                "ability_scores": {
                    "strength": 9, "dexterity": 14, "constitution": 16,
                    "intelligence": 8, "wisdom": 14, "charisma": 14,
                },
                "hp": 45,
                "max_hp": 45,
                "temp_hp": 0,
                "armor_class": 12,
                "initiative": 2,
                "speed": {"normal": {"walk": 30}},
                "spellcasting": {
                    "save_dc": 13,
                    "attack_bonus": 5,
                    "class_calculations": [
                        {"class_name": "Sorcerer", "ability_name": "charisma",
                         "save_dc": 13, "attack_bonus": 5},
                        {"class_name": "Bard", "ability_name": "charisma",
                         "save_dc": 13, "attack_bonus": 5},
                    ],
                },
            }
        }
    }

class Stage5MulticlassTests(unittest.TestCase):
    def test_sorcerer1_bard6(self):
        attrs = build_plan(payload())["attributes"]
        self.assertEqual(attrs["class"]["current"], "Sorcerer")
        self.assertEqual(attrs["base_level"]["current"], "1")
        self.assertEqual(attrs["multiclass1_flag"]["current"], "1")
        self.assertEqual(attrs["multiclass1"]["current"], "Bard")
        self.assertEqual(attrs["multiclass1_lvl"]["current"], "6")
        self.assertEqual(attrs["level"]["current"], "7")
        self.assertEqual(attrs["pb"]["current"], "3")
        self.assertEqual(attrs["caster_level"]["current"], "7")
        self.assertEqual(attrs["spellcasting_ability"]["current"], "@{charisma_mod}+")
        self.assertEqual(attrs["spell_save_dc"]["current"], "13")
        self.assertEqual(attrs["spell_attack_bonus"]["current"], "5")
        self.assertIn("Sorcerer 1", attrs["class_display"]["current"])
        self.assertIn("Bard 6", attrs["class_display"]["current"])

    def test_starting_class_not_position_dependent(self):
        p = payload()
        rows = p["roll20_payload"]["character"]["classes"]
        rows[:] = [rows[1], rows[0]]
        attrs = build_plan(p)["attributes"]
        self.assertEqual(attrs["class"]["current"], "Sorcerer")
        self.assertEqual(attrs["multiclass1"]["current"], "Bard")

    def test_more_than_four_classes_is_rejected(self):
        p = payload()
        rows = p["roll20_payload"]["character"]["classes"]
        for i in range(3):
            rows.append({
                "name": f"Extra{i}",
                "original_name": f"Extra{i}",
                "level": 1,
                "is_starting_class": False,
            })
        with self.assertRaisesRegex(RuntimeError, "최대 4개 클래스"):
            build_plan(p)

if __name__ == "__main__":
    unittest.main()
