import tempfile
import unittest
from pathlib import Path

from sheet_mover.roll20_resources import (
    build_resource_candidates,
    place_resources,
    resource_row_id,
)


class Stage12ResourceTests(unittest.TestCase):
    def _payload(self):
        return {
            "translated": {
                "resources": [
                    {
                        "source_id": "9414019",
                        "kind": "action:class",
                        "name": "재기의 바람 (Second Wind)",
                        "original_name": "Second Wind",
                        "limited_use": {
                            "resetType": 2,
                            "numberUsed": 0,
                            "maxUses": 2,
                            "useProficiencyBonus": False,
                        },
                    },
                    {
                        "source_id": "9414020",
                        "kind": "action:class",
                        "name": "행동 쇄도 (Action Surge)",
                        "original_name": "Action Surge",
                        "limited_use": {
                            "resetType": 1,
                            "numberUsed": 0,
                            "maxUses": 1,
                            "useProficiencyBonus": False,
                        },
                    },
                ],
            },
            "roll20_payload": {
                "source_character_id": "170892133",
                "character": {
                    "name": "견본 캐릭터",
                    "proficiency_bonus": 2,
                },
            },
        }

    @staticmethod
    def _snapshot(values=None, max_values=None):
        values = values or {}
        max_values = max_values or {}
        attrs = {}
        for name in set(values) | set(max_values):
            attrs[name] = [{
                "id": f"id-{name}",
                "current": str(values.get(name, "")),
                "max": str(max_values.get(name, "")),
            }]
        return {"attributes": attrs}

    def test_sample_has_only_two_independent_resources(self):
        rows = build_resource_candidates(self._payload())
        self.assertEqual(len(rows), 2)
        self.assertEqual(
            [row["original_name"] for row in rows],
            ["Second Wind", "Action Surge"],
        )

    def test_sample_remaining_uses(self):
        rows = build_resource_candidates(self._payload())
        self.assertEqual(rows[0]["current"], 2)
        self.assertEqual(rows[0]["maximum"], 2)
        self.assertEqual(rows[1]["current"], 1)
        self.assertEqual(rows[1]["maximum"], 1)

    def test_number_used_becomes_remaining(self):
        payload = self._payload()
        payload["translated"]["resources"][0]["limited_use"]["numberUsed"] = 1
        rows = build_resource_candidates(payload)
        self.assertEqual(rows[0]["current"], 1)
        self.assertEqual(rows[0]["maximum"], 2)

    def test_blank_fixed_slots_receive_first_two_resources(self):
        rows = build_resource_candidates(self._payload())
        placement = place_resources(
            rows,
            self._snapshot(),
            {},
        )
        self.assertEqual(
            placement["fixed"]["class_resource"]["original_name"],
            "Second Wind",
        )
        self.assertEqual(
            placement["fixed"]["other_resource"]["original_name"],
            "Action Surge",
        )
        self.assertEqual(placement["repeating"], [])

    def test_manual_fixed_resource_is_preserved(self):
        rows = build_resource_candidates(self._payload())
        fixed = self._snapshot(
            {
                "class_resource": "3",
                "class_resource_name": "수동 자원",
            },
            {"class_resource": "3"},
        )
        placement = place_resources(rows, fixed, {})

        self.assertNotIn("class_resource", placement["fixed"])
        self.assertEqual(
            placement["fixed"]["other_resource"]["original_name"],
            "Second Wind",
        )
        self.assertEqual(
            [row["original_name"] for row in placement["repeating"]],
            ["Action Surge"],
        )

    def test_previous_fixed_ownership_is_stable(self):
        rows = build_resource_candidates(self._payload())
        second_key = rows[1]["source_key"]
        previous = {
            "fixed_slot_assignments": {
                "class_resource": second_key,
            }
        }
        fixed = self._snapshot(
            {
                "class_resource": "1",
                "class_resource_name": "행동 쇄도 (Action Surge)",
            },
            {"class_resource": "1"},
        )
        placement = place_resources(rows, fixed, previous)

        self.assertEqual(
            placement["fixed"]["class_resource"]["source_key"],
            second_key,
        )

    def test_removed_previously_owned_fixed_slot_is_cleared(self):
        rows = build_resource_candidates(self._payload())[:1]
        previous = {
            "fixed_slot_assignments": {
                "class_resource": rows[0]["source_key"],
                "other_resource": "resource:action:class:removed",
            }
        }
        fixed = self._snapshot(
            {
                "class_resource": "2",
                "class_resource_name": "재기의 바람 (Second Wind)",
                "other_resource": "1",
                "other_resource_name": "예전 자원",
            },
            {
                "class_resource": "2",
                "other_resource": "1",
            },
        )
        placement = place_resources(rows, fixed, previous)
        self.assertIn("other_resource", placement["clear_fixed"])

    def test_more_than_two_resources_overflow_to_repeating(self):
        payload = self._payload()
        payload["translated"]["resources"].append({
            "source_id": "third",
            "kind": "action:class",
            "name": "세 번째 자원",
            "original_name": "Third Resource",
            "limited_use": {
                "numberUsed": 0,
                "maxUses": 3,
                "useProficiencyBonus": False,
            },
        })
        rows = build_resource_candidates(payload)
        placement = place_resources(rows, self._snapshot(), {})
        self.assertEqual(len(placement["repeating"]), 1)
        self.assertEqual(
            placement["repeating"][0]["original_name"],
            "Third Resource",
        )

    def test_proficiency_bonus_fallback(self):
        payload = self._payload()
        payload["translated"]["resources"] = [{
            "source_id": "pb-resource",
            "kind": "action:class",
            "name": "숙련 보너스 자원",
            "original_name": "PB Resource",
            "limited_use": {
                "numberUsed": 1,
                "maxUses": None,
                "useProficiencyBonus": True,
            },
        }]
        rows = build_resource_candidates(payload)
        self.assertEqual(rows[0]["maximum"], 2)
        self.assertEqual(rows[0]["current"], 1)

    def test_resource_row_id_is_stable(self):
        key = "resource:action:class:9414019"
        self.assertEqual(resource_row_id(key), resource_row_id(key))
        self.assertEqual(len(resource_row_id(key)), 20)
        self.assertNotIn("_", resource_row_id(key))


    def test_fixed_resource_uses_attribute_max_property(self):
        from sheet_mover.roll20_resources import _fixed_attributes

        rows = build_resource_candidates(self._payload())
        placement = place_resources(rows, self._snapshot(), {})
        attrs = _fixed_attributes(placement)

        self.assertEqual(attrs["class_resource"]["current"], "2")
        self.assertEqual(attrs["class_resource"]["max"], "2")
        self.assertEqual(attrs["other_resource"]["current"], "1")
        self.assertEqual(attrs["other_resource"]["max"], "1")
        self.assertNotIn("class_resource_max", attrs)
        self.assertNotIn("other_resource_max", attrs)

    def test_repeating_resource_uses_attribute_max_property(self):
        from sheet_mover.roll20_resources import (
            _repeating_attributes,
            _repeating_row,
        )

        payload = self._payload()
        payload["translated"]["resources"].append({
            "source_id": "third",
            "kind": "action:class",
            "name": "세 번째 자원",
            "original_name": "Third Resource",
            "limited_use": {
                "numberUsed": 1,
                "maxUses": 3,
                "useProficiencyBonus": False,
            },
        })
        rows = build_resource_candidates(payload)
        placement = place_resources(rows, self._snapshot(), {})
        repeating = [_repeating_row(r) for r in placement["repeating"]]
        attrs = _repeating_attributes(repeating)

        base_name = (
            f"repeating_resource_{repeating[0]['row_id']}_resource_left"
        )
        self.assertEqual(attrs[base_name]["current"], "2")
        self.assertEqual(attrs[base_name]["max"], "3")
        self.assertFalse(
            any(name.endswith("_resource_left_max") for name in attrs)
        )

    def test_slot_blank_reads_base_attribute_max_property(self):
        from sheet_mover.roll20_resources import _slot_blank

        occupied = self._snapshot(
            {"class_resource": ""},
            {"class_resource": "2"},
        )
        self.assertFalse(_slot_blank(occupied, "class_resource"))

        blank = self._snapshot(
            {"class_resource": "", "class_resource_name": ""},
            {"class_resource": ""},
        )
        self.assertTrue(_slot_blank(blank, "class_resource"))


    def test_sample_rest_reset_mapping(self):
        rows = build_resource_candidates(self._payload())
        by_name = {row["original_name"]: row for row in rows}

        # Second Wind is the 2024 partial-short-rest case in this sample.
        # Legacy cannot restore only one use, so its DDB resetType is mapped
        # to the Long Rest checkbox.
        self.assertEqual(by_name["Second Wind"]["roll20_reset"], "long")

        # Action Surge fully recharges on a short rest.
        self.assertEqual(by_name["Action Surge"]["roll20_reset"], "short")

    def test_fixed_resources_write_rest_reset_attributes(self):
        from sheet_mover.roll20_resources import _fixed_attributes

        rows = build_resource_candidates(self._payload())
        placement = place_resources(rows, self._snapshot(), {})
        attrs = _fixed_attributes(placement)

        self.assertEqual(attrs["class_resource"]["current"], "2")
        self.assertEqual(attrs["class_resource"]["max"], "2")
        self.assertEqual(attrs["class_resource_reset"]["current"], "long")

        self.assertEqual(attrs["other_resource"]["current"], "1")
        self.assertEqual(attrs["other_resource"]["max"], "1")
        self.assertEqual(attrs["other_resource_reset"]["current"], "short")

    def test_repeating_resource_writes_reset_attribute(self):
        from sheet_mover.roll20_resources import (
            _repeating_attributes,
            _repeating_row,
        )

        payload = self._payload()
        payload["translated"]["resources"].append({
            "source_id": "third",
            "kind": "action:class",
            "name": "롱레 자원",
            "original_name": "Long Rest Resource",
            "limited_use": {
                "resetType": 2,
                "numberUsed": 1,
                "maxUses": 3,
                "useProficiencyBonus": False,
            },
        })

        rows = build_resource_candidates(payload)
        placement = place_resources(rows, self._snapshot(), {})
        repeating = [_repeating_row(r) for r in placement["repeating"]]
        attrs = _repeating_attributes(repeating)

        row = repeating[0]
        base_name = (
            f"repeating_resource_{row['row_id']}_resource_left"
        )
        reset_name = (
            f"repeating_resource_{row['row_id']}_resource_left_reset"
        )

        # v1.1 max-property hotfix is cumulative in v1.2.
        self.assertEqual(attrs[base_name]["current"], "2")
        self.assertEqual(attrs[base_name]["max"], "3")
        self.assertEqual(attrs[reset_name]["current"], "long")

    def test_unknown_reset_type_does_not_guess(self):
        payload = self._payload()
        payload["translated"]["resources"] = [{
            "source_id": "special",
            "kind": "action:class",
            "name": "특수 자원",
            "original_name": "Special Resource",
            "limited_use": {
                "resetType": 99,
                "numberUsed": 0,
                "maxUses": 2,
                "useProficiencyBonus": False,
            },
        }]
        rows = build_resource_candidates(payload)
        self.assertEqual(rows[0]["roll20_reset"], "")


if __name__ == "__main__":
    unittest.main()
