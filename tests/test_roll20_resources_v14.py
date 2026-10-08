# -*- coding: utf-8 -*-
import unittest
from sheet_mover.roll20_resources import build_resource_candidates


class ResourceDynamicUsesV14Tests(unittest.TestCase):
    def test_divine_sense_one_plus_charisma_modifier(self):
        payload = {
            "roll20_payload": {"character": {
                "proficiency_bonus": 5,
                "ability_scores": {"charisma": 22},
            }},
            "translated": {"resources": [{
                "source_id": "1026", "kind": "action:class",
                "name": "신성한 감각 (Divine Sense)",
                "original_name": "Divine Sense",
                "limited_use": {
                    "maxUses": 1, "statModifierUsesId": 6, "operator": 1,
                    "useProficiencyBonus": False,
                    "proficiencyBonusOperator": 1,
                    "numberUsed": 0, "resetType": 2,
                },
            }]},
            "original": {"resources": []},
        }
        rows = build_resource_candidates(payload)
        self.assertEqual(rows[0]["maximum"], 7)
        self.assertEqual(rows[0]["current"], 7)

    def test_missing_breath_weapon_merges_from_fresh_original(self):
        payload = {
            "roll20_payload": {"character": {
                "proficiency_bonus": 5,
                "ability_scores": {"charisma": 22},
            }},
            "translated": {"resources": [{
                "source_id": "1026", "kind": "action:class",
                "name": "신성한 감각 (Divine Sense)",
                "original_name": "Divine Sense",
                "limited_use": {
                    "maxUses": 1, "statModifierUsesId": 6, "operator": 1,
                    "numberUsed": 0, "resetType": 2,
                },
            }]},
            "original": {"resources": [
                {
                    "source_id": "1026", "kind": "action:class",
                    "name": "Divine Sense", "original_name": "Divine Sense",
                    "limited_use": {
                        "maxUses": 1, "statModifierUsesId": 6, "operator": 1,
                        "numberUsed": 0, "resetType": 2,
                    },
                },
                {
                    "source_id": "4034760", "kind": "action:race",
                    "name": "Breath Weapon (Necrotic)",
                    "original_name": "Breath Weapon (Necrotic)",
                    "limited_use": {
                        "maxUses": 0, "statModifierUsesId": None,
                        "useProficiencyBonus": True,
                        "proficiencyBonusOperator": 1,
                        "numberUsed": 0, "resetType": 2,
                    },
                },
            ]},
        }
        rows = build_resource_candidates(payload)
        by_name = {row["original_name"]: row for row in rows}
        self.assertEqual(by_name["Divine Sense"]["maximum"], 7)
        self.assertEqual(by_name["Breath Weapon (Necrotic)"]["maximum"], 5)
        self.assertEqual(by_name["Breath Weapon (Necrotic)"]["current"], 5)

    def test_unknown_operator_fails_closed(self):
        payload = {
            "roll20_payload": {"character": {
                "proficiency_bonus": 5,
                "ability_scores": {"charisma": 22},
            }},
            "translated": {"resources": [{
                "source_id": "x", "kind": "action:class",
                "name": "Unknown", "original_name": "Unknown",
                "limited_use": {
                    "maxUses": 1, "statModifierUsesId": 6,
                    "operator": 999, "numberUsed": 0, "resetType": 2,
                },
            }]},
            "original": {"resources": []},
        }
        self.assertEqual(build_resource_candidates(payload), [])


if __name__ == "__main__":
    unittest.main()
