import unittest
from unittest.mock import patch

from sheet_mover.roll20_spell_attacks import (
    _poll_persisted,
    _split_link_attrs,
)


class Stage10BRollcontentDispatchTests(unittest.TestCase):
    def test_link_fields_are_split_from_normal_spell_fields(self):
        attrs = {
            "repeating_spell-2_-SMabc_spelloutput": {
                "current": "ATTACK",
                "max": "",
            },
            "repeating_spell-2_-SMabc_spellattackid": {
                "current": "-SMattack",
                "max": "",
            },
            "repeating_spell-2_-SMabc_rollcontent": {
                "current": "%{-CHAR|repeating_attack_-SMattack_attack}",
                "max": "",
            },
        }

        normal, links = _split_link_attrs(attrs)

        self.assertEqual(len(normal), 1)
        self.assertEqual(len(links), 2)
        self.assertIn("repeating_spell-2_-SMabc_spelloutput", normal)
        self.assertIn("repeating_spell-2_-SMabc_spellattackid", links)
        self.assertIn("repeating_spell-2_-SMabc_rollcontent", links)

    def test_poll_waits_until_server_value_matches(self):
        name = "repeating_spell-2_-SMabc_rollcontent"
        value = "%{-CHAR|repeating_attack_-SMattack_attack}"
        attrs = {name: {"current": value, "max": ""}}

        missing = {"attributes": {name: []}}
        present = {
            "attributes": {
                name: [{"id": "-ATTR", "current": value, "max": ""}]
            }
        }

        with patch(
            "sheet_mover.roll20_spell_attacks._snapshot",
            side_effect=[missing, missing, present],
        ), patch(
            "sheet_mover.roll20_spell_attacks.time.sleep"
        ):
            actual, attempts = _poll_persisted(
                object(),
                {},
                attrs,
                "테스트",
                attempts=4,
                delay=0.01,
            )

        self.assertEqual(attempts, 3)
        self.assertEqual(actual[name]["current"], value)


if __name__ == "__main__":
    unittest.main()
