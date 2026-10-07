import unittest
from unittest.mock import patch

from sheet_mover.roll20_spell_attacks import (
    SPELL_LINK_UPSERT_SEQUENTIAL_SCRIPT,
    _upsert_and_verify,
)


class Stage10BSequentialHotfixTests(unittest.TestCase):
    def test_spell_link_writer_is_sequential(self):
        script = SPELL_LINK_UPSERT_SEQUENTIAL_SCRIPT
        self.assertNotIn("Promise.all(jobs)", script)
        self.assertNotIn("const jobs=[]", script)
        self.assertIn("await saveExisting", script)
        self.assertIn("await createNew", script)

    def test_timeout_recovers_when_server_already_persisted(self):
        name = "repeating_spell-2_-SMabc_rollcontent"
        value = "%{-CHAR|repeating_attack_-SMattack_attack}"
        attrs = {name: {"current": value, "max": ""}}

        class Driver:
            def set_script_timeout(self, value):
                self.timeout = value

            def execute_async_script(self, *args):
                return {"ok": False, "error": "save_rollcontent_timeout"}

        snapshot = {
            "attributes": {
                name: [{"id": "-ATTR", "current": value, "max": ""}]
            }
        }

        driver = Driver()
        with patch(
            "sheet_mover.roll20_spell_attacks._snapshot",
            return_value=snapshot,
        ):
            outcome, _ = _upsert_and_verify(
                driver,
                {"roll20_character_id": "-CHAR", "character_name": "견본"},
                attrs,
                "테스트",
                sequential=True,
            )

        self.assertTrue(outcome["ok"])
        self.assertTrue(outcome["recovered_after_upsert_error"])
        self.assertEqual(driver.timeout, 120)


if __name__ == "__main__":
    unittest.main()
