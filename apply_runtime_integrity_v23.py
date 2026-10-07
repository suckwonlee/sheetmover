# -*- coding: utf-8 -*-
from __future__ import annotations

import argparse
import ast
import importlib.util
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile

ROOT = Path.cwd()
HERE = Path(__file__).resolve().parent
SUPPORT = HERE / "patch_support_runtime_v22.py"

SPELLS = ROOT / "sheet_mover" / "roll20_spells.py"
PROFS = ROOT / "sheet_mover" / "roll20_proficiencies.py"
FULL_RUN = ROOT / "sheet_mover" / "full_run.py"
MOVER = ROOT / "sheet_mover" / "mover.py"
UI = ROOT / "sheet_mover" / "ui.py"
TEST_FULL_RUN = ROOT / "tests" / "test_full_run.py"

BASE_COMMIT = "29c50d9c5850495667a02666e08a1acb25ab1591"


def _load_support():
    if not SUPPORT.is_file():
        raise RuntimeError(f"패치 지원 파일이 없습니다: {SUPPORT}")
    spec = importlib.util.spec_from_file_location("sheetmover_runtime_v22_support", SUPPORT)
    if spec is None or spec.loader is None:
        raise RuntimeError("패치 지원 파일을 불러오지 못했습니다.")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _base_text(path: Path) -> tuple[str, str]:
    """Prefer the clean snapshot from immediately before the broken v2.2 apply."""
    backup = path.with_suffix(path.suffix + ".pre-runtime-v2.2.bak")
    if backup.is_file():
        return backup.read_text(encoding="utf-8"), str(backup)
    return path.read_text(encoding="utf-8"), str(path)


def _replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: 예상 코드 1개가 필요한데 {count}개를 찾았습니다.")
    return text.replace(old, new, 1)


def _replace_region(text: str, start_marker: str, end_marker: str, new: str, label: str) -> str:
    start = text.find(start_marker)
    if start < 0:
        raise RuntimeError(f"{label}: 시작 위치를 찾지 못했습니다.")
    end = text.find(end_marker, start)
    if end < 0:
        raise RuntimeError(f"{label}: 끝 위치를 찾지 못했습니다.")
    return text[:start] + new + text[end:]


def _patch_proficiencies(support, text: str) -> str:
    # Current tree may contain the v2.2 installer bug literally as "\\nSTAGE11...".
    text = text.replace(
        '\\nSTAGE11_NO_WAIT_ATTR_SCRIPT = r"""',
        'STAGE11_NO_WAIT_ATTR_SCRIPT = r"""',
    )

    if "stage11-roll20-proficiencies-v2.3-resilient-batches" in text:
        return text

    if "stage11-roll20-proficiencies-v2.2-resilient-batches" not in text:
        text = support.patch_proficiencies(text)
        text = text.replace(
            '\\nSTAGE11_NO_WAIT_ATTR_SCRIPT = r"""',
            'STAGE11_NO_WAIT_ATTR_SCRIPT = r"""',
        )

    text = text.replace(
        'STAGE11_VERSION = "2026-10-07-stage11-roll20-proficiencies-v2.2-resilient-batches"',
        'STAGE11_VERSION = "2026-10-07-stage11-roll20-proficiencies-v2.3-resilient-batches"',
        1,
    )
    return text


