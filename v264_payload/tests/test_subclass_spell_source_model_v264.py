# -*- coding: utf-8 -*-
import unittest

from sheet_mover.source import _normalize_spell
from sheet_mover.roll20_payload import build_roll20_payload
from sheet_mover.roll20_spells import _raw_spell_definitions


class SubclassSpellSourceModelV264Tests(unittest.TestCase):
    def test_structured_spell_preserves_source_identity_and_semantics(self):
        entry = {
            "id": 123,
            "componentId": 456,
            "componentTypeId": 789,
            "prepared": False,
            "alwaysPrepared": True,
            "countsAsKnownSpell": False,
            "usesSpellSlot": True,
            "spellCastingAbilityId": 6,
            "definition": {
                "id": 999,
                "name": "Example Spell",
                "level": 2,
                "description": "rules",
                "activation": {},
                "range": {},
                "duration": {},
                "components": [],
                "componentsDescription": "",
                "school": "Evocation",
                "ritual": False,
                "concentration": False,
                "saveDcAbilityId": None,
                "attackType": None,
                "damageEffect": None,
                "isLegacy": False,
            },
        }
        item = _normalize_spell(
            entry,
            source_group="class",
            character_class_id="class-instance-1",
        )
        self.assertEqual(item["source_group"], "class")
        self.assertEqual(item["character_class_id"], "class-instance-1")
        self.assertEqual(item["component_id"], "456")
        self.assertEqual(item["component_type_id"], "789")
        self.assertIs(item["definition_is_legacy"], False)
        self.assertEqual(item["grant_type"], "always_prepared")

    def test_roll20_payload_preserves_grant_metadata(self):
        source = {
            "source_id": "1",
            "name": "Sample",
            "spells": [{
                "source_id": "s1",
                "definition_id": "d1",
                "kind": "spell",
                "name": "Spell",
                "original_name": "Spell",
                "description": "rules",
                "level": 1,
                "prepared": True,
                "always_prepared": True,
                "uses_spell_slot": True,
                "source_group": "subclass_feature_fallback",
                "character_class_id": "c1",
                "component_id": "f1",
                "component_type_id": "ct1",
                "definition_is_legacy": True,
                "grant_type": "always_prepared",
                "grant_feature_id": "f1",
                "grant_feature_name": "Granted Spells",
            }],
        }
        payload = build_roll20_payload(source, source)
        row = payload["spells"][0]
        self.assertEqual(row["grant_type"], "always_prepared")
        self.assertEqual(row["source_group"], "subclass_feature_fallback")
        self.assertEqual(row["grant_feature_id"], "f1")

    def test_stage7_reads_catalog_definition_from_resolution_report(self):
        definition = {"id": 77, "name": "Bane", "level": 1}
        payload = {
            "raw_source": {"classSpells": [], "spells": {}},
            "original": {
                "calculation_inputs": {
                    "subclass_spell_resolution": {
                        "resolved_definitions": {
                            "subclass-grant:x:y:77": definition,
                        }
                    }
                }
            },
        }
        result = _raw_spell_definitions(payload)
        self.assertEqual(result["subclass-grant:x:y:77"], definition)


if __name__ == "__main__":
    unittest.main()
