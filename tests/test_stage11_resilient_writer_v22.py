import unittest

from sheet_mover.roll20_proficiencies import (
    STAGE11_NO_WAIT_ATTR_SCRIPT,
    WRITE_BATCH_SIZE,
    _attribute_batches,
    build_proficiency_plan,
)


class Stage11ResilientWriterTests(unittest.TestCase):
    def test_attribute_batches_are_bounded(self):
        attrs = {f"field_{i}": {"current": str(i), "max": ""} for i in range(35)}
        self.assertEqual([len(batch) for batch in _attribute_batches(attrs)], [16, 16, 3])
        self.assertEqual(WRITE_BATCH_SIZE, 16)

    def test_repair_writer_does_not_wait_for_backbone_callback(self):
        self.assertIn("{wait:false}", STAGE11_NO_WAIT_ATTR_SCRIPT)

    def test_nonstarting_core_class_saves_are_not_added(self):
        payload = {
            "original": {
                "classes": [
                    {"name": "Sorcerer", "original_name": "Sorcerer", "is_starting_class": True},
                    {"name": "Bard", "original_name": "Bard", "is_starting_class": False},
                ],
                "features": [
                    {"source_id": "sorc-core", "kind": "class_feature", "original_name": "Core Sorcerer Traits"},
                    {"source_id": "bard-core", "kind": "class_feature", "original_name": "Core Bard Traits"},
                ],
                "calculation_inputs": {
                    "class_feature_levels": [
                        {"feature_id": "sorc-core", "class_name": "Sorcerer"},
                        {"feature_id": "bard-core", "class_name": "Bard"},
                    ]
                },
                "proficiency_entries": [
                    {"type": "proficiency", "subType": "constitution-saving-throws", "source_group": "class", "componentId": "sorc-core"},
                    {"type": "proficiency", "subType": "charisma-saving-throws", "source_group": "class", "componentId": "sorc-core"},
                    {"type": "proficiency", "subType": "dexterity-saving-throws", "source_group": "class", "componentId": "bard-core"},
                    {"type": "proficiency", "subType": "charisma-saving-throws", "source_group": "class", "componentId": "bard-core"},
                ],
                "saving_throw_proficiencies": [
                    "Constitution Saving Throws", "Charisma Saving Throws", "Dexterity Saving Throws"
                ],
                "proficiencies": [],
                "languages": [],
            },
            "translated": {"proficiencies": [], "languages": []},
            "roll20_payload": {
                "source_character_id": "153714540",
                "character": {
                    "name": "견본2",
                    "ability_scores": {
                        "strength": 9, "dexterity": 14, "constitution": 16,
                        "intelligence": 8, "wisdom": 14, "charisma": 15,
                    },
                    "proficiency_bonus": 3,
                },
            },
        }

        plan = build_proficiency_plan(payload)
        saves = {row["ability"]: row for row in plan["saves"]}
        self.assertTrue(saves["constitution"]["proficient"])
        self.assertTrue(saves["charisma"]["proficient"])
        self.assertFalse(saves["dexterity"]["proficient"])


if __name__ == "__main__":
    unittest.main()
