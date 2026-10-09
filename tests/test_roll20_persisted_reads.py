import importlib
import json
from contextlib import ExitStack
from pathlib import Path
import tempfile
import subprocess
import sys
import shutil
import unittest
from unittest.mock import Mock, patch

from sheet_mover.roll20_read import PersistedReadError, read_persisted


class PersistedReadTests(unittest.TestCase):
    def test_browser_fetch_scripts_report_failures_and_ignore_late_success(self):
        node = shutil.which("node")
        if not node:
            bundled = Path(sys.executable).parent.parent / "Lib/site-packages/playwright/driver/node.exe"
            node = str(bundled) if bundled.exists() else None
        if not node:
            self.skipTest("JavaScript runtime unavailable")
        scripts = (
            ("stage5_basic_writer_v2", "FETCH_SCRIPT"),
            ("sheet_mover.roll20_inventory", "FETCH_ATTRS_SCRIPT"),
            ("sheet_mover.roll20_features", "TRAIT_STATE_SCRIPT"),
            ("sheet_mover.roll20_attacks", "ATTACK_STATE_SCRIPT"),
            ("sheet_mover.roll20_proficiencies", "PROFICIENCY_STATE_SCRIPT"),
            ("sheet_mover.roll20_resources", "RESOURCE_STATE_SCRIPT"),
        )
        cases = [{"script": getattr(importlib.import_module(module), name), "mode": mode}
                 for module, name in scripts
                 for mode in ("success", "success_promise", "error", "error_promise", "timeout", "exception")]
        harness = r"""
const cases = JSON.parse(require('fs').readFileSync(0, 'utf8'));
const results = cases.map(test => {
  const timers = [], calls = [];
  let lateSuccess;
  const collection = {
    models:[{attributes:{id:'cached', name:'hp', current:'10', max:''}}],
    fetch(options) {
      if (test.mode === 'success') options.success();
      if (test.mode === 'error') options.error(null, {status:503,statusText:'Unavailable'});
      if (test.mode === 'exception') throw new Error('offline');
      if (test.mode === 'timeout') lateSuccess = options.success;
      if (test.mode === 'success_promise') return {then(success) {success();}};
      if (test.mode === 'error_promise') return {then(success,error) {error('offline');}};
    },
  };
  const character = {attributes:{id:'id', name:'fixture'}, attribs:collection};
  const window = {d20:{Campaign:{characters:{models:[character]}}}};
  const read = new Function('window','setTimeout','return function() {' + test.script + '}')(
    window, fn => timers.push(fn));
  read('id','fixture',['hp'],result => calls.push(result));
  timers.forEach(fn => fn());
  if (lateSuccess) lateSuccess();
  return {calls:calls.length, result:calls[0]};
});
process.stdout.write(JSON.stringify(results));
"""
        result = subprocess.run([node, "-e", harness], input=json.dumps(cases),
                                text=True, encoding="utf-8", capture_output=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr)
        for case, actual in zip(cases, json.loads(result.stdout)):
            with self.subTest(mode=case["mode"]):
                self.assertEqual(actual["calls"], 1)
                self.assertEqual(actual["result"]["fetch_status"], case["mode"])
                self.assertEqual(actual["result"]["ok"], case["mode"].startswith("success"))

    def setUp(self):
        self.sleep = patch("sheet_mover.roll20_read.time.sleep").start()
        self.addCleanup(patch.stopall)

    def test_matching_cached_values_do_not_prove_persistence(self):
        for status in ("error", "error_promise", "timeout", None):
            with self.subTest(status=status):
                driver = Mock()
                driver.execute_async_script.return_value = {
                    "ok": True, "fetch_status": status,
                    "attributes": {"hp": [{"current": "10"}]},
                }
                with self.assertRaises(PersistedReadError):
                    read_persisted(driver, "read-only-script")
                self.assertEqual(driver.execute_async_script.call_count, 3)
                self.assertTrue(all(call.args == ("read-only-script",)
                                    for call in driver.execute_async_script.call_args_list))

    def test_transient_failure_retries_only_read_and_returns_fresh_server_value(self):
        driver = Mock()
        fresh = {"ok": True, "fetch_status": "success", "attributes": {"hp": 12}}
        driver.execute_async_script.side_effect = [
            TimeoutError(), {"ok": False, "fetch_status": "error"}, fresh,
        ]
        self.assertIs(read_persisted(driver, "read", "target-id"), fresh)
        self.assertEqual(driver.execute_async_script.call_count, 3)

    def test_missing_target_stops_immediately(self):
        driver = Mock()
        driver.execute_async_script.return_value = {"ok": False, "reason": "not_found"}
        with self.assertRaises(PersistedReadError):
            read_persisted(driver, "read")
        driver.execute_async_script.assert_called_once()

    def test_every_active_snapshot_rejects_failed_fetch(self):
        readers = (
            ("stage5_basic_writer_v2", "_snapshot", True),
            ("sheet_mover.roll20_inventory", "_snapshot", True),
            ("sheet_mover.roll20_features", "_trait_state", False),
            ("sheet_mover.roll20_attacks", "_attack_state", False),
            ("sheet_mover.roll20_proficiencies", "_proficiency_state", False),
            ("sheet_mover.roll20_resources", "_resource_state", False),
        )
        for module, name, needs_names in readers:
            with self.subTest(reader=module):
                driver = Mock()
                driver.execute_async_script.return_value = {
                    "ok": True, "fetch_status": "error", "attributes": {},
                }
                args = (driver, {"roll20_character_id": "id", "character_name": "fixture"})
                if needs_names:
                    args += (["hp"],)
                with self.assertRaises(PersistedReadError):
                    getattr(importlib.import_module(module), name)(*args)

    def test_inventory_aborts_before_writing_when_initial_server_read_fails(self):
        self._inventory_failure(after_write=False)

    def test_inventory_does_not_repeat_write_when_post_write_verification_fails(self):
        self._inventory_failure(after_write=True)

    def _inventory_failure(self, after_write):
        from sheet_mover import roll20_inventory as inventory
        with tempfile.TemporaryDirectory() as temp, ExitStack() as stack:
            root = Path(temp)
            result = root / "result.json"
            result.write_text("{}", encoding="utf-8")
            row = {"source_key": "item-1", "row_id": "row1", "name": "Sword",
                   "fields": {"itemname": "Sword"}}
            plan = {"source_character_id": "1", "character_name": "fixture",
                    "row_count": 1, "policy": {}, "rows": [row]}
            driver = Mock()
            failed = {"ok": True, "fetch_status": "error", "attributes": {}}
            driver.execute_async_script.side_effect = (
                [{"ok": True, "fetch_status": "success", "attributes": {}},
                 {"ok": True}, failed, failed, failed] if after_write else [failed] * 3
            )
            for name, value in (
                ("CURRENT_RESULT_DIR", root), ("build_inventory_plan", Mock(return_value=plan)),
                ("_load_target", Mock(return_value=({"character_name": "fixture",
                                                      "roll20_character_id": "id"}, root / "target.json"))),
                ("_ensure_cdp", Mock()), ("_attach_driver", Mock(return_value=driver)),
                ("_select_roll20_tab", Mock()),
            ):
                stack.enter_context(patch.object(inventory, name, value))
            with self.assertRaises(PersistedReadError):
                inventory.apply_inventory(result_path=result)
            writes = [call for call in driver.execute_async_script.call_args_list
                      if call.args[0] == inventory.UPSERT_ROW_SCRIPT]
            self.assertEqual(len(writes), int(after_write))
            report = json.loads((root / "roll20-inventory-1.json").read_text(encoding="utf-8"))
            self.assertEqual(report["status"], "error")
            self.assertEqual(report["mutated"], after_write)
            self.assertNotEqual(report["verification"].get("status"), "pass")
