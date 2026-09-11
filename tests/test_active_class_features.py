import unittest

from sheet_mover.source import normalize_character


class ActiveClassFeatureTests(unittest.TestCase):
    def test_future_class_features_are_not_character_features(self):
        sheet = normalize_character(
            {
                "name": "Level 3 Fighter",
                "classes": [
                    {
                        "id": 1,
                        "level": 3,
                        "definition": {
                            "id": 10,
                            "name": "Fighter",
                            "hitDice": 10,
                        },
                        "classFeatures": [
                            {
                                "definition": {
                                    "id": 101,
                                    "name": "Level One",
                                    "description": "active",
                                    "requiredLevel": 1,
                                }
                            },
                            {
                                "definition": {
                                    "id": 103,
                                    "name": "Level Three",
                                    "description": "active",
                                    "requiredLevel": 3,
                                }
                            },
                            {
                                "definition": {
                                    "id": 104,
                                    "name": "Level Four",
                                    "description": "future",
                                    "requiredLevel": 4,
                                }
                            },
                            {
                                "definition": {
                                    "id": 107,
                                    "name": "Level Seven",
                                    "description": "future",
                                    "requiredLevel": 7,
                                }
                            },
                        ],
                    }
                ],
            }
        )

        names = [
            item["name"]
            for item in sheet.features
            if item.get("kind") == "class_feature"
        ]

        self.assertEqual(names, ["Level One", "Level Three"])


if __name__ == "__main__":
    unittest.main()
