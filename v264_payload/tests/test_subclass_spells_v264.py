# -*- coding: utf-8 -*-
import unittest
from types import SimpleNamespace

from sheet_mover.subclass_spells import (
    _spell_table_rows,
    _feature_grant_type,
    apply_subclass_spell_resolution,
)


def spell_definition(spell_id, name, level, legacy, source_id):
    return {
        "id": spell_id,
        "name": name,
        "level": level,
        "school": "Enchantment",
        "description": f"{name} rules",
        "activation": {"activationType": 1, "activationTime": 1},
        "range": {"origin": "Ranged", "rangeValue": 60},
        "duration": {"durationType": "Instantaneous"},
        "components": [1, 2],
        "componentsDescription": "",
        "ritual": False,
        "concentration": False,
        "saveDcAbilityId": None,
        "attackType": None,
        "damageEffect": None,
        "isLegacy": legacy,
        "sources": [{"sourceId": source_id, "sourceType": 1}],
    }


class SubclassSpellV264Tests(unittest.TestCase):
    def make_sheet(self, spells=None):
        return SimpleNamespace(
            spells=list(spells or []),
            calculation_inputs={},
            warnings=[],
        )

    def test_parses_level_spell_table(self):
        html = """
        <table><thead><tr><th>Paladin Level</th><th>Spells</th></tr></thead>
        <tbody>
          <tr><td><p>3rd</p></td><td><p>bane, hunter’s mark</p></td></tr>
          <tr><td><p>5th</p></td><td><p>hold person, misty step</p></td></tr>
        </tbody></table>
        """
        self.assertEqual(
            _spell_table_rows(html),
            [(3, ["bane", "hunter's mark"]), (5, ["hold person", "misty step"])],
        )

    def test_expanded_list_never_auto_adds(self):
        expanded = {
            "id": 395,
            "name": "Expanded Spell List",
            "description": (
                "The Archfey lets you choose from an expanded list of spells when you learn a warlock spell."
                "<table><tr><th>Spell Level</th><th>Spells</th></tr>"
                "<tr><td>1st</td><td>faerie fire, sleep</td></tr></table>"
            ),
            "snippet": "",
            "requiredLevel": 1,
        }
        raw = {
            "classes": [{
                "id": 1001,
                "level": 9,
                "definition": {
                    "name": "Warlock",
                    "sources": [{"sourceId": 11}],
                    "spellCastingAbilityId": 6,
                },
                "classFeatures": [{"definition": expanded}],
            }]
        }
        sheet = self.make_sheet()
        report = apply_subclass_spell_resolution(raw, sheet, catalog=[])
        self.assertEqual(sheet.spells, [])
        self.assertEqual(report["expanded_list_count"], 1)
        self.assertEqual(report["added_count"], 0)

    def test_twilight_style_reference_inherits_always_prepared_semantics(self):
        domain_rules = {
            "id": 109,
            "name": "Divine Domain",
            "description": (
                "Each domain has a list of spells. Once you gain a domain spell, "
                "you always have it prepared, and it doesn't count against the number "
                "of spells you can prepare each day."
            ),
            "requiredLevel": 1,
        }
        domain_table = {
            "id": 2996528,
            "name": "Domain Spells",
            "description": (
                "You gain domain spells at the cleric levels listed. "
                "See the Divine Domain class feature for how domain spells work."
                "<table><tr><th>Cleric Level</th><th>Spells</th></tr>"
                "<tr><td>1st</td><td>faerie fire, sleep</td></tr>"
                "<tr><td>11th</td><td>future spell</td></tr></table>"
            ),
            "requiredLevel": 1,
        }
        self.assertEqual(
            _feature_grant_type(domain_table, [domain_rules, domain_table]),
            "always_prepared",
        )

        catalog = [
            spell_definition(2001, "Faerie Fire", 1, True, 1),
            spell_definition(2002, "Sleep", 1, True, 1),
            spell_definition(9001, "Faerie Fire", 1, False, 145),
            spell_definition(9002, "Sleep", 1, False, 145),
        ]
        raw = {
            "classes": [{
                "id": 2001,
                "level": 9,
                "definition": {
                    "name": "Cleric",
                    "sources": [{"sourceId": 1}],
                    "spellCastingAbilityId": 5,
                },
                "classFeatures": [
                    {"definition": domain_rules},
                    {"definition": domain_table},
                ],
            }]
        }
        sheet = self.make_sheet()
        report = apply_subclass_spell_resolution(raw, sheet, catalog=catalog)
        self.assertEqual(report["added_count"], 2)
        self.assertEqual(report["unresolved_count"], 0)
        self.assertEqual(
            {row["definition_id"] for row in sheet.spells},
            {"2001", "2002"},
        )
        self.assertTrue(all(row["always_prepared"] for row in sheet.spells))

    def test_edition_resolution_is_source_driven_not_name_only(self):
        feature = {
            "id": 293,
            "entityTypeId": 12168134,
            "name": "Oath Spells",
            "description": (
                "You gain oath spells at the paladin levels listed."
                "<table><tr><th>Paladin Level</th><th>Spells</th></tr>"
                "<tr><td>3rd</td><td>bane</td></tr></table>"
            ),
            "snippet": "The listed oath spells are always prepared.",
            "requiredLevel": 3,
        }
        catalog = [
            spell_definition(2009, "Bane", 1, True, 1),
            spell_definition(2618900, "Bane", 1, False, 145),
        ]
        raw = {
            "classes": [{
                "id": 3001,
                "level": 13,
                "definition": {
                    "name": "Paladin",
                    "sources": [{"sourceId": 1}],
                    "spellCastingAbilityId": 6,
                },
                "classFeatures": [{"definition": feature}],
            }]
        }
        sheet = self.make_sheet()
        report = apply_subclass_spell_resolution(raw, sheet, catalog=catalog)
        self.assertEqual(report["added_count"], 1)
        self.assertEqual(sheet.spells[0]["definition_id"], "2009")
        self.assertIs(sheet.spells[0]["definition_is_legacy"], True)

    def test_existing_exact_definition_is_upgraded_without_duplicate(self):
        feature = {
            "id": 500,
            "name": "Granted Spells",
            "description": (
                "You always have the listed spells prepared."
                "<table><tr><th>Class Level</th><th>Spells</th></tr>"
                "<tr><td>3rd</td><td>bane</td></tr></table>"
            ),
            "requiredLevel": 3,
        }
        catalog = [spell_definition(2009, "Bane", 1, True, 1)]
        existing = {
            "source_id": "char-spell-1",
            "definition_id": "2009",
            "kind": "spell",
            "name": "Bane",
            "original_name": "Bane",
            "level": 1,
            "prepared": False,
            "always_prepared": False,
            "uses_spell_slot": True,
        }
        raw = {
            "classes": [{
                "id": 4001,
                "level": 5,
                "definition": {
                    "name": "Example",
                    "sources": [{"sourceId": 1}],
                    "spellCastingAbilityId": 6,
                },
                "classFeatures": [{"definition": feature}],
            }]
        }
        sheet = self.make_sheet([existing])
        report = apply_subclass_spell_resolution(raw, sheet, catalog=catalog)
        self.assertEqual(len(sheet.spells), 1)
        self.assertEqual(report["upgraded_count"], 1)
        self.assertTrue(sheet.spells[0]["always_prepared"])

    def test_ambiguous_definition_fails_closed(self):
        feature = {
            "id": 501,
            "name": "Granted Spells",
            "description": (
                "You always have the listed spells prepared."
                "<table><tr><th>Class Level</th><th>Spells</th></tr>"
                "<tr><td>3rd</td><td>mystery spell</td></tr></table>"
            ),
            "requiredLevel": 3,
        }
        catalog = [
            spell_definition(1, "Mystery Spell", 1, True, 9),
            spell_definition(2, "Mystery Spell", 1, True, 10),
        ]
        raw = {
            "classes": [{
                "id": 5001,
                "level": 5,
                "definition": {"name": "Example", "sources": []},
                "classFeatures": [{"definition": feature}],
            }]
        }
        sheet = self.make_sheet()
        report = apply_subclass_spell_resolution(raw, sheet, catalog=catalog)
        self.assertEqual(sheet.spells, [])
        self.assertEqual(report["unresolved_count"], 1)
        self.assertEqual(report["status"], "partial")


if __name__ == "__main__":
    unittest.main()
