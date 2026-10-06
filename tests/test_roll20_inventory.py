import re
import unittest

from sheet_mover.roll20_inventory import (
    INVENTORY_FIELDS,
    build_inventory_plan,
    inventory_row_id,
    map_equipment_row,
    plan_attributes,
    row_attribute_name,
)


class Stage6InventoryTests(unittest.TestCase):
    def _payload(self):
        return {
            "roll20_payload": {
                "source_character_id": "170892133",
                "character": {"name": "견본 캐릭터"},
                "equipment": [
                    {
                        "source_key": "equipment:100",
                        "source_id": "100",
                        "definition_id": "200",
                        "name": "[item]장검[/item]",
                        "original_name": "Longsword",
                        "description": "<p>[action]공격[/action]에 사용하는 검입니다.</p>",
                        "quantity": 2,
                        "equipped": True,
                        "weight": 3,
                        "item_type": "Weapon",
                        "rarity": "Common",
                        "magic": False,
                        "attuned": False,
                        "properties": ["Versatile", "Martial"],
                        "damage": "1d8",
                        "range": "5 ft.",
                    },
                    {
                        "source_key": "equipment:101",
                        "source_id": "101",
                        "name": "배낭",
                        "original_name": "Backpack",
                        "description": "짐을 넣습니다.",
                        "quantity": 1,
                        "equipped": False,
                        "weight": 5,
                    },
                ],
            }
        }

    def test_row_id_is_stable_and_roll20_safe(self):
        first = inventory_row_id("equipment:100")
        second = inventory_row_id("equipment:100")
        other = inventory_row_id("equipment:101")
        self.assertEqual(first, second)
        self.assertNotEqual(first, other)
        self.assertEqual(len(first), 20)
        self.assertRegex(first, r"^-SM[0-9a-f]{17}$")
        self.assertNotIn("_", first)

    def test_mapping_strips_dnd_and_html(self):
        row = map_equipment_row(self._payload()["roll20_payload"]["equipment"][0])
        self.assertEqual(row["name"], "장검")
        self.assertEqual(row["fields"]["itemcontent"], "공격에 사용하는 검입니다.")
        self.assertEqual(row["fields"]["itemcount"], "2")
        self.assertEqual(row["fields"]["itemweight"], "3")
        self.assertEqual(row["fields"]["equipped"], "1")

    def test_caltrops_bundle_weight_is_converted_to_per_piece(self):
        row = map_equipment_row({
            "source_key": "equipment:caltrops",
            "name": "마름쇠 (Caltrops)",
            "original_name": "Caltrops",
            "quantity": 20,
            "weight": 2,
        })
        self.assertEqual(row["fields"]["itemcount"], "20")
        self.assertEqual(row["fields"]["itemweight"], "0.1")

    def test_oil_bundle_weight_is_converted_to_per_piece(self):
        row = map_equipment_row({
            "source_key": "equipment:oil",
            "name": "기름 (Oil)",
            "original_name": "Oil",
            "quantity": 2,
            "weight": 1,
        })
        self.assertEqual(row["fields"]["itemcount"], "2")
        self.assertEqual(row["fields"]["itemweight"], "0.5")

    def test_normal_stack_weights_are_not_divided(self):
        cases = [
            ("Javelin", 8, 2, "2"),
            ("Rations", 10, 2, "2"),
            ("Torch", 10, 1, "1"),
        ]
        for index, (name, quantity, weight, expected) in enumerate(cases):
            with self.subTest(name=name):
                row = map_equipment_row({
                    "source_key": f"equipment:normal:{index}",
                    "name": name,
                    "original_name": name,
                    "quantity": quantity,
                    "weight": weight,
                })
                self.assertEqual(row["fields"]["itemweight"], expected)

    def test_bundle_conversion_uses_catalog_bundle_size_not_owned_quantity(self):
        row = map_equipment_row({
            "source_key": "equipment:caltrops-40",
            "name": "마름쇠 (Caltrops)",
            "original_name": "Caltrops",
            "quantity": 40,
            "weight": 2,
        })
        self.assertEqual(row["fields"]["itemcount"], "40")
        self.assertEqual(row["fields"]["itemweight"], "0.1")

    def test_stage6_never_creates_attack_or_resource(self):
        row = map_equipment_row(self._payload()["roll20_payload"]["equipment"][0])
        self.assertEqual(row["fields"]["hasattack"], "0")
        self.assertEqual(row["fields"]["useasresource"], "0")
        self.assertEqual(row["fields"]["itemmodifiers"], "")
        self.assertEqual(row["fields"]["itemattackid"], "")
        self.assertEqual(row["fields"]["itemresourceid"], "")

    def test_unmanaged_rows_are_preserved_by_policy(self):
        plan = build_inventory_plan(self._payload())
        self.assertTrue(plan["policy"]["preserve_unmanaged_rows"])
        self.assertFalse(plan["policy"]["delete_existing_rows"])
        self.assertEqual(plan["row_count"], 2)

    def test_all_attribute_names_are_repeating_inventory(self):
        plan = build_inventory_plan(self._payload())
        attrs = plan_attributes(plan)
        self.assertEqual(len(attrs), 2 * len(INVENTORY_FIELDS))
        for name in attrs:
            self.assertTrue(name.startswith("repeating_inventory_-SM"))
            self.assertRegex(
                name,
                r"^repeating_inventory_-SM[0-9a-f]{17}_[a-z]+$",
            )

    def test_duplicate_source_key_fails_closed(self):
        payload = self._payload()
        payload["roll20_payload"]["equipment"][1]["source_key"] = "equipment:100"
        with self.assertRaisesRegex(RuntimeError, "source_key 중복"):
            build_inventory_plan(payload)

    def test_unknown_inventory_field_is_rejected(self):
        row_id = inventory_row_id("equipment:100")
        with self.assertRaises(ValueError):
            row_attribute_name(row_id, "attack")

    def test_display_properties_do_not_use_itemmodifiers(self):
        row = map_equipment_row(self._payload()["roll20_payload"]["equipment"][0])
        self.assertIn("Versatile", row["fields"]["itemproperties"])
        self.assertIn("Common", row["fields"]["itemproperties"])
        self.assertEqual(row["fields"]["itemmodifiers"], "")


if __name__ == "__main__":
    unittest.main()
