import unittest

from sheet_mover.source import normalize_character
from sheet_mover.roll20_features import (
    TRAIT_UPSERT_SEQUENTIAL_SCRIPT,
    _stale_sheetmover_row_ids,
    feature_row_id,
)


class Stage8RowIntegrityV17Tests(unittest.TestCase):
    def test_unselected_top_level_feat_is_not_owned_feature(self):
        data = {
            "id": 1,
            "name": "Ghost Feat Test",
            "race": {
                "fullName": "Human",
                "racialTraits": [],
            },
            "background": {
                "definition": {
                    "id": 1,
                    "name": "Background",
                    "featureName": "",
                    "featureDescription": "",
                    "grantedFeats": [],
                }
            },
            "classes": [],
            "feats": [
                {
                    "componentId": 54571,
                    "componentTypeId": 67468084,
                    "definition": {
                        "id": 2048517,
                        "name": "Dark Bargain",
                        "description": "<p>Candidate only</p>",
                        "categories": [{"tagName": "__DISGUISE_FEAT"}],
                    },
                }
            ],
            "choices": {},
            "modifiers": {},
            "classSpells": [],
        }

        sheet = normalize_character(data)
        feat_names = [
            item["original_name"]
            for item in sheet.features
            if item["kind"] == "feat"
        ]
        self.assertEqual(feat_names, [])

    def test_stage8_trait_writer_is_sequential(self):
        script = TRAIT_UPSERT_SEQUENTIAL_SCRIPT
        self.assertNotIn("Promise.all(jobs)", script)
        self.assertNotIn("const jobs=[]", script)
        self.assertIn("await saveExisting", script)
        self.assertIn("await createNew", script)
        self.assertIn("await fetchCollection(collection)", script)

    def test_stale_cleanup_only_targets_sheetmover_owned_ids(self):
        keep = feature_row_id("feature:keep")
        stale = feature_row_id("feature:old-dark-bargain")
        state = {
            "row_ids": [
                keep,
                stale,
                "-ManualRoll20Row12345",
                "-SMnot-a-sheetmover-id",
            ]
        }
        plan = {"all_managed_row_ids": [keep]}
        self.assertEqual(
            _stale_sheetmover_row_ids(state, plan),
            [stale],
        )


if __name__ == "__main__":
    unittest.main()
