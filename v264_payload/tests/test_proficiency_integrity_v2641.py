# -*- coding: utf-8 -*-
import unittest

from sheet_mover.roll20_proficiencies import (
    _translated_languages,
    build_proficiency_plan,
    plan_attributes,
)


class ProficiencyIntegrityV2641Tests(unittest.TestCase):
    def _payload(self):
        profs = [
            "Bagpipes",
            "Martial Weapons",
            "Simple Weapons",
            "Light Armor",
            "Shields",
            "Heavy Armor",
            "Medium Armor",
        ]
        entries = [
            {"type": "proficiency", "subType": "bagpipes", "friendlySubtypeName": "Bagpipes", "entityTypeId": 2103445194, "source_group": "background"},
            {"type": "proficiency", "subType": "martial-weapons", "friendlySubtypeName": "Martial Weapons", "entityTypeId": 660121713, "source_group": "class"},
            {"type": "proficiency", "subType": "simple-weapons", "friendlySubtypeName": "Simple Weapons", "entityTypeId": 660121713, "source_group": "class"},
            {"type": "proficiency", "subType": "light-armor", "friendlySubtypeName": "Light Armor", "entityTypeId": 174869515, "source_group": "class"},
            {"type": "proficiency", "subType": "shields", "friendlySubtypeName": "Shields", "entityTypeId": 174869515, "source_group": "class"},
            {"type": "proficiency", "subType": "heavy-armor", "friendlySubtypeName": "Heavy Armor", "entityTypeId": 174869515, "source_group": "class"},
            {"type": "proficiency", "subType": "medium-armor", "friendlySubtypeName": "Medium Armor", "entityTypeId": 174869515, "source_group": "class"},
        ]
        return {
            "original": {
                "source_id": "125047838",
                "name": "바리언트 드라칸",
                "ability_scores": {
                    "strength": 25, "dexterity": 8, "constitution": 18,
                    "intelligence": 8, "wisdom": 10, "charisma": 22,
                },
                "proficiency_bonus": 5,
                "languages": ["Giant", "Common", "Draconic", "Telepathy"],
                "proficiencies": profs,
                "proficiency_entries": entries,
                "skill_proficiencies": [],
                "saving_throw_proficiencies": [],
                "features": [],
                "classes": [],
                "calculation_inputs": {},
            },
            "translated": {
                "languages": ["거인어", "공용어", "용언", "텔레파시"],
                "proficiencies": [
                    "백파이프", "군용 무기", "단순 무기", "경갑", "방패", "중갑", "평갑"
                ],
            },
            "roll20_payload": {
                "source_character_id": "125047838",
                "character": {
                    "name": "바리언트 드라칸",
                    "ability_scores": {
                        "strength": 25, "dexterity": 8, "constitution": 18,
                        "intelligence": 8, "wisdom": 10, "charisma": 22,
                    },
                    "proficiency_bonus": 5,
                },
            },
        }

    def test_real_paladin_languages_and_non_skill_proficiencies_are_not_dropped(self):
        plan = build_proficiency_plan(self._payload())
        self.assertEqual(plan["language_count"], 4)
        self.assertEqual(plan["tool_proficiency_count"], 1)
        self.assertEqual(plan["weapon_proficiency_count"], 2)
        self.assertEqual(plan["armor_proficiency_count"], 4)
        self.assertEqual(plan["source_integrity"]["status"], "pass")
        self.assertEqual(
            sorted(row["name"] for row in plan["other_proficiencies"] if row["prof_type"] == "LANGUAGE"),
            sorted(["거인어", "공용어", "용언", "텔레파시"]),
        )
        attrs = plan_attributes(plan)
        self.assertEqual(attrs["simpleproficencies"]["current"], "complex")
        for row in plan["other_proficiencies"]:
            row_id = row["row_id"]
            self.assertIn(f"repeating_proficiencies_{row_id}_prof_type", attrs)
            self.assertIn(f"repeating_proficiencies_{row_id}_name", attrs)

    def test_partial_translated_language_list_falls_back_without_omission(self):
        payload = self._payload()
        payload["translated"]["languages"] = ["거인어", "공용어", "용언"]
        self.assertEqual(
            _translated_languages(payload),
            ["Giant", "Common", "Draconic", "Telepathy"],
        )

    def test_blank_translation_slot_uses_original_language(self):
        payload = self._payload()
        payload["translated"]["languages"] = ["거인어", "", "용언", "텔레파시"]
        self.assertEqual(
            _translated_languages(payload),
            ["거인어", "Common", "용언", "텔레파시"],
        )


if __name__ == "__main__":
    unittest.main()
