import unittest

from sheet_mover.roll20_payload import build_roll20_payload


class Stage3PayloadTests(unittest.TestCase):
    def test_character_and_repeating_rows_are_built(self):
        translated = {
            "source_id": "123",
            "name": "견본",
            "total_level": 3,
            "ability_scores": {"strength": 17},
            "proficiency_bonus": 2,
            "armor_class": 10,
            "hp": 37,
            "max_hp": 37,
            "equipment": [{
                "source_id": "e1", "definition_id": "d1", "kind": "equipment",
                "name": "체인 메일", "original_name": "Chain Mail", "quantity": 1,
            }],
            "spells": [{
                "source_id": "s1", "kind": "spell", "name": "수면", "original_name": "Sleep",
                "level": 1, "description": "설명",
            }],
            "features": [{
                "source_id": "f1", "kind": "class_feature", "name": "행동 쇄도",
                "original_name": "Action Surge", "description": "설명",
            }],
            "actions": [{
                "source_id": "a1", "kind": "action:class", "name": "행동 쇄도",
                "original_name": "Action Surge", "description": "설명",
            }],
        }
        payload = build_roll20_payload(translated)
        self.assertEqual(payload["source_character_id"], "123")
        self.assertEqual(payload["character"]["total_level"], 3)
        self.assertEqual(payload["equipment"][0]["source_key"], "equipment:e1")
        self.assertEqual(payload["spells"][0]["source_key"], "spell:s1")
        self.assertEqual(payload["features"][0]["source_key"], "feature:f1")
        self.assertEqual(payload["actions"][0]["source_key"], "action:a1")
        self.assertEqual(payload["counts"], {"equipment": 1, "spells": 1, "features": 1, "actions": 1})

    def test_duplicate_fallback_keys_are_still_unique(self):
        translated = {
            "name": "Hero",
            "features": [
                {"name": "같은 이름", "original_name": "Same"},
                {"name": "같은 이름", "original_name": "Same"},
            ],
        }
        payload = build_roll20_payload(translated)
        keys = [row["source_key"] for row in payload["features"]]
        self.assertEqual(len(keys), len(set(keys)))

    def test_future_stage_collections_are_not_pretended_complete(self):
        payload = build_roll20_payload({"name": "Hero", "attacks": [{"name": "Sword"}]})
        self.assertNotIn("attacks", payload)
        self.assertIn("attacks", payload["deferred_to_later_stages"])


if __name__ == "__main__":
    unittest.main()
