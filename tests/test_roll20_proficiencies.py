import unittest

from sheet_mover.roll20_proficiencies import (
    PB_CHECKED,
    QUERY_ABILITY,
    SKILL_CHECKED_VALUE,
    build_proficiency_plan,
    managed_repeating_rows,
    plan_attributes,
)


class Stage11V2ProficiencyTests(unittest.TestCase):
    def _payload(self):
        prof_names = [
            "Acrobatics",
            "Animal Handling",
            "Athletics",
            "Carpenter's Tools",
            "Constitution Saving Throws",
            "Heavy Armor",
            "Light Armor",
            "Martial Weapons",
            "Medium Armor",
            "Nature",
            "Persuasion",
            "Shields",
            "Simple Weapons",
            "Strength Saving Throws",
        ]
        translated = [
            "곡예",
            "동물 조련",
            "운동",
            "목공 도구 (Carpenter's Tools)",
            "건강 내성 굴림",
            "중갑",
            "경갑",
            "군용 무기",
            "평갑",
            "자연",
            "설득",
            "방패",
            "단순 무기",
            "근력 내성 굴림",
        ]
        entries = [
            {
                "type": "proficiency",
                "subType": "acrobatics",
                "friendlySubtypeName": "Acrobatics",
                "entityTypeId": 1958004211,
                "source_group": "race",
            },
            {
                "type": "proficiency",
                "subType": "strength-saving-throws",
                "friendlySubtypeName": "Strength Saving Throws",
                "entityTypeId": None,
                "source_group": "class",
            },
            {
                "type": "proficiency",
                "subType": "constitution-saving-throws",
                "friendlySubtypeName": "Constitution Saving Throws",
                "entityTypeId": None,
                "source_group": "class",
            },
            {
                "type": "proficiency",
                "subType": "simple-weapons",
                "friendlySubtypeName": "Simple Weapons",
                "entityTypeId": 660121713,
                "source_group": "class",
            },
            {
                "type": "proficiency",
                "subType": "martial-weapons",
                "friendlySubtypeName": "Martial Weapons",
                "entityTypeId": 660121713,
                "source_group": "class",
            },
            {
                "type": "proficiency",
                "subType": "light-armor",
                "friendlySubtypeName": "Light Armor",
                "entityTypeId": 174869515,
                "source_group": "class",
            },
            {
                "type": "proficiency",
                "subType": "medium-armor",
                "friendlySubtypeName": "Medium Armor",
                "entityTypeId": 174869515,
                "source_group": "class",
            },
            {
                "type": "proficiency",
                "subType": "heavy-armor",
                "friendlySubtypeName": "Heavy Armor",
                "entityTypeId": 174869515,
                "source_group": "class",
            },
            {
                "type": "proficiency",
                "subType": "shields",
                "friendlySubtypeName": "Shields",
                "entityTypeId": 174869515,
                "source_group": "class",
            },
            {
                "type": "proficiency",
                "subType": "carpenters-tools",
                "friendlySubtypeName": "Carpenter's Tools",
                "entityTypeId": 2103445194,
                "source_group": "background",
            },
        ]

        return {
            "original": {
                "proficiencies": prof_names,
                "languages": ["Celestial", "Common"],
                "saving_throw_proficiencies": [
                    "Strength Saving Throws",
                    "Constitution Saving Throws",
                ],
                "skill_proficiencies": [
                    {
                        "subtype": "acrobatics",
                        "type": "proficiency",
                        "source_group": "race",
                    },
                    {
                        "subtype": "athletics",
                        "type": "proficiency",
                        "source_group": "class",
                    },
                    {
                        "subtype": "persuasion",
                        "type": "proficiency",
                        "source_group": "class",
                    },
                    {
                        "subtype": "animal-handling",
                        "type": "proficiency",
                        "source_group": "background",
                    },
                    {
                        "subtype": "nature",
                        "type": "proficiency",
                        "source_group": "background",
                    },
                ],
                "proficiency_entries": entries,
            },
            "translated": {
                "proficiencies": translated,
                "languages": ["천상어", "공용어"],
            },
            "roll20_payload": {
                "source_character_id": "170892133",
                "character": {
                    "name": "견본 캐릭터",
                    "ability_scores": {
                        "strength": 17,
                        "dexterity": 11,
                        "constitution": 16,
                        "intelligence": 10,
                        "wisdom": 12,
                        "charisma": 8,
                    },
                    "proficiency_bonus": 2,
                },
            },
        }

    def test_sample_counts_now_include_all_requested_proficiencies(self):
        plan = build_proficiency_plan(self._payload())
        self.assertEqual(plan["skill_proficiency_count"], 5)
        self.assertEqual(plan["save_proficiency_count"], 2)
        self.assertEqual(plan["tool_proficiency_count"], 1)
        self.assertEqual(plan["language_count"], 2)
        self.assertEqual(plan["weapon_proficiency_count"], 2)
        self.assertEqual(plan["armor_proficiency_count"], 4)
        self.assertEqual(plan["other_proficiency_count"], 0)

    def test_tool_goes_to_repeating_tool_with_query_attribute(self):
        plan = build_proficiency_plan(self._payload())
        self.assertEqual(len(plan["tools"]), 1)
        tool = plan["tools"][0]
        self.assertEqual(tool["name"], "목공 도구 (Carpenter's Tools)")
        self.assertEqual(tool["fields"]["toolbonus_base"], "(@{pb})")
        self.assertEqual(tool["fields"]["toolattr_base"], QUERY_ABILITY)
        self.assertEqual(tool["fields"]["toolattr"], "QUERY")
        self.assertEqual(tool["fields"]["toolbonus_display"], "?")
        self.assertIn("+2", tool["fields"]["toolbonus"])

    def test_languages_use_translated_names(self):
        plan = build_proficiency_plan(self._payload())
        langs = [
            row["name"] for row in plan["other_proficiencies"]
            if row["prof_type"] == "LANGUAGE"
        ]
        self.assertEqual(langs, ["공용어", "천상어"])

    def test_weapon_and_armor_rows_use_official_roll20_types(self):
        plan = build_proficiency_plan(self._payload())
        weapons = {
            row["name"] for row in plan["other_proficiencies"]
            if row["prof_type"] == "WEAPON"
        }
        armor = {
            row["name"] for row in plan["other_proficiencies"]
            if row["prof_type"] == "ARMOR"
        }
        self.assertEqual(weapons, {"군용 무기", "단순 무기"})
        self.assertEqual(armor, {"경갑", "평갑", "중갑", "방패"})

    def test_core_skill_and_save_behavior_is_preserved(self):
        plan = build_proficiency_plan(self._payload())
        skills = {row["skill"]: row for row in plan["skills"]}
        saves = {row["ability"]: row for row in plan["saves"]}

        self.assertEqual(
            skills["athletics"]["attributes"]["athletics_prof"],
            SKILL_CHECKED_VALUE["athletics"],
        )
        self.assertEqual(skills["athletics"]["bonus"], 5)
        self.assertEqual(
            saves["strength"]["attributes"]["strength_save_prof"],
            PB_CHECKED,
        )
        self.assertEqual(saves["strength"]["bonus"], 5)

    def test_expertise_still_works(self):
        payload = self._payload()
        payload["original"]["proficiency_entries"].append({
            "type": "expertise",
            "subType": "athletics",
            "friendlySubtypeName": "Athletics",
            "entityTypeId": 1958004211,
        })
        plan = build_proficiency_plan(payload)
        athletics = next(
            row for row in plan["skills"]
            if row["skill"] == "athletics"
        )
        self.assertTrue(athletics["expertise"])
        self.assertEqual(athletics["bonus"], 7)

    def test_tool_expertise_still_works(self):
        payload = self._payload()
        payload["original"]["proficiency_entries"].append({
            "type": "expertise",
            "subType": "carpenters-tools",
            "friendlySubtypeName": "Carpenter's Tools",
            "entityTypeId": 2103445194,
        })
        plan = build_proficiency_plan(payload)
        tool = plan["tools"][0]
        self.assertTrue(tool["expertise"])
        self.assertEqual(tool["fields"]["toolbonus_base"], "(@{pb}*2)")
        self.assertIn("+4", tool["fields"]["toolbonus"])

    def test_managed_attributes_include_both_repeating_sections(self):
        plan = build_proficiency_plan(self._payload())
        attrs = plan_attributes(plan)
        self.assertEqual(attrs["simpleproficencies"]["current"], "complex")
        self.assertTrue(
            any(name.startswith("repeating_tool_-SM") for name in attrs)
        )
        self.assertTrue(
            any(name.startswith("repeating_proficiencies_-SM") for name in attrs)
        )

        rows = managed_repeating_rows(plan)
        self.assertEqual(len(rows["tool"]), 1)
        self.assertEqual(len(rows["proficiencies"]), 8)

    def test_duplicate_normal_skill_does_not_fake_expertise(self):
        payload = self._payload()
        payload["original"]["proficiency_entries"].extend([
            {
                "type": "proficiency",
                "subType": "athletics",
                "friendlySubtypeName": "Athletics",
            },
            {
                "type": "proficiency",
                "subType": "athletics",
                "friendlySubtypeName": "Athletics",
            },
        ])
        plan = build_proficiency_plan(payload)
        athletics = next(
            row for row in plan["skills"]
            if row["skill"] == "athletics"
        )
        self.assertFalse(athletics["expertise"])


    def test_proficiency_plan_has_explicit_grouped_display_order(self):
        plan = build_proficiency_plan(self._payload())
        rows = plan["other_proficiencies"]
        types = [row["prof_type"] for row in rows]
        self.assertEqual(
            types,
            [
                "LANGUAGE",
                "LANGUAGE",
                "WEAPON",
                "WEAPON",
                "ARMOR",
                "ARMOR",
                "ARMOR",
                "ARMOR",
            ],
        )
        self.assertEqual(
            plan["desired_proficiency_order"],
            [row["row_id"] for row in rows],
        )

    def test_reporder_builder_preserves_manual_rows_after_managed_rows(self):
        from sheet_mover.roll20_proficiencies import _build_reporders

        plan = build_proficiency_plan(self._payload())
        managed_prof = plan["desired_proficiency_order"]
        managed_tool = plan["desired_tool_order"]

        state = {
            "tool_row_ids": ["manual-tool"] + managed_tool,
            "tool_reporder": ",".join(["manual-tool"] + managed_tool),
            "proficiency_row_ids": [
                "manual-prof-a",
                *reversed(managed_prof),
                "manual-prof-b",
            ],
            "proficiency_reporder": ",".join([
                "manual-prof-b",
                *reversed(managed_prof),
                "manual-prof-a",
            ]),
        }

        reporders = _build_reporders(state, plan)

        self.assertEqual(
            reporders["_reporder_repeating_tool"].split(","),
            managed_tool + ["manual-tool"],
        )
        self.assertEqual(
            reporders["_reporder_repeating_proficiencies"].split(","),
            managed_prof + ["manual-prof-b", "manual-prof-a"],
        )


if __name__ == "__main__":
    unittest.main()