def _patch_full_run_v23(text: str) -> str:
    if "stage13-full-run-v2.3-roll20-preflight-cache" in text:
        return text

    if "stage13-full-run-v2.2-roll20-preflight" not in text:
        raise RuntimeError("full_run.py에 v2.2 Roll20 preflight 구조가 없습니다.")

    text = text.replace(
        'FULL_RUN_VERSION = "2026-10-07-stage13-full-run-v2.2-roll20-preflight"',
        'FULL_RUN_VERSION = "2026-10-07-stage13-full-run-v2.3-roll20-preflight-cache"',
        1,
    )

    if "def _find_reusable_result(" not in text:
        marker = '''def _write_json(path: Path, payload: dict) -> Path:\n    from .result_store import write_json_atomic\n    return write_json_atomic(path, payload)\n'''
        helper = '''\n\ndef _result_summary(payload):\n    if not isinstance(payload, dict):\n        return {}\n    summary = payload.get("translation_summary")\n    if isinstance(summary, dict):\n        return summary\n    translated = payload.get("translated")\n    if isinstance(translated, dict):\n        summary = translated.get("translation_summary")\n        if isinstance(summary, dict):\n            return summary\n    return {}\n\n\ndef _stable_json(value):\n    try:\n        return json.dumps(\n            value,\n            ensure_ascii=False,\n            sort_keys=True,\n            separators=(",", ":"),\n        )\n    except (TypeError, ValueError):\n        return ""\n\n\ndef _find_reusable_result(source_id: str, raw_source: dict):\n    # Reuse only a result produced from the exact same D&D Beyond raw payload.\n    folder = data_dir() / "results" / "current"\n    if not folder.is_dir():\n        return None, None\n\n    wanted_raw = _stable_json(raw_source)\n    if not wanted_raw:\n        return None, None\n\n    candidates = sorted(\n        folder.glob(f"sheet-result-{source_id}-*.json"),\n        key=lambda path: path.name,\n        reverse=True,\n    )\n    for path in candidates:\n        try:\n            payload = json.loads(path.read_text(encoding="utf-8"))\n        except (OSError, UnicodeDecodeError, json.JSONDecodeError):\n            continue\n        if not isinstance(payload, dict):\n            continue\n\n        original = payload.get("original")\n        if not isinstance(original, dict):\n            continue\n        if str(original.get("source_id") or "").strip() != str(source_id).strip():\n            continue\n\n        summary = _result_summary(payload)\n        if str(summary.get("status") or "").strip() not in {"complete", "partial"}:\n            continue\n        if not isinstance(payload.get("roll20_payload"), dict):\n            continue\n\n        cached_raw = payload.get("raw_source")\n        if not isinstance(cached_raw, dict):\n            continue\n        if _stable_json(cached_raw) != wanted_raw:\n            continue\n        return payload, path\n\n    return None, None\n'''
        text = _replace_once(text, marker, marker + helper, "full-run cache helper")

    start_marker = "        # Import stages only after applying the worker's settings snapshot.\n"
    end_marker = '        run_stage(3, "basic", apply_basic, source_id=source_id,\n'
    new_region = '''        # Import stages only after applying the worker's settings snapshot.\n        from .mover import run as prepare\n        from .result_store import default_result_path\n        from .roll20_connection import check_roll20_target\n        from .source import fetch_character\n        from .roll20_inventory import apply_inventory\n        from .roll20_spells import apply_spells\n        from .roll20_features import apply_features\n        from .roll20_attacks import apply_attacks\n        from .roll20_spell_attacks import apply_spell_attacks\n        from .roll20_proficiencies import apply_proficiencies\n        from .roll20_resources import apply_resources\n        from stage5_basic_writer_v3 import run as apply_basic\n\n        # Do not spend translation work until the actual Roll20 destination is ready.\n        stage(2, "running", "번역 전에 Roll20 대상과 시트 상태를 확인합니다.")\n        overall(1, "D&D Beyond 캐릭터 ID와 이름만 먼저 확인합니다.")\n        identity_raw = fetch_character(source_url)\n        source_id = str(identity_raw.get("id") or "").strip()\n        character_name = str(identity_raw.get("name") or "").strip()\n        if not source_id or not character_name:\n            raise RuntimeError("D&D Beyond 원본에서 캐릭터 ID/이름을 확인하지 못했습니다.")\n\n        state["source_character_id"] = source_id\n        state["character_name"] = character_name\n\n        preflight_path = data_dir() / "workers" / run_id / "roll20-preflight.json"\n        _write_json(\n            preflight_path,\n            {\n                "original": {"source_id": source_id, "name": character_name},\n                "roll20_payload": {\n                    "source_character_id": source_id,\n                    "character": {"name": character_name},\n                },\n            },\n        )\n        try:\n            target, target_path = check_roll20_target(\n                result_path=preflight_path,\n                source_id=source_id,\n                cdp_url=settings.roll20_cdp_url,\n                save=True,\n            )\n        finally:\n            preflight_path.unlink(missing_ok=True)\n\n        sheet_type = str(getattr(target, "sheet_type", "") or "").strip()\n        if sheet_type != "ogl5e":\n            raise RuntimeError(\n                "Roll20 대상 캐릭터가 Legacy OGL5e 시트가 아닙니다. "\n                f"확인된 시트 유형: {sheet_type or '미확인'}"\n            )\n\n        state["reports"]["target"] = (\n            target.__dict__ if hasattr(target, "__dict__") else str(target)\n        )\n        state["paths"]["target"] = str(Path(target_path).resolve())\n        stage(2, "pass", "Roll20 게임 탭 · 대상 캐릭터 · Legacy OGL5e 시트 확인 완료")\n        overall(5, "Roll20 준비 완료. 기존 번역 결과를 확인합니다.")\n\n        payload, reusable_path = _find_reusable_result(source_id, identity_raw)\n        if payload is not None and reusable_path is not None:\n            original = payload.get("original") or {}\n            if str(original.get("name") or "").strip() != character_name:\n                payload = None\n                reusable_path = None\n\n        if payload is not None and reusable_path is not None:\n            result_path = Path(reusable_path)\n            summary = _result_summary(payload)\n            translation_status = str(summary.get("status") or "").strip()\n            preserved_count = int(summary.get("original_preserved_count") or 0)\n            state["translation_summary"] = summary\n            state["paths"]["sheet_result"] = str(result_path.resolve())\n            if translation_status == "partial":\n                stage(\n                    1,\n                    "pass",\n                    f"기존 번역 결과 재사용: {result_path.name} · "\n                    f"원문 유지 {preserved_count}개",\n                )\n                overall(\n                    30,\n                    f"기존 번역 재사용 완료 · API 번역 생략 · 원문 유지 {preserved_count}개",\n                )\n            else:\n                stage(1, "pass", f"기존 번역 결과 재사용: {result_path.name}")\n                overall(30, "기존 번역 재사용 완료 · API 번역 생략")\n        else:\n            stage(1, "running", "Roll20 준비 완료. 번역 서비스 상태를 확인합니다.")\n            overall(5, "재사용 가능한 번역이 없습니다. 번역 서비스 상태를 확인합니다.")\n            for name, check in (("Google", check_google), ("Ollama", check_ollama)):\n                result = check(settings)\n                if not result.get("ok"):\n                    raise RuntimeError(\n                        f"{name}: {result.get('message', '설정 오류')} "\n                        f"{result.get('detail') or ''}".strip()\n                    )\n\n            prepared = prepare(\n                source_url,\n                cdp_url=settings.roll20_cdp_url,\n                on_progress=lambda p, m: overall(5 + float(p) * .25, m),\n                raw_source=identity_raw,\n            )\n            payload = prepared.to_dict()\n            original = payload.get("original") or {}\n            prepared_source_id = str(original.get("source_id") or "").strip()\n            prepared_name = str(original.get("name") or "").strip()\n            if prepared_source_id != source_id or prepared_name != character_name:\n                raise RuntimeError(\n                    "사전 확인한 D&D Beyond 캐릭터와 번역 대상이 달라졌습니다. "\n                    "Roll20 입력을 중단합니다."\n                )\n\n            summary = payload.get("translation_summary") or {}\n            translation_status = str(summary.get("status") or "").strip()\n            preserved_count = int(summary.get("original_preserved_count") or 0)\n            state["translation_summary"] = summary\n            if translation_status not in {"complete", "partial"}:\n                raise RuntimeError(\n                    "번역 결과 상태를 확인할 수 없어 Roll20 입력을 중단합니다. "\n                    f"상태={translation_status or '없음'}"\n                )\n\n            result_path = default_result_path(source_id, root=data_dir())\n            _write_json(result_path, payload)\n            state["paths"]["sheet_result"] = str(result_path.resolve())\n\n            if translation_status == "partial":\n                stage(\n                    1,\n                    "pass",\n                    f"원문 {preserved_count}개를 안전하게 유지하고 계속 진행합니다. "\n                    f"결과 저장: {result_path.name}",\n                )\n                overall(30, f"D&D Beyond 준비 완료 · 원문 유지 {preserved_count}개")\n            else:\n                stage(1, "pass", f"결과 저장: {result_path.name}")\n                overall(30, "D&D Beyond 준비 완료")\n\n'''
    return _replace_region(text, start_marker, end_marker, new_region, "full-run v2.3 flow")


