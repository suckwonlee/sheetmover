import unittest

from sheet_mover.source import normalize_character


class OrphanActionFilteringTests(unittest.TestCase):
    def test_orphan_class_action_is_removed(self):
        sheet = normalize_character(
            {
                "name": "Action Filter",
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
                                    "name": "Active Feature",
                                    "requiredLevel": 1,
                                    "description": "",
                                }
                            }
                        ],
                    }
                ],
                "actions": {
                    "class": [
                        {
                            "id": "201",
                            "name": "Legitimate Action",
                            "componentId": 101,
                            "componentTypeId": 12168134,
                        },
                        {
                            "id": "202",
                            "name": "Orphan Circle Action",
                            "componentId": 999,
                            "componentTypeId": 12168134,
                        },
                    ]
                },
            }
        )

        self.assertEqual(
            [item["name"] for item in sheet.actions],
            ["Legitimate Action"],
        )
        self.assertTrue(
            any(
                "현재 활성 특성과 연결되지 않은 행동 1개" in warning
                for warning in sheet.warnings
            )
        )

    def test_active_feat_action_is_kept(self):
        sheet = normalize_character(
            {
                "name": "Feat Action",
                "feats": [
                    {
                        "definition": {
                            "id": 21,
                            "name": "Great Weapon Master",
                            "description": "",
                        }
                    }
                ],
                "actions": {
                    "feat": [
                        {
                            "id": "301",
                            "name": "Great Weapon Master Attack",
                            "componentId": 21,
                            "componentTypeId": 1088085227,
                        }
                    ]
                },
            }
        )

        self.assertEqual(
            [item["name"] for item in sheet.actions],
            ["Great Weapon Master Attack"],
        )


if __name__ == "__main__":
    unittest.main()
