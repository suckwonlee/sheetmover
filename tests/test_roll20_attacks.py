import unittest

from sheet_mover.roll20_attacks import (
    ATTACK_FLAG,
    DMG_FLAG,
    build_attack_plan,
    map_weapon_attack,
    old_stage9_action_row_id,
    plan_attack_attributes,
    weapon_attack_row_id,
)


class Stage10AWeaponAttackTests(unittest.TestCase):
    def _payload(self):
        equipment = [
            {
                "source_key": "equipment:1086102771",
                "source_id": "1086102771",
                "definition_id": "8",
                "name": "투창 (Javelin)",
                "original_name": "Javelin",
                "item_type": "Weapon",
                "description": "<p>숙련.</p><p>감속 효과.</p>",
                "damage": {"diceString": "1d6"},
                "damage_type": "Piercing",
                "range": 30,
                "properties": [
                    {"original_name": "Thrown", "name": "투척"},
                    {"original_name": "Slow", "name": "감속"},
                ],
            },
            {
                "source_key": "equipment:1086102780",
                "source_id": "1086102780",
                "definition_id": "13",
                "name": "낫 (Sickle)",
                "original_name": "Sickle",
                "item_type": "Weapon",
                "description": "<p>닉.</p>",
                "damage": {"diceString": "1d4"},
                "damage_type": "Slashing",
                "range": 5,
                "properties": [
                    {"original_name": "Light", "name": "경량"},
                    {"original_name": "Nick", "name": "닉"},
                ],
            },
            {
                "source_key": "equipment:1086102770",
                "source_id": "1086102770",
                "definition_id": "20",
                "name": "플레일 (Flail)",
                "original_name": "Flail",
                "item_type": "Weapon",
                "description": "<p>약화.</p>",
                "damage": {"diceString": "1d8"},
                "damage_type": "Bludgeoning",
                "range": 5,
                "properties": [{"original_name": "Sap", "name": "약화"}],
            },
            {
                "source_key": "equipment:1086102769",
                "source_id": "1086102769",
                "definition_id": "22",
                "name": "대검 (Greatsword)",
                "original_name": "Greatsword",
                "item_type": "Weapon",
                "description": "<p>스침.</p>",
                "damage": {"diceString": "2d6"},
                "damage_type": "Slashing",
                "range": 5,
                "properties": [
                    {"original_name": "Heavy", "name": "중량"},
                    {"original_name": "Two-Handed", "name": "양손"},
                    {"original_name": "Graze", "name": "스침"},
                ],
            },
            {
                "source_key": "equipment:armor",
                "source_id": "armor",
                "name": "체인 메일",
                "original_name": "Chain Mail",
                "item_type": "Armor",
                "damage": None,
            },
        ]

        raw_inventory = [
            {
                "id": 1086102771,
                "definition": {
                    "categoryId": 1,
                    "attackType": 1,
                    "range": 30,
                    "longRange": 120,
                    "properties": [
                        {"name": "Thrown"},
                        {"name": "Slow"},
                    ],
                    "damage": {"diceString": "1d6"},
                    "damageType": "Piercing",
                },
            },
            {
                "id": 1086102780,
                "definition": {
                    "categoryId": 1,
                    "attackType": 1,
                    "range": 5,
                    "longRange": 5,
                    "properties": [
                        {"name": "Light"},
                        {"name": "Nick"},
                    ],
                    "damage": {"diceString": "1d4"},
                    "damageType": "Slashing",
                },
            },
            {
                "id": 1086102770,
                "definition": {
                    "categoryId": 2,
                    "attackType": 1,
                    "range": 5,
                    "longRange": 5,
                    "properties": [{"name": "Sap"}],
                    "damage": {"diceString": "1d8"},
                    "damageType": "Bludgeoning",
                },
            },
            {
                "id": 1086102769,
                "definition": {
                    "categoryId": 2,
                    "attackType": 1,
                    "range": 5,
                    "longRange": 5,
                    "properties": [
                        {"name": "Heavy"},
                        {"name": "Two-Handed"},
                        {"name": "Graze"},
                    ],
                    "damage": {"diceString": "2d6"},
                    "damageType": "Slashing",
                },
            },
        ]

        actions = [
            {"source_key": "action:9414019"},
            {"source_key": "action:9414020"},
            {"source_key": "action:9414385"},
            {"source_key": "action:53624"},
        ]

        return {
            "original": {
                "proficiencies": [
                    "Simple Weapons",
                    "Martial Weapons",
                ],
            },
            "raw_source": {"inventory": raw_inventory},
            "roll20_payload": {
                "source_character_id": "170892133",
                "character": {
                    "name": "견본 캐릭터",
                    "ability_scores": {
                        "strength": 17,
                        "dexterity": 11,
                    },
                    "proficiency_bonus": 2,
                },
                "equipment": equipment,
                "actions": actions,
            },
        }

    def test_only_real_weapons_are_imported(self):
        plan = build_attack_plan(self._payload())
        self.assertEqual(plan["row_count"], 4)
        names = [row["name"] for row in plan["rows"]]
        self.assertEqual(
            names,
            [
                "투창 (Javelin)",
                "낫 (Sickle)",
                "플레일 (Flail)",
                "대검 (Greatsword)",
            ],
        )
        self.assertNotIn("전쟁의 유대", " ".join(names))
        self.assertNotIn("대형 무기 달인 공격", " ".join(names))

    def test_sample_weapon_math_is_plus_5_and_strength_damage(self):
        plan = build_attack_plan(self._payload())
        by_name = {row["name"]: row for row in plan["rows"]}

        expected = {
            "투창 (Javelin)": "1d6+3 Piercing",
            "낫 (Sickle)": "1d4+3 Slashing",
            "플레일 (Flail)": "1d8+3 Bludgeoning",
            "대검 (Greatsword)": "2d6+3 Slashing",
        }
        for name, damage in expected.items():
            row = by_name[name]
            self.assertEqual(row["ability"], "strength")
            self.assertTrue(row["proficient"])
            self.assertEqual(row["attack_bonus"], 5)
            self.assertEqual(row["damage_display"], damage)
            self.assertEqual(row["fields"]["atkbonus"], "+5")
            self.assertEqual(row["fields"]["atkflag"], ATTACK_FLAG)
            self.assertEqual(row["fields"]["dmgflag"], DMG_FLAG)

    def test_javelin_uses_source_long_range(self):
        plan = build_attack_plan(self._payload())
        row = next(r for r in plan["rows"] if "Javelin" in r["name"])
        self.assertEqual(row["range"], "5 ft. / 30/120 ft.")

    def test_weapon_mastery_text_stays_in_attack_description(self):
        plan = build_attack_plan(self._payload())
        greatsword = next(r for r in plan["rows"] if "Greatsword" in r["name"])
        self.assertIn("스침", greatsword["fields"]["atk_desc"])

    def test_stage9_rows_are_targeted_for_cleanup_only_by_deterministic_ids(self):
        plan = build_attack_plan(self._payload())
        self.assertEqual(len(plan["cleanup_stage9_row_ids"]), 4)
        self.assertEqual(
            plan["cleanup_stage9_row_ids"][0],
            old_stage9_action_row_id("action:9414019"),
        )
        weapon_ids = {row["row_id"] for row in plan["rows"]}
        self.assertTrue(
            weapon_ids.isdisjoint(set(plan["cleanup_stage9_row_ids"]))
        )

    def test_attack_row_ids_are_stable(self):
        a = weapon_attack_row_id("equipment:1086102769")
        b = weapon_attack_row_id("equipment:1086102769")
        self.assertEqual(a, b)
        self.assertEqual(len(a), 20)
        self.assertNotIn("_", a)

    def test_managed_attributes_are_repeating_attack_only(self):
        plan = build_attack_plan(self._payload())
        attrs = plan_attack_attributes(plan)
        self.assertTrue(attrs)
        self.assertTrue(
            all(name.startswith("repeating_attack_-SM") for name in attrs)
        )
        self.assertFalse(any("resource" in name for name in attrs))

    def test_unproficient_weapon_does_not_add_pb(self):
        payload = self._payload()
        payload["original"]["proficiencies"] = []
        item = payload["roll20_payload"]["equipment"][3]
        raw_def = payload["raw_source"]["inventory"][3]["definition"]
        row = map_weapon_attack(
            item,
            payload["roll20_payload"]["character"],
            raw_def,
            payload,
        )
        self.assertFalse(row["proficient"])
        self.assertEqual(row["attack_bonus"], 3)
        self.assertEqual(row["fields"]["atkprofflag"], "0")


if __name__ == "__main__":
    unittest.main()
