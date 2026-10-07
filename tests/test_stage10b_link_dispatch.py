import unittest

from sheet_mover.roll20_spell_attacks import _split_link_attrs


class Stage10BLinkDispatchTests(unittest.TestCase):
    def test_spellattackid_and_rollcontent_are_deferred_together(self):
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

        self.assertIn("repeating_spell-2_-SMabc_spelloutput", normal)
        self.assertIn("repeating_spell-2_-SMabc_spellattackid", links)
        self.assertIn("repeating_spell-2_-SMabc_rollcontent", links)
        self.assertNotIn("repeating_spell-2_-SMabc_spellattackid", normal)
        self.assertNotIn("repeating_spell-2_-SMabc_rollcontent", normal)


if __name__ == "__main__":
    unittest.main()
