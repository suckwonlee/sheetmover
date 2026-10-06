import unittest

from sheet_mover.roll20_spells import (
    ROLLCONTENT,
    SPELL_FIELDS,
    build_spell_plan,
    format_casting_time,
    format_duration,
    format_range,
    map_spell_row,
    plan_spell_attributes,
    slot_attributes,
    spell_attribute_name,
    spell_row_id,
    spell_section,
)


class Stage7SpellTests(unittest.TestCase):
    def _character(self):
        return {
            "name": "견본 캐릭터",
            "spellcasting": {
                "class_rules_source": [
                    {
                        "class_name": "Fighter",
                        "subclass_name": "Eldritch Knight",
                        "spell_rules": {
                            "levelSpellKnownMaxes": [0, 0, 0, 3],
                            "levelPreparedSpellMaxes": [],
                        },
                        "slots_at_level": [
                            {"level": 1, "available": 2},
                            {"level": 2, "available": 0},
                            {"level": 3, "available": 0},
                            {"level": 4, "available": 0},
                            {"level": 5, "available": 0},
                            {"level": 6, "available": 0},
                            {"level": 7, "available": 0},
                            {"level": 8, "available": 0},
                            {"level": 9, "available": 0},
                        ],
                    }
                ],
                "spell_slots_source": [
                    {"level": 1, "used": 0, "available": 0},
                ],
            },
        }

    def _spells(self):
        return [
            {
                "source_key": "spell:21173511",
                "source_id": "21173511",
                "definition_id": "2410",
                "name": "부밍 블레이드 (Booming Blade)",
                "original_name": "Booming Blade",
                "description": "<p>무기를 휘둘러 공격합니다.</p>",
                "level": 0,
                "prepared": False,
                "always_prepared": False,
                "uses_spell_slot": False,
                "casting_time": {"activationTime": 1, "activationType": 1},
                "range": {
                    "origin": "Self",
                    "rangeValue": None,
                    "aoeType": "Sphere",
                    "aoeValue": 5,
                },
                "duration": {
                    "durationInterval": 1,
                    "durationUnit": "Round",
                    "durationType": "Time",
                },
                "components": [2, 3],
                "components_description": "최소 1 sp 가치의 근접 무기",
                "school": "Evocation",
                "ritual": False,
                "concentration": False,
                "counts_as_known_spell": True,
            },
            {
                "source_key": "spell:21170946",
                "source_id": "21170946",
                "definition_id": "2618877",
                "name": "사역마 찾기 (Find Familiar)",
                "original_name": "Find Familiar",
                "description": "<p>사역마를 소환합니다.</p>",
                "level": 1,
                "prepared": False,
                "always_prepared": False,
                "uses_spell_slot": True,
                "casting_time": {"activationTime": 1, "activationType": 7},
                "range": {
                    "origin": "Ranged",
                    "rangeValue": 10,
                    "aoeType": None,
                    "aoeValue": None,
                },
                "duration": {
                    "durationInterval": 0,
                    "durationUnit": None,
                    "durationType": "Instantaneous",
                },
                "components": [1, 2, 3],
                "components_description": "향 10+ GP",
                "school": "Conjuration",
                "ritual": True,
                "concentration": False,
                "counts_as_known_spell": True,
            },
            {
                "source_key": "spell:sleep",
                "name": "수면 (Sleep)",
                "original_name": "Sleep",
                "description": "<p>잠들게 합니다.</p>",
                "level": 1,
                "prepared": False,
                "always_prepared": False,
                "uses_spell_slot": True,
                "casting_time": {"activationTime": 1, "activationType": 1},
                "range": {
                    "origin": "Ranged",
                    "rangeValue": 60,
                    "aoeType": "Sphere",
                    "aoeValue": 5,
                },
                "duration": {
                    "durationInterval": 1,
                    "durationUnit": "Minute",
                    "durationType": "Concentration",
                },
                "components": [1, 2, 3],
                "components_description": "모래 한 꼬집",
                "school": "Enchantment",
                "ritual": False,
                "concentration": True,
                "counts_as_known_spell": True,
            },
        ]

    def _payload(self):
        return {
            "roll20_payload": {
                "source_character_id": "170892133",
                "character": self._character(),
                "spells": self._spells(),
            }
        }

    def test_row_id_stable_and_safe(self):
        first = spell_row_id("spell:21173511")
        second = spell_row_id("spell:21173511")
        self.assertEqual(first, second)
        self.assertEqual(len(first), 20)
        self.assertNotIn("_", first)

    def test_section_mapping(self):
        self.assertEqual(spell_section(0), "spell-cantrip")
        self.assertEqual(spell_section(1), "spell-1")
        self.assertEqual(spell_section(9), "spell-9")
        with self.assertRaises(ValueError):
            spell_section(10)

    def test_booming_blade_display_mapping(self):
        row = map_spell_row(self._spells()[0], known_spell_mode=True)
        self.assertEqual(row["section"], "spell-cantrip")
        self.assertEqual(row["fields"]["spellname"], "부밍 블레이드 (Booming Blade)")
        self.assertEqual(row["fields"]["spellschool"], "evocation")
        self.assertEqual(row["fields"]["spellcastingtime"], "Action")
        self.assertEqual(row["fields"]["spellrange"], "Self (5 ft. Sphere)")
        self.assertEqual(row["fields"]["spelltarget"], "5 ft. Sphere")
        self.assertEqual(row["fields"]["spellcomp_v"], "0")
        self.assertEqual(row["fields"]["spellcomp_s"], "{{s=1}}")
        self.assertEqual(row["fields"]["spellcomp_m"], "{{m=1}}")
        self.assertEqual(row["fields"]["spellprepared"], "0")

    def test_find_familiar_ritual_and_hour(self):
        row = map_spell_row(self._spells()[1], known_spell_mode=True)
        self.assertEqual(row["fields"]["spellcastingtime"], "1 Hour")
        self.assertEqual(row["fields"]["spellrange"], "10 ft.")
        self.assertEqual(row["fields"]["spellritual"], "{{ritual=1}}")
        self.assertEqual(row["fields"]["spellprepared"], "1")
        self.assertEqual(row["fields"]["spellcomp_materials"], "향 10+ GP")

    def test_concentration_duration(self):
        row = map_spell_row(self._spells()[2], known_spell_mode=True)
        self.assertEqual(row["fields"]["spellconcentration"], "{{concentration=1}}")
        self.assertEqual(row["fields"]["spellduration"], "Up to 1 Minute")
        self.assertEqual(row["fields"]["spellrange"], "60 ft. (5 ft. Sphere)")

    def test_stage7_keeps_spellcard_and_no_attack_link(self):
        row = map_spell_row(self._spells()[2], known_spell_mode=True)
        self.assertEqual(row["fields"]["spelloutput"], "SPELLCARD")
        self.assertEqual(row["fields"]["spellattack"], "None")
        self.assertEqual(row["fields"]["spellattackid"], "")
        self.assertEqual(row["fields"]["spelldamage"], "")
        self.assertEqual(row["fields"]["spellsave"], "")
        self.assertEqual(row["fields"]["rollcontent"], ROLLCONTENT)

    def test_slots_total_and_remaining(self):
        attrs, source = slot_attributes(self._character())
        self.assertEqual(source, "class_rules_source")
        self.assertEqual(attrs["lvl1_slots_total"]["current"], "2")
        self.assertEqual(attrs["lvl1_slots_expended"]["current"], "2")
        self.assertEqual(attrs["lvl2_slots_total"]["current"], "0")
        self.assertEqual(attrs["lvl2_slots_expended"]["current"], "0")

    def test_used_slots_are_converted_to_remaining(self):
        character = self._character()
        character["spellcasting"]["spell_slots_source"][0]["used"] = 1
        attrs, _ = slot_attributes(character)
        self.assertEqual(attrs["lvl1_slots_total"]["current"], "2")
        self.assertEqual(attrs["lvl1_slots_expended"]["current"], "1")

    def test_plan_preserves_manual_rows_and_has_stable_ids(self):
        plan = build_spell_plan(self._payload())
        self.assertEqual(plan["row_count"], 3)
        self.assertTrue(plan["policy"]["preserve_unmanaged_rows"])
        self.assertFalse(plan["policy"]["delete_existing_rows"])
        self.assertFalse(plan["policy"]["create_attacks"])
        self.assertTrue(plan["known_spell_mode"])

    def test_attribute_names_match_roll20_sections(self):
        plan = build_spell_plan(self._payload())
        attrs = plan_spell_attributes(plan)
        self.assertEqual(len(attrs), len(SPELL_FIELDS) * 3)
        for name in attrs:
            self.assertTrue(name.startswith("repeating_spell-"))
            self.assertNotIn("repeating_attack", name)

    def test_unknown_field_is_rejected(self):
        row_id = spell_row_id("spell:1")
        with self.assertRaises(ValueError):
            spell_attribute_name("spell-1", row_id, "attackid")

    def test_duplicate_source_key_fails_closed(self):
        payload = self._payload()
        payload["roll20_payload"]["spells"][1]["source_key"] = "spell:21173511"
        with self.assertRaisesRegex(RuntimeError, "source_key 중복"):
            build_spell_plan(payload)

    def test_format_helpers(self):
        self.assertEqual(
            format_casting_time({"activationTime": 2, "activationType": 6}),
            "2 Minutes",
        )
        self.assertEqual(
            format_range({
                "origin": "Self",
                "rangeValue": 0,
                "aoeType": "Cube",
                "aoeValue": 15,
            }),
            "Self (15 ft. Cube)",
        )
        self.assertEqual(
            format_duration({
                "durationInterval": 1,
                "durationUnit": "Round",
                "durationType": "Time",
            }),
            "1 Round",
        )


if __name__ == "__main__":
    unittest.main()
