import unittest
from pathlib import Path


class UIRoll20ManualLaunchTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = (Path("sheet_mover") / "ui.py").read_text(encoding="utf-8")

    def test_startup_only_checks_status(self):
        self.assertIn("self.after(300, self.check_all)", self.source)
        self.assertNotIn("self.after(300, self.auto_launch_roll20)", self.source)
        self.assertNotIn("def auto_launch_roll20", self.source)

    def test_manual_roll20_button_remains(self):
        self.assertIn("Roll20 전용 Chrome 열기", self.source)
        self.assertIn("command=self.launch_roll20", self.source)

    def test_manual_launch_keeps_dedicated_profile_and_port(self):
        self.assertIn(
            'data_dir() / ".roll20_chrome_profile"',
            self.source,
        )
        self.assertIn("urlparse(self.settings.roll20_cdp_url)", self.source)
        self.assertIn("port=port", self.source)


if __name__ == "__main__":
    unittest.main()
