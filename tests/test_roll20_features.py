import unittest

from sheet_mover.roll20_features import build_feature_plan


class Stage8FeatureHotfixV15Tests(unittest.TestCase):
    def _payload(self):
        return {
            "raw_source": {
                "feats": [
                    {
                        "componentTypeId": 1960452172,
                        "componentId": 103,
                        "definition": {"id": 21, "name": "Great Weapon Master"},
                    },
                    {
                        "componentTypeId": 12168134,
                        "componentId": 10292232,
                        "definition": {"id": 1789148, "name": "Great Weapon Fighting"},
                    },
                    {
                        "componentTypeId": 67468084,
                        "componentId": 16315,
                        "definition": {"id": 1789206, "name": "Tough"},
                    },
                ],
            },
            "roll20_payload": {
                "source_character_id": "170892133",
                "character": {
                    "name": "견본 캐릭터",
                    "race": {
                        "name": "변형 인간 (Variant Human)",
                        "original_name": "Variant Human",
                    },
                    "background": {
                        "source_id": "406479",
                        "name": "농부 (Farmer)",
                        "original_name": "Farmer",
                        "feature_name": "강인함 (Tough)",
                        "original_feature_name": "Tough",
                    },
                    "classes": [
                        {
                            "name": "전사 (Fighter)",
                            "original_name": "Fighter",
                            "level": 3,
                        }
                    ],
                },
                "features": [
                    {
                        "source_key": "feature:64",
                        "source_id": "64",
                        "definition_id": "64",
                        "kind": "racial_trait",
                        "name": "언어 (Languages)",
                        "original_name": "Languages",
                        "description": "언어",
                        "required_level": None,
                    },
                    {
                        "source_key": "feature:65",
                        "source_id": "65",
                        "definition_id": "65",
                        "kind": "racial_trait",
                        "name": "기술 (Skills)",
                        "original_name": "Skills",
                        "description": "기술",
                        "required_level": None,
                    },
                    {
                        "source_key": "feature:103",
                        "source_id": "103",
                        "definition_id": "103",
                        "kind": "racial_trait",
                        "name": "특기 (Feat)",
                        "original_name": "Feat",
                        "description": "원하는 특기 하나",
                        "required_level": None,
                    },
                    {
                        "source_key": "feature:1789140",
                        "source_id": "1789140",
                        "definition_id": "1789140",
                        "kind": "feat",
                        "name": "농부 능력치 향상 (Farmer Ability Score Improvements)",
                        "original_name": "Farmer Ability Score Improvements",
                        "description": "배경 능력치 설명",
                        "required_level": None,
                    },
                    {
                        "source_key": "feature:10292232",
                        "source_id": "10292232",
                        "definition_id": "10292232",
                        "kind": "class_feature",
                        "name": "전투 스타일 (Fighting Style)",
                        "original_name": "Fighting Style",
                        "description": "전투 스타일 하나를 얻습니다.",
                        "required_level": 1,
                    },
                    {
                        "source_key": "feature:second-wind",
                        "source_id": "second-wind",
                        "definition_id": "second-wind",
                        "kind": "class_feature",
                        "name": "재기의 바람 (Second Wind)",
                        "original_name": "Second Wind",
                        "description": "재기의 바람",
                        "required_level": 1,
                    },
                    {
                        "source_key": "feature:21",
                        "source_id": "21",
                        "definition_id": "21",
                        "kind": "feat",
                        "name": "대형 무기 달인 (Great Weapon Master)",
                        "original_name": "Great Weapon Master",
                        "description": "GWM 전체 설명",
                        "required_level": None,
                    },
                    {
                        "source_key": "feature:1789148",
                        "source_id": "1789148",
                        "definition_id": "1789148",
                        "kind": "feat",
                        "name": "대형 무기 전투술 (Great Weapon Fighting)",
                        "original_name": "Great Weapon Fighting",
                        "description": "GWF 전체 설명",
                        "required_level": None,
                    },
                    {
                        "source_key": "feature:1789206",
                        "source_id": "1789206",
                        "definition_id": "1789206",
                        "kind": "feat",
                        "name": "강인함 (Tough)",
                        "original_name": "Tough",
                        "description": "Tough 전체 설명",
                        "required_level": None,
                    },
                ],
            },
        }

    def test_racial_feat_grant_has_selected_name_and_nonempty_one_line_description(self):
        plan = build_feature_plan(self._payload())
        row = next(r for r in plan["rows"] if r["source_key"] == "feature:103")
        self.assertEqual(
            row["fields"]["name"],
            "특기: 대형 무기 달인 (Great Weapon Master)",
        )
        self.assertEqual(
            row["fields"]["description"],
            "대형 무기 달인 (Great Weapon Master)",
        )
        self.assertEqual(row["fields"]["source"], "Racial")

    def test_background_grant_has_selected_tough_name_only(self):
        plan = build_feature_plan(self._payload())
        row = next(
            r for r in plan["rows"]
            if r["fields"]["source"] == "Background"
            and r["fields"]["name"].startswith("배경 특기:")
        )
        self.assertEqual(row["fields"]["name"], "배경 특기: 강인함 (Tough)")
        self.assertEqual(row["fields"]["description"], "강인함 (Tough)")

    def test_farmer_asi_is_removed(self):
        plan = build_feature_plan(self._payload())
        active = {r["original_name"] for r in plan["rows"]}
        self.assertNotIn("Farmer Ability Score Improvements", active)
        excluded = {
            r["original_name"]: r["reason"]
            for r in plan["excluded_rows"]
        }
        self.assertEqual(
            excluded["Farmer Ability Score Improvements"],
            "dedicated_roll20_field",
        )

    def test_fighting_style_shows_actual_selected_style(self):
        plan = build_feature_plan(self._payload())
        row = next(
            r for r in plan["rows"]
            if r["source_key"] == "feature:10292232"
        )
        self.assertEqual(
            row["fields"]["name"],
            "전투 스타일: 대형 무기 전투술 (Great Weapon Fighting)",
        )
        self.assertEqual(
            row["fields"]["description"],
            "GWF 전체 설명",
        )
        self.assertEqual(row["fields"]["source"], "Class")

    def test_actual_feat_rows_keep_full_descriptions(self):
        plan = build_feature_plan(self._payload())
        expected = {
            "feature:21": "GWM 전체 설명",
            "feature:1789148": "GWF 전체 설명",
            "feature:1789206": "Tough 전체 설명",
        }
        for key, description in expected.items():
            row = next(r for r in plan["rows"] if r["source_key"] == key)
            self.assertEqual(row["fields"]["description"], description)
            self.assertEqual(row["fields"]["source"], "Feat")

    def test_requested_dedicated_fields_remain_removed(self):
        plan = build_feature_plan(self._payload())
        active = {r["original_name"] for r in plan["rows"]}
        self.assertNotIn("Languages", active)
        self.assertNotIn("Skills", active)

    def test_rows_are_collapsed(self):
        plan = build_feature_plan(self._payload())
        self.assertTrue(plan["rows"])
        for row in plan["rows"]:
            self.assertEqual(row["fields"]["options-flag"], "0")


if __name__ == "__main__":
    unittest.main()
