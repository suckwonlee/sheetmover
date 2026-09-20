import unittest

from sheet_mover.source import normalize_character


def _feat(feat_id, name, component_id=None, component_type_id=67468084):
    return {
        "componentId": component_id,
        "componentTypeId": component_type_id,
        "definition": {
            "id": feat_id,
            "name": name,
            "description": f"<p>{name}</p>",
        },
    }


class SheetSemanticsTests(unittest.TestCase):
    def _base(self):
        return {
            "id": 1,
            "name": "Sheet Semantics",
            "race": {
                "fullName": "Variant Human",
                "racialTraits": [
                    {
                        "definition": {
                            "id": 64,
                            "name": "Languages",
                            "description": "<p>Languages</p>",
                            "hideInSheet": False,
                            "categories": [],
                        }
                    },
                    {
                        "definition": {
                            "id": 150,
                            "name": "Age",
                            "description": "<p>Age</p>",
                            "hideInSheet": True,
                            "categories": [],
                        }
                    },
                    {
                        "definition": {
                            "id": 102,
                            "name": "Ability Score Increase",
                            "description": "<p>ASI</p>",
                            "hideInSheet": False,
                            "categories": [{"tagName": "__INITIAL_ASI"}],
                        }
                    },
                    {
                        "definition": {
                            "id": 65,
                            "name": "Skills",
                            "description": "<p>Skills</p>",
                            "hideInSheet": False,
                            "categories": [],
                        }
                    },
                    {
                        "definition": {
                            "id": 103,
                            "name": "Feat",
                            "description": "<p>Feat</p>",
                            "hideInSheet": False,
                            "categories": [],
                        }
                    },
                ],
            },
            "background": {
                "definition": {
                    "id": 406479,
                    "name": "Farmer",
                    "featureName": "Tough",
                    "featureDescription": "",
                    "grantedFeats": [
                        {
                            "id": 16315,
                            "name": "Tough",
                            "featIds": [1789206],
                        },
                        {
                            "id": 16316,
                            "name": "Ability Scores",
                            "featIds": [1789140],
                        },
                    ],
                }
            },
            "classes": [
                {
                    "id": 10,
                    "level": 3,
                    "definition": {
                        "id": 20,
                        "name": "Fighter",
                        "hitDice": 10,
                        "canCastSpells": True,
                        "spellRules": {
                            "levelSpellSlots": [
                                [0] * 9,
                                [0] * 9,
                                [0] * 9,
                                [2, 0, 0, 0, 0, 0, 0, 0, 0],
                            ]
                        },
                    },
                    "subclassDefinition": {
                        "id": 30,
                        "name": "Eldritch Knight",
                        "spellCastingAbilityId": 4,
                        "canCastSpells": True,
                    },
                    "classFeatures": [
                        {
                            "definition": {
                                "id": 101,
                                "name": "Core Fighter Traits",
                                "requiredLevel": 1,
                                "hideInSheet": False,
                            }
                        },
                        {
                            "definition": {
                                "id": 102,
                                "name": "Weapon Mastery",
                                "requiredLevel": 1,
                                "hideInSheet": False,
                                "grantedFeats": [
                                    {
                                        "id": 16340,
                                        "name": "Weapon Mastery",
                                        "featIds": [1789142],
                                    }
                                ],
                            }
                        },
                        {
                            "definition": {
                                "id": 999,
                                "name": "Future Feature",
                                "requiredLevel": 7,
                                "hideInSheet": False,
                            }
                        },
                    ],
                }
            ],
            "feats": [
                _feat(21, "Great Weapon Master", 103, 1960452172),
                _feat(1789206, "Tough", 16315),
                _feat(1789140, "Farmer Ability Score Improvements", 16316),
                _feat(1789142, "Weapon Mastery", 16340),
                _feat(2048517, "Dark Bargain", 54563),
            ],
            "choices": {
                "race": [
                    {
                        "componentId": 103,
                        "componentTypeId": 1960452172,
                        "type": 6,
                        "optionValue": 21,
                    }
                ]
            },
            "modifiers": {
                "race": [
                    {
                        "type": "proficiency",
                        "subType": "acrobatics",
                        "friendlySubtypeName": "Acrobatics",
                        "componentId": 65,
                        "isGranted": False,
                    }
                ],
                "class": [
                    {
                        "type": "proficiency",
                        "subType": "athletics",
                        "friendlySubtypeName": "Athletics",
                        "componentId": 101,
                        "isGranted": False,
                    },
                    {
                        "type": "proficiency",
                        "subType": "strength-saving-throws",
                        "friendlySubtypeName": "Strength Saving Throws",
                        "componentId": 101,
                        "isGranted": True,
                    },
                ],
                "background": [
                    {
                        "type": "proficiency",
                        "subType": "nature",
                        "friendlySubtypeName": "Nature",
                        "componentId": 406479,
                        "isGranted": False,
                    }
                ],
            },
            "classSpells": [
                {
                    "characterClassId": 10,
                    "spells": [
                        {
                            "id": 501,
                            "prepared": False,
                            "countsAsKnownSpell": True,
                            "usesSpellSlot": True,
                            "castOnlyAsRitual": False,
                            "definition": {
                                "id": 601,
                                "name": "Sleep",
                                "description": "<p>Sleep</p>",
                                "level": 1,
                            },
                        }
                    ],
                }
            ],
        }

    def test_subclass_spellcasting_ability_and_slot_rules_are_preserved(self):
        sheet = normalize_character(self._base())

        self.assertEqual(sheet.classes[0]["spellcasting_ability_id"], 4)
        self.assertEqual(
            sheet.classes[0]["spellcasting_ability_name"],
            "intelligence",
        )
        self.assertEqual(
            sheet.spellcasting["class_abilities"][0]["ability_id"],
            4,
        )
        self.assertEqual(
            sheet.spellcasting["class_rules_source"][0]["slots_at_level"][0],
            {"level": 1, "available": 2},
        )

    def test_real_skill_subtypes_are_detected_without_skill_suffix(self):
        sheet = normalize_character(self._base())

        self.assertEqual(
            [item["name"] for item in sheet.skill_proficiencies],
            ["Acrobatics", "Athletics", "Nature"],
        )
        self.assertEqual(
            sheet.saving_throw_proficiencies,
            ["Strength Saving Throws"],
        )

    def test_sheet_visibility_filters_hidden_and_legacy_initial_asi(self):
        sheet = normalize_character(self._base())

        race_names = [
            item["original_name"]
            for item in sheet.features
            if item["kind"] == "racial_trait"
        ]
        self.assertEqual(race_names, ["Languages", "Skills", "Feat"])
        self.assertNotIn("Age", race_names)
        self.assertNotIn("Ability Score Increase", race_names)

    def test_only_selected_or_granted_feats_are_exposed(self):
        sheet = normalize_character(self._base())

        feat_names = [
            item["original_name"]
            for item in sheet.features
            if item["kind"] == "feat"
        ]
        self.assertEqual(
            feat_names,
            [
                "Great Weapon Master",
                "Tough",
                "Farmer Ability Score Improvements",
                "Weapon Mastery",
            ],
        )
        self.assertNotIn("Dark Bargain", feat_names)

    def test_background_granted_feat_is_not_duplicated_as_background_feature(self):
        sheet = normalize_character(self._base())

        self.assertFalse(
            any(
                item["kind"] == "background"
                and item["original_name"] == "Tough"
                for item in sheet.features
            )
        )
        self.assertEqual(
            sum(
                item["original_name"] == "Tough"
                for item in sheet.features
            ),
            1,
        )

    def test_spell_selection_metadata_is_preserved(self):
        sheet = normalize_character(self._base())

        sleep = next(item for item in sheet.spells if item["name"] == "Sleep")
        self.assertIs(sleep["counts_as_known_spell"], True)
        self.assertIs(sleep["prepared"], False)
        self.assertIs(sleep["uses_spell_slot"], True)
        self.assertIs(sleep["cast_only_as_ritual"], False)


if __name__ == "__main__":
    unittest.main()
