import ast
from pathlib import Path
import unittest


class UIRoll20LaunchTests(unittest.TestCase):
    def test_startup_does_not_launch_browser(self):
        path = Path("sheet_mover") / "ui.py"
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source)
        constructor = next(node for node in ast.walk(tree)
                           if isinstance(node, ast.FunctionDef) and node.name == "__init__")
        calls = [node.func.attr for node in ast.walk(constructor)
                 if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)]
        self.assertNotIn("launch_roll20", calls)
        self.assertNotIn("auto_launch_roll20", calls)

    def test_ui_uses_dedicated_runtime_profile(self):
        source = (Path("sheet_mover") / "ui.py").read_text(encoding="utf-8")
        self.assertIn(
            'data_dir() / ".roll20_chrome_profile"',
            source,
        )

    def test_ui_honors_configured_cdp_port(self):
        source = (Path("sheet_mover") / "ui.py").read_text(encoding="utf-8")
        self.assertIn("urlparse(self.settings.roll20_cdp_url)", source)
        self.assertIn("port=port", source)


if __name__ == "__main__":
    unittest.main()