def _patch_ui(text: str) -> str:
    if 'log_path = run_folder / "run.log"' not in text:
        if 'log_path = run_folder / "worker.log"' not in text:
            raise RuntimeError("ui.py에서 worker log 경로를 찾지 못했습니다.")
        text = text.replace(
            '        log_path = run_folder / "worker.log"\n',
            '        log_path = run_folder / "run.log"\n',
            1,
        )

    if 'self.log(f"실행 로그: {log_path}")' not in text:
        anchor = '        self.log(f"작업 기록: {run_folder}")\n'
        if anchor not in text:
            raise RuntimeError("ui.py에서 작업 기록 로그 위치를 찾지 못했습니다.")
        text = text.replace(
            anchor,
            anchor + '        self.log(f"실행 로그: {log_path}")\n',
            1,
        )

    if "for transient in (event_path, snapshot_path):" not in text:
        old_tail = '''                self._post(self._worker_done, code, error)\n            except Exception as exc:\n                self._post(self._worker_failure, str(exc))\n\n        threading.Thread(target=worker, daemon=True).start()\n'''
        new_tail = '''                self._post(self._worker_done, code, error)\n            except Exception as exc:\n                self._post(self._worker_failure, str(exc))\n            finally:\n                # events/settings are IPC files, not retained user logs.\n                for transient in (event_path, snapshot_path):\n                    try:\n                        Path(transient).unlink(missing_ok=True)\n                    except OSError:\n                        pass\n\n        threading.Thread(target=worker, daemon=True).start()\n'''
        text = _replace_once(text, old_tail, new_tail, "UI transient cleanup")

    text = text.replace(
        "이미 입력된 내용이 있을 수 있습니다. 로그와 결과 기록을 확인하세요.",
        "완료된 단계는 유지됩니다. 다시 실행하면 같은 값은 건너뜁니다. 세부 원인은 run.log를 확인하세요.",
    )
    return text


