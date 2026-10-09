# -*- coding: utf-8 -*-
import unittest

from sheet_mover.runtime_integrity_v261 import (
    sanitize_raw_ability_choices,
    selected_feat_replacement_components,
)


class RuntimeIntegrityV261Tests(unittest.TestCase):
    def _raw(self):
        return {
            "stats": [
                {"id": 1, "value": 14},
                {"id": 2, "value": 8},
                {"id": 3, "value": 15},
                {"id": 4, "value": 8},
                {"id": 5, "value": 10},
                {"id": 6, "value": 15},
            ],
            "feats": [
                {
                    "definition": {
                        "id": 39,
                        "name": "Resilient",
                    }
                }
            ],
            "choices": {
                "class": [
                    {
                        "componentId": 268,
                        "id": "1-268",
                        "parentChoiceId": None,
                        "type": 1,
                        "optionValue": 696,
                        "optionIds": [695, 696],
                    },
                    {
                        "componentId": 268,
                        "id": "6-268",
                        "parentChoiceId": "1-268",
                        "type": 6,
                        "optionValue": 39,
                        "label": "Choose a Feat",
                    },
                    {
                        "componentId": 275,
                        "id": "2-1731",
                        "parentChoiceId": "1-275",
                        "type": 2,
                        "optionValue": 3525,
                        "label": "Choose an Ability Score",
                    },
                ]
            },
            "modifiers": {
                "class": [
                    {
                        "id": "1729",
                        "type": "bonus",
                        "subType": "choose-an-ability-score",
                        "value": 1,
                        "componentId": 268,
                        "isGranted": True,
                    },
                    {
                        "id": "1821",
                        "type": "bonus",
                        "subType": "choose-an-ability-score",
                        "value": 1,
                        "componentId": 268,
                        "isGranted": True,
                    },
                    {
                        "id": "1731",
                        "type": "bonus",
                        "subType": "charisma-score",
                        "value": 1,
                        "entityId": 6,
                        "componentId": 275,
                        "isGranted": False,
                    },
                ],
                "feat": [
                    {
                        "id": "43691615",
                        "type": "bonus",
                        "subType": "constitution-score",
                        "value": 1,
                        "entityId": 3,
                        "componentId": 39,
                        "isGranted": True,
                    }
                ],
            },
        }

    def test_feat_branch_identifies_replaced_asi_component(self):
        components, selections = (
            selected_feat_replacement_components(
                self._raw()
            )
        )
        self.assertEqual(components, {"268"})
        self.assertEqual(selections[0]["feat_id"], "39")

    def test_only_stale_generic_asi_modifiers_are_removed(self):
        raw, report = sanitize_raw_ability_choices(
            self._raw()
        )
        class_mods = raw["modifiers"]["class"]
        ids = [row["id"] for row in class_mods]

        self.assertEqual(report["removed_count"], 2)
        self.assertNotIn("1729", ids)
        self.assertNotIn("1821", ids)

        # A real selected ASI modifier on a different component must survive
        # even when DDB carries isGranted=False; its choice row is the source
        # of truth for that selected slot.
        self.assertIn("1731", ids)

        # The selected feat's own ability bonus must also survive.
        feat_ids = [
            row["id"]
            for row in raw["modifiers"]["feat"]
        ]
        self.assertIn("43691615", feat_ids)

    def test_item_set_score_data_is_not_touched(self):
        raw = self._raw()
        raw["modifiers"]["item"] = [{
            "id": "409",
            "type": "set",
            "subType": "strength-score",
            "value": 25,
            "fixedValue": 25,
            "componentId": 4828,
            "isGranted": True,
        }]
        cleaned, report = sanitize_raw_ability_choices(
            raw
        )
        self.assertEqual(
            cleaned["modifiers"]["item"][0]["value"],
            25,
        )
        self.assertEqual(report["removed_count"], 2)


if __name__ == "__main__":
    unittest.main()
