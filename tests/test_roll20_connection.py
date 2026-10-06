import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from sheet_mover.roll20_connection import (
    _normalize_matches,
    _target_from_result,
    check_roll20_target,
)


class FakeSwitch:
    def __init__(self, driver): self.driver = driver
    def window(self, handle): self.driver.current = handle


class FakeDriver:
    def __init__(self, response, urls=None):
        self.response = response
        self.urls = urls or {"a": "https://app.roll20.net/editor/?viewas="}
        self.window_handles = list(self.urls)
        self.current = self.window_handles[0]
        self.switch_to = FakeSwitch(self)
        self.quit_called = False
    @property
    def current_url(self): return self.urls[self.current]
    def execute_script(self, script, name):
        self.last_name = name
        return self.response
    def quit(self): self.quit_called = True


def _result(path):
    path.write_text(json.dumps({
        "roll20_payload": {
            "source_character_id": "170892133",
            "character": {"source_id": "170892133", "name": "견본 캐릭터"},
        },
        "translation_summary": {"status": "complete"},
    }, ensure_ascii=False), encoding="utf-8")


class Roll20ConnectionTests(unittest.TestCase):
    def test_target_name_comes_from_roll20_payload(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "result.json"
            _result(path)
            source_id, name = _target_from_result(path)
            self.assertEqual(source_id, "170892133")
            self.assertEqual(name, "견본 캐릭터")

    def test_duplicate_dom_and_model_match_are_collapsed(self):
        rows = _normalize_matches({"matches": [
            {"name": "견본 캐릭터", "id": "-abc", "source": "model"},
            {"name": "견본 캐릭터", "id": "-abc", "source": "dom"},
        ]})
        self.assertEqual(len(rows), 1)

    @mock.patch("sheet_mover.roll20_connection._ensure_cdp")
    def test_exact_one_match_passes_read_only(self, ensure):
        with tempfile.TemporaryDirectory() as td:
            result = Path(td) / "result.json"
            _result(result)
            driver = FakeDriver({
                "campaign_id": "-game",
                "campaign_name": "테스트 게임",
                "matches": [{"name": "견본 캐릭터", "id": "-char", "source": "d20.Campaign.characters", "sheet_type": "dnd5e"}],
            })
            target, saved = check_roll20_target(result, save=False, driver_factory=lambda _url: driver)
            self.assertEqual(target.roll20_character_id, "-char")
            self.assertEqual(target.character_name, "견본 캐릭터")
            self.assertIsNone(saved)
            self.assertTrue(driver.quit_called)

    @mock.patch("sheet_mover.roll20_connection._ensure_cdp")
    def test_no_match_fails(self, ensure):
        with tempfile.TemporaryDirectory() as td:
            result = Path(td) / "result.json"
            _result(result)
            driver = FakeDriver({"matches": []})
            with self.assertRaisesRegex(RuntimeError, "찾지 못했습니다"):
                check_roll20_target(result, save=False, driver_factory=lambda _url: driver)

    @mock.patch("sheet_mover.roll20_connection._ensure_cdp")
    def test_duplicate_exact_names_fail_closed(self, ensure):
        with tempfile.TemporaryDirectory() as td:
            result = Path(td) / "result.json"
            _result(result)
            driver = FakeDriver({"matches": [
                {"name": "견본 캐릭터", "id": "-one", "source": "model"},
                {"name": "견본 캐릭터", "id": "-two", "source": "model"},
            ]})
            with self.assertRaisesRegex(RuntimeError, "2개"):
                check_roll20_target(result, save=False, driver_factory=lambda _url: driver)

    @mock.patch("sheet_mover.roll20_connection._ensure_cdp")
    def test_roll20_editor_tab_is_required(self, ensure):
        with tempfile.TemporaryDirectory() as td:
            result = Path(td) / "result.json"
            _result(result)
            driver = FakeDriver({"matches": []}, urls={"a": "https://app.roll20.net/campaigns/details/1"})
            with self.assertRaisesRegex(RuntimeError, "게임 탭"):
                check_roll20_target(result, save=False, driver_factory=lambda _url: driver)


if __name__ == "__main__":
    unittest.main()