def _patch_full_run_tests(support, text: str) -> str:
    if (
        "self.fetch_character" in text
        and "test_roll20_preflight_happens_before_translation" in text
    ):
        return text
    return support.patch_full_run_tests(text)


def _compile_text(path: Path, text: str):
    try:
        ast.parse(text, filename=str(path))
    except SyntaxError as exc:
        raise RuntimeError(f"Python 구문 검증 실패: {path}: {exc}") from exc


def _atomic_write(path: Path, text: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + ".runtime-v23.tmp")
    temp.write_text(text, encoding="utf-8")
    os.replace(temp, path)


def _backup_v23(path: Path):
    backup = path.with_suffix(path.suffix + ".pre-runtime-v2.3.bak")
    if not backup.exists():
        shutil.copy2(path, backup)


def _sandbox_ignore(_dir, names):
    ignored = set()
    for name in names:
        if name in {".git", ".venv", "venv", "dist", "build", "__pycache__", ".idea"}:
            ignored.add(name)
        elif name == ".roll20_chrome_profile":
            ignored.add(name)
        elif name.endswith(".pyc"):
            ignored.add(name)
    return ignored


def _run_sandbox_tests(patched: dict[Path, str], generated_tests: dict[Path, str]):
    with tempfile.TemporaryDirectory(prefix="sheetmover-runtime-v23-") as temp_dir:
        sandbox = Path(temp_dir) / "repo"
        shutil.copytree(ROOT, sandbox, ignore=_sandbox_ignore)

        for path, content in patched.items():
            relative = path.relative_to(ROOT)
            target = sandbox / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")

        for path, content in generated_tests.items():
            relative = path.relative_to(ROOT)
            target = sandbox / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")

        env = os.environ.copy()
        env["PYTHONPATH"] = str(sandbox) + os.pathsep + env.get("PYTHONPATH", "")
        completed = subprocess.run(
            [sys.executable, "-m", "unittest", "discover", "-s", "tests", "-v"],
            cwd=sandbox,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=300,
        )
        output = completed.stdout or ""
        if completed.returncode != 0:
            tail = "\n".join(output.splitlines()[-120:])
            raise RuntimeError(
                "복제본 전체 테스트가 실패했습니다. 실제 프로젝트는 수정하지 않았습니다.\n"
                + tail
            )

        match = re.search(r"Ran\s+(\d+)\s+tests?", output)
        count = match.group(1) if match else "전체"
        return count


