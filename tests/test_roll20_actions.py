import unittest

from sheet_mover.roll20_actions import (
    ACTION_FIELDS,
    DMG_FLAG,
    action_attribute_name,
    action_row_id,
    build_action_plan,
    format_activation,
    format_range,
    map_action_row,
    plan_action_attributes,
)


class Stage9ActionTests(unittest.TestCase):
    def _actions(self):
        return [
            {
                "source_key": "action:9414019",
                "source_id": "9414019",
                "definition_id": "9414019",
                "kind": "action:class",
                "name": "재기의 바람 (Second Wind)",
                "original_name": "Second Wind",
                "description": (
                    "<p>추가 행동으로 HP를 회복합니다.</p>"
                    "<p>두 번 사용할 수 있습니다.</p>"
                ),
                "activation": {
                    "activationTime": None,
                    "activationType": 3,
                },
                "range": {
                    "range": None,
                    "longRange": None,
                    "aoeType": None,
                    "aoeSize": None,
                    "minimumRange": None,
                },
                "attack_type": None,
                "ability_modifier_stat_id": None,
                "dice": {
                    "diceCount": 1,
                    "diceValue": 10,
                    "fixedValue": 3,
                    "diceString": "1d10 + 3",
                },
                "damage_type_id": None,
                "limited_use": {"maxUses": 2, "numberUsed": 0},
            },
            {
                "source_key": "action:9414020",
                "source_id": "9414020",
                "definition_id": "9414020",
                "kind": "action:class",
                "name": "행동 쇄도 (Action Surge)",
                "original_name": "Action Surge",
                "description": "<p>[action]마법[/action] 행동을 제외한 추가 행동.</p>",
                "activation": {
                    "activationTime": None,
                    "activationType": 8,
                },
                "range": {},
                "dice": None,
                "limited_use": {"maxUses": 1},
            },
            {
                "source_key": "action:9414021",
                "source_id": "9414021",
                "definition_id": "9414021",
                "kind": "action:class",
                "name": "전술적 사고 (Tactical Mind)",
                "original_name": "Tactical Mind",
                "description": "<p>1d10을 굴려 능력 판정에 더합니다.</p>",
                "activation": {
                    "activationTime": None,
                    "activationType": 8,
                },
                "range": {},
                "dice": {
                    "diceCount": 1,
                    "diceValue": 10,
                    "fixedValue": None,
                    "diceString": "1d10",
                },
                "limited_use": None,
            },
            {
                "source_key": "action:9414385",
                "source_id": "9414385",
                "definition_id": "9414385",
                "kind": "action:class",
                "name": "전쟁의 유대: 의식적 결속 (War Bond: Ritual Bonding)",
                "original_name": "War Bond: Ritual Bonding",
                "description": "<p>무기와 결속합니다.</p>",
                "activation": {
                    "activationTime": 1,
                    "activationType": 7,
                },
                "range": {},
                "dice": None,
            },
            {
                "source_key": "action:9414386",
                "source_id": "9414386",
                "definition_id": "9414386",
                "kind": "action:class",
                "name": "전쟁의 유대: 무기 소환 (War Bond: Summon Weapon)",
                "original_name": "War Bond: Summon Weapon",
                "description": "<p>무기를 손으로 소환합니다.</p>",
                "activation": {
                    "activationTime": None,
                    "activationType": 3,
                },
                "range": {},
                "dice": None,
            },
            {
                "source_key": "action:53624",
                "source_id": "53624",
                "definition_id": "53624",
                "kind": "action:feat",
                "name": "대형 무기 달인 공격 (Great Weapon Master Attack)",
                "original_name": "Great Weapon Master Attack",
                "description": "조건 충족 시 추가 행동으로 근접 무기 공격.",
                "activation": {
                    "activationTime": None,
                    "activationType": 3,
                },
                "range": {},
                "dice": None,
            },
            {
                "source_key": "action:9414103",
                "source_id": "9414103",
                "definition_id": "9414103",
                "kind": "action:feat",
                "name": "스침 (대검) (Graze (Greatsword))",
                "original_name": "Graze (Greatsword)",
                "description": "<p>빗나갈 때 능력 수정치만큼 피해.</p>",
                "activation": {
                    "activationTime": None,
                    "activationType": 1,
                },
                "range": {},
                "dice": None,
            },
            {
                "source_key": "action:9414129",
                "source_id": "9414129",
                "definition_id": "9414129",
                "kind": "action:feat",
                "name": "넘어뜨리기 (삼지창) (Topple (Trident))",
                "original_name": "Topple (Trident)",
                "description": "<p>건강 내성 굴림을 강제합니다.</p>",
                "activation": {
                    "activationTime": None,
                    "activationType": 1,
                },
                "range": {},
                "dice": None,
            },
            {
                "source_key": "action:9414119",
                "source_id": "9414119",
                "definition_id": "9414119",
                "kind": "action:feat",
                "name": "감속 (투창) (Slow (Javelin))",
                "original_name": "Slow (Javelin)",
                "description": "<p>이동 속도를 10피트 감소시킵니다.</p>",
                "activation": {
                    "activationTime": None,
                    "activationType": 1,
                },
                "range": {},
                "dice": None,
            },
        ]

    def _payload(self):
        return {
            "roll20_payload": {
                "source_character_id": "170892133",
                "character": {"name": "견본 캐릭터"},
                "actions": self._actions(),
            }
        }

    def test_row_id_is_stable_and_safe(self):
        first = action_row_id("action:9414019")
        second = action_row_id("action:9414019")
        self.assertEqual(first, second)
        self.assertEqual(len(first), 20)
        self.assertNotIn("_", first)

    def test_plan_has_all_nine_actions(self):
        plan = build_action_plan(self._payload())
        self.assertEqual(plan["row_count"], 9)
        self.assertTrue(plan["policy"]["preserve_unmanaged_rows"])
        self.assertFalse(plan["policy"]["delete_existing_rows"])
        self.assertFalse(plan["policy"]["attack_rolls_enabled"])
        self.assertFalse(plan["policy"]["resource_links_enabled"])

    def test_all_actions_are_non_attack_rows(self):
        plan = build_action_plan(self._payload())
        for row in plan["rows"]:
            self.assertEqual(row["fields"]["atkflag"], "0")
            self.assertEqual(row["fields"]["atkattr_base"], "0")
            self.assertEqual(row["fields"]["atkprofflag"], "0")
            self.assertEqual(row["fields"]["saveflag"], "0")
            self.assertEqual(row["fields"]["spellid"], "")
            self.assertEqual(row["fields"]["itemid"], "")
            self.assertEqual(row["fields"]["options-flag"], "0")

    def test_second_wind_is_healing_not_attack(self):
        row = map_action_row(self._actions()[0])
        self.assertTrue(row["healing_roll"])
        self.assertEqual(row["fields"]["atkflag"], "0")
        self.assertEqual(row["fields"]["dmgflag"], DMG_FLAG)
        self.assertEqual(row["fields"]["dmgbase"], "1d10 + 3")
        self.assertEqual(row["fields"]["dmgtype"], "Healing")
        self.assertEqual(row["fields"]["atkdmgtype"], "1d10 + 3 Healing")
        self.assertIn("@{dmgbase}", row["fields"]["rollbase"])

    def test_tactical_mind_dice_is_not_fake_damage(self):
        row = map_action_row(self._actions()[2])
        self.assertFalse(row["healing_roll"])
        self.assertEqual(row["fields"]["dmgflag"], "0")
        self.assertEqual(row["fields"]["dmgbase"], "")
        self.assertEqual(row["fields"]["dmgtype"], "")

    def test_attack_like_feat_actions_wait_for_stage10(self):
        for index in (5, 6, 7, 8):
            row = map_action_row(self._actions()[index])
            self.assertEqual(row["fields"]["atkflag"], "0")
            self.assertEqual(row["fields"]["saveflag"], "0")

    def test_activation_labels(self):
        self.assertEqual(
            format_activation({"activationType": 1, "activationTime": None}),
            "행동",
        )
        self.assertEqual(
            format_activation({"activationType": 3, "activationTime": None}),
            "추가 행동",
        )
        self.assertEqual(
            format_activation({"activationType": 7, "activationTime": 1}),
            "1시간",
        )
        self.assertEqual(
            format_activation({"activationType": 8, "activationTime": None}),
            "특수",
        )

    def test_description_includes_activation_and_strips_dnd_tags(self):
        row = map_action_row(self._actions()[1])
        self.assertTrue(row["fields"]["atk_desc"].startswith("사용: 특수"))
        self.assertIn("마법 행동", row["fields"]["atk_desc"])
        self.assertNotIn("[action]", row["fields"]["atk_desc"])

    def test_range_formatter(self):
        self.assertEqual(
            format_range({
                "range": 30,
                "longRange": 120,
                "aoeType": None,
                "aoeSize": None,
                "minimumRange": None,
            }),
            "30/120 ft.",
        )
        self.assertEqual(
            format_range({
                "range": None,
                "longRange": None,
                "aoeType": "Cone",
                "aoeSize": 15,
                "minimumRange": None,
            }),
            "15 ft. Cone",
        )

    def test_attribute_names_use_repeating_attack(self):
        plan = build_action_plan(self._payload())
        attrs = plan_action_attributes(plan)
        self.assertEqual(len(attrs), len(ACTION_FIELDS) * 9)
        self.assertTrue(
            all(name.startswith("repeating_attack_-SM") for name in attrs)
        )
        self.assertFalse(any("resource" in name for name in attrs))

    def test_unknown_field_is_rejected(self):
        row_id = action_row_id("action:1")
        with self.assertRaises(ValueError):
            action_attribute_name(row_id, "resource")

    def test_duplicate_source_key_fails_closed(self):
        payload = self._payload()
        payload["roll20_payload"]["actions"][1]["source_key"] = "action:9414019"
        with self.assertRaisesRegex(RuntimeError, "source_key 중복"):
            build_action_plan(payload)


if __name__ == "__main__":
    unittest.main()