def _generated_tests():
    spell_test = '''import unittest\n\nfrom sheet_mover.roll20_spells import build_spell_plan\n\n\nclass GroupedSpellSourceDamageV23Tests(unittest.TestCase):\n    def test_grouped_class_spell_keeps_moonbeam_damage_and_upcast(self):\n        payload = {\n            "raw_source": {\n                "classSpells": [],\n                "spells": {\n                    "class": [{\n                        "id": 8180449,\n                        "definition": {\n                            "id": 2619134,\n                            "name": "Moonbeam",\n                            "level": 2,\n                            "requiresSavingThrow": True,\n                            "requiresAttackRoll": False,\n                            "saveDcAbilityId": 3,\n                            "description": "On a successful save, a creature takes half as much damage.",\n                            "modifiers": [{\n                                "type": "damage",\n                                "subType": "radiant",\n                                "friendlySubtypeName": "Radiant",\n                                "restriction": "",\n                                "die": {"diceCount": 2, "diceValue": 10, "diceString": "2d10"},\n                                "atHigherLevels": {\n                                    "higherLevelDefinitions": [{\n                                        "level": 1,\n                                        "dice": {\n                                            "diceCount": 1,\n                                            "diceValue": 10,\n                                            "fixedValue": 0,\n                                            "diceString": "1d10",\n                                        },\n                                    }]\n                                },\n                            }],\n                        },\n                    }],\n                },\n            },\n            "roll20_payload": {\n                "source_character_id": "153714540",\n                "character": {\n                    "name": "견본2",\n                    "spellcasting": {"class_rules_source": [], "spell_slots_source": []},\n                },\n                "spells": [{\n                    "source_key": "spell:8180449",\n                    "source_id": "8180449",\n                    "definition_id": "2619134",\n                    "name": "달빛 (Moonbeam)",\n                    "original_name": "Moonbeam",\n                    "description": "<p>달빛 설명</p>",\n                    "level": 2,\n                    "prepared": False,\n                    "always_prepared": True,\n                    "uses_spell_slot": True,\n                    "casting_time": {"activationTime": 1, "activationType": 1},\n                    "range": {"origin": "Ranged", "rangeValue": 120, "aoeType": "Cylinder", "aoeValue": 5},\n                    "duration": {"durationInterval": 1, "durationUnit": "Minute", "durationType": "Concentration"},\n                    "components": [1, 2, 3],\n                    "components_description": "a moonseed leaf",\n                    "school": "Evocation",\n                    "ritual": False,\n                    "concentration": True,\n                    "save_dc_ability_id": 3,\n                    "attack_type": None,\n                    "counts_as_known_spell": False,\n                }],\n            },\n        }\n        row = build_spell_plan(payload)["rows"][0]\n        fields = row["fields"]\n        self.assertEqual(fields["spelldamage"], "2d10")\n        self.assertEqual(fields["spelldamagetype"], "Radiant")\n        self.assertEqual(fields["spellsave"], "Constitution")\n        self.assertEqual(fields["spellsavesuccess"], "성공 시 절반 피해")\n        self.assertEqual(fields["spellhldie"], "1")\n        self.assertEqual(fields["spellhldietype"], "d10")\n\n\nif __name__ == "__main__":\n    unittest.main()\n'''

    prof_test = '''import unittest\n\nfrom sheet_mover.roll20_proficiencies import (\n    STAGE11_NO_WAIT_ATTR_SCRIPT,\n    WRITE_BATCH_SIZE,\n    _attribute_batches,\n)\n\n\nclass Stage11ResilientWriterV23Tests(unittest.TestCase):\n    def test_batch_size_and_no_wait_repair_exist(self):\n        attrs = {f"f{i}": {"current": str(i), "max": ""} for i in range(35)}\n        self.assertEqual([len(x) for x in _attribute_batches(attrs)], [16, 16, 3])\n        self.assertEqual(WRITE_BATCH_SIZE, 16)\n        self.assertIn("{wait:false}", STAGE11_NO_WAIT_ATTR_SCRIPT)\n\n\nif __name__ == "__main__":\n    unittest.main()\n'''

    cache_test = '''import json\nfrom pathlib import Path\nimport tempfile\nimport unittest\nfrom unittest.mock import patch\n\nfrom sheet_mover.full_run import _find_reusable_result\n\n\nclass RuntimePreflightCacheV23Tests(unittest.TestCase):\n    def test_exact_raw_source_can_reuse_partial_translation(self):\n        raw = {"id": 153714540, "name": "견본2", "classes": [{"level": 1}]}\n        with tempfile.TemporaryDirectory() as folder:\n            root = Path(folder)\n            current = root / "results" / "current"\n            current.mkdir(parents=True)\n            path = current / "sheet-result-153714540-20261007-144601.json"\n            path.write_text(json.dumps({\n                "original": {"source_id": "153714540", "name": "견본2"},\n                "raw_source": raw,\n                "translation_summary": {"status": "partial", "original_preserved_count": 1},\n                "roll20_payload": {"source_character_id": "153714540", "character": {"name": "견본2"}},\n            }, ensure_ascii=False), encoding="utf-8")\n            with patch("sheet_mover.full_run.data_dir", return_value=root):\n                payload, found = _find_reusable_result("153714540", raw)\n            self.assertIsNotNone(payload)\n            self.assertEqual(found, path)\n\n    def test_changed_raw_source_is_not_reused(self):\n        raw = {"id": 1, "name": "fixture", "value": 2}\n        with tempfile.TemporaryDirectory() as folder:\n            root = Path(folder)\n            current = root / "results" / "current"\n            current.mkdir(parents=True)\n            path = current / "sheet-result-1-20261007-000000.json"\n            path.write_text(json.dumps({\n                "original": {"source_id": "1", "name": "fixture"},\n                "raw_source": {"id": 1, "name": "fixture", "value": 1},\n                "translation_summary": {"status": "complete"},\n                "roll20_payload": {"source_character_id": "1", "character": {"name": "fixture"}},\n            }), encoding="utf-8")\n            with patch("sheet_mover.full_run.data_dir", return_value=root):\n                payload, found = _find_reusable_result("1", raw)\n            self.assertIsNone(payload)\n            self.assertIsNone(found)\n\n\nif __name__ == "__main__":\n    unittest.main()\n'''

    return {
        ROOT / "tests" / "test_stage7_grouped_spell_sources_v23.py": spell_test,
        ROOT / "tests" / "test_stage11_resilient_writer_v23.py": prof_test,
        ROOT / "tests" / "test_runtime_preflight_cache_v23.py": cache_test,
    }


def build_patch():
    support = _load_support()
    required = (SPELLS, PROFS, FULL_RUN, MOVER, UI, TEST_FULL_RUN)
    missing = [str(path) for path in required if not path.is_file()]
    if missing:
        raise RuntimeError("필수 파일이 없습니다: " + ", ".join(missing))

    source_info = {}
    source = {}
    for path in required:
        text, origin = _base_text(path)
        source[path] = text
        source_info[path] = origin

    spells = support.patch_spells(source[SPELLS])
    profs = _patch_proficiencies(support, source[PROFS])
    mover = support.patch_mover(source[MOVER])

    full_run = source[FULL_RUN]
    if "stage13-full-run-v2.2-roll20-preflight" not in full_run:
        full_run = support.patch_full_run(full_run)
    full_run = _patch_full_run_v23(full_run)

    ui = _patch_ui(source[UI])
    test_full_run = _patch_full_run_tests(support, source[TEST_FULL_RUN])

    patched = {
        SPELLS: spells,
        PROFS: profs,
        FULL_RUN: full_run,
        MOVER: mover,
        UI: ui,
        TEST_FULL_RUN: test_full_run,
    }
    generated_tests = _generated_tests()

    for path, content in {**patched, **generated_tests}.items():
        _compile_text(path, content)

    return patched, generated_tests, source_info


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--no-tests", action="store_true", help="복제본 전체 테스트를 생략합니다.")
    args = parser.parse_args()

    print("[시트 이동기] runtime-integrity v2.3 준비")
    print(f"- 기준 커밋: {BASE_COMMIT}")
    print("- 깨진 v2.2 Stage 11 소스 자동 복구")
    print("- Moonbeam grouped spell damage 유지")
    print("- Roll20 준비 확인 전에는 번역 시작 안 함")
    print("- 같은 D&D Beyond 원본이면 기존 번역 결과 재사용(API 번역 생략)")
    print("- 실행 폴더에는 종료 후 run.log 하나만 유지")

    patched, generated_tests, source_info = build_patch()

    if not args.no_tests:
        print("[시트 이동기] 실제 프로젝트를 건드리기 전에 복제본 전체 테스트를 실행합니다...")
        count = _run_sandbox_tests(patched, generated_tests)
        print(f"[시트 이동기] 복제본 전체 테스트 통과: {count}개")

    for path in patched:
        _backup_v23(path)
    for path, content in patched.items():
        _atomic_write(path, content)
    for path, content in generated_tests.items():
        _atomic_write(path, content)

    # Final syntax verification on the real tree; no imports or external calls.
    for path in list(patched) + list(generated_tests):
        _compile_text(path, path.read_text(encoding="utf-8"))

    print("[시트 이동기] runtime-integrity v2.3 적용 완료")
    for path, origin in source_info.items():
        if origin.endswith(".pre-runtime-v2.2.bak"):
            print(f"- {path.name}: 깨진 v2.2 직전 백업을 기준으로 재구성")
    print("- 다음 실행에서 DDB 원본이 직전 결과와 같으면 Google/Ollama 번역 호출을 건너뜁니다.")
    print("- 이제 바로: python main.py")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"[시트 이동기] runtime-integrity v2.3 실패: {exc}", file=sys.stderr)
        raise
