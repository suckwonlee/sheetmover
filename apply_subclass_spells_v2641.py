# -*- coding: utf-8 -*-
from __future__ import annotations

import ast
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile


ROOT = Path.cwd()
PACKAGE_ROOT = Path(__file__).resolve().parent
PAYLOAD_ROOT = PACKAGE_ROOT / "v264_payload"

SOURCE = Path("sheet_mover/source.py")
ROLL20_PAYLOAD = Path("sheet_mover/roll20_payload.py")
ROLL20_SPELLS = Path("sheet_mover/roll20_spells.py")
ROLL20_PROFICIENCIES = Path("sheet_mover/roll20_proficiencies.py")
FULL_RUN = Path("sheet_mover/full_run.py")
NEW_RUNTIME = Path("sheet_mover/runtime_integrity_v264.py")
NEW_SUBCLASS = Path("sheet_mover/subclass_spells.py")

NEW_TESTS = (
    Path("tests/test_subclass_spells_v264.py"),
    Path("tests/test_runtime_integrity_v264.py"),
    Path("tests/test_subclass_spell_source_model_v264.py"),
    Path("tests/test_proficiency_integrity_v2641.py"),
)

PATCH_TARGETS = (
    SOURCE,
    ROLL20_PAYLOAD,
    ROLL20_SPELLS,
    ROLL20_PROFICIENCIES,
    FULL_RUN,
    NEW_RUNTIME,
    NEW_SUBCLASS,
    *NEW_TESTS,
)


def parse(path: Path):
    ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def replace_exact(path: Path, old: str, new: str, label: str):
    text = path.read_text(encoding="utf-8")
    if new in text:
        return False
    if old not in text:
        raise RuntimeError(f"{label} 위치를 찾지 못했습니다: {path}")
    text = text.replace(old, new, 1)
    ast.parse(text, filename=str(path))
    path.write_text(text, encoding="utf-8")
    return True


def copy_payload(root: Path):
    for relative in (NEW_RUNTIME, NEW_SUBCLASS, *NEW_TESTS):
        src = PAYLOAD_ROOT / relative
        if not src.is_file():
            raise RuntimeError(f"패치 payload 파일 없음: {src}")
        dst = root / relative
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        parse(dst)


def patch_source(root: Path):
    path = root / SOURCE
    text = path.read_text(encoding="utf-8")

    old_spell = '''def _normalize_spell(entry, source_kind="spell"):
    item = _definition(entry, source_kind)
    if not item:
        return None

    definition = _dict(entry.get("definition"))
    item.update(
        level=definition.get("level"),
        prepared=entry.get("prepared"),
        always_prepared=entry.get("alwaysPrepared"),
        uses_spell_slot=entry.get("usesSpellSlot"),
        casting_time=deepcopy(definition.get("activation")),
        range=deepcopy(definition.get("range")),
        duration=deepcopy(definition.get("duration")),
        components=deepcopy(definition.get("components")),
        components_description=definition.get("componentsDescription") or "",
        school=definition.get("school"),
        ritual=definition.get("ritual"),
        concentration=definition.get("concentration"),
        save_dc_ability_id=definition.get("saveDcAbilityId"),
        attack_type=definition.get("attackType"),
        damage_effect=deepcopy(definition.get("damageEffect")),
        counts_as_known_spell=entry.get("countsAsKnownSpell"),
        spellcasting_ability_id=entry.get("spellCastingAbilityId"),
        cast_only_as_ritual=entry.get("castOnlyAsRitual"),
        ritual_casting_type=entry.get("ritualCastingType"),
        restriction=entry.get("restriction"),
        display_as_attack=entry.get("displayAsAttack"),
    )
    return item
'''
    new_spell = '''def _structured_spell_grant_type(entry):
    if not isinstance(entry, dict):
        return "structured"
    if entry.get("alwaysPrepared") is True:
        return "always_prepared"
    if entry.get("countsAsKnownSpell") is True:
        return "known"
    if entry.get("prepared") is True:
        return "prepared"
    return "structured"


def _normalize_spell(
    entry,
    source_kind="spell",
    *,
    source_group="",
    character_class_id="",
):
    item = _definition(entry, source_kind)
    if not item:
        return None

    definition = _dict(entry.get("definition"))
    legacy = definition.get("isLegacy")
    if not isinstance(legacy, bool):
        legacy = None

    item.update(
        level=definition.get("level"),
        prepared=entry.get("prepared"),
        always_prepared=entry.get("alwaysPrepared"),
        uses_spell_slot=entry.get("usesSpellSlot"),
        casting_time=deepcopy(definition.get("activation")),
        range=deepcopy(definition.get("range")),
        duration=deepcopy(definition.get("duration")),
        components=deepcopy(definition.get("components")),
        components_description=definition.get("componentsDescription") or "",
        school=definition.get("school"),
        ritual=definition.get("ritual"),
        concentration=definition.get("concentration"),
        save_dc_ability_id=definition.get("saveDcAbilityId"),
        attack_type=definition.get("attackType"),
        damage_effect=deepcopy(definition.get("damageEffect")),
        counts_as_known_spell=entry.get("countsAsKnownSpell"),
        spellcasting_ability_id=entry.get("spellCastingAbilityId"),
        cast_only_as_ritual=entry.get("castOnlyAsRitual"),
        ritual_casting_type=entry.get("ritualCastingType"),
        restriction=entry.get("restriction"),
        display_as_attack=entry.get("displayAsAttack"),
        source_group=str(source_group or ""),
        character_class_id=str(character_class_id or ""),
        component_id=str(entry.get("componentId") or ""),
        component_type_id=str(entry.get("componentTypeId") or ""),
        definition_is_legacy=legacy,
        grant_type=_structured_spell_grant_type(entry),
    )
    return item
'''
    if "def _structured_spell_grant_type" not in text:
        replace_exact(path, old_spell, new_spell, "구조화 주문 메타데이터")

    text = path.read_text(encoding="utf-8")
    old_loop = '''    spell_groups = list(_dict(data.get("spells")).values())
    spell_groups += [
        _list(character_class.get("spells"))
        for character_class in _list(data.get("classSpells"))
        if isinstance(character_class, dict)
    ]
    for group in spell_groups:
        for entry in _list(group):
            item = _normalize_spell(entry)
            if item:
                sheet.spells.append(item)
'''
    new_loop = '''    for group_name, group in _dict(data.get("spells")).items():
        for entry in _list(group):
            item = _normalize_spell(
                entry,
                source_group=str(group_name or ""),
            )
            if item:
                sheet.spells.append(item)

    for class_spell_group in _list(data.get("classSpells")):
        if not isinstance(class_spell_group, dict):
            continue
        character_class_id = class_spell_group.get("characterClassId")
        for entry in _list(class_spell_group.get("spells")):
            item = _normalize_spell(
                entry,
                source_group="classSpells",
                character_class_id=character_class_id,
            )
            if item:
                sheet.spells.append(item)
'''
    if new_loop not in text:
        replace_exact(path, old_loop, new_loop, "주문 source group 보존")

    text = path.read_text(encoding="utf-8")
    hook = '''# runtime-integrity-v2.6.3 selected-option hook
from .runtime_integrity_v263 import install_source_integrity as _install_source_integrity_v263
normalize_character = _install_source_integrity_v263(normalize_character)
'''
    new_hook = hook + '''
# subclass-spells-v2.6.4 source hook
from .subclass_spells import install_source_integrity as _install_subclass_spells_v264
normalize_character = _install_subclass_spells_v264(normalize_character)
'''
    if "subclass-spells-v2.6.4 source hook" not in text:
        replace_exact(path, hook, new_hook, "v2.6.4 source hook")


def patch_roll20_payload(root: Path):
    path = root / ROLL20_PAYLOAD
    old = '''_SPELL_FIELDS = (
    "description", "level", "prepared", "always_prepared", "uses_spell_slot",
    "casting_time", "range", "duration", "components", "components_description",
    "school", "ritual", "concentration", "save_dc_ability_id", "attack_type",
    "damage_effect", "counts_as_known_spell", "spellcasting_ability_id",
    "cast_only_as_ritual", "ritual_casting_type", "restriction",
    "display_as_attack",
)
'''
    new = '''_SPELL_FIELDS = (
    "description", "level", "prepared", "always_prepared", "uses_spell_slot",
    "casting_time", "range", "duration", "components", "components_description",
    "school", "ritual", "concentration", "save_dc_ability_id", "attack_type",
    "damage_effect", "counts_as_known_spell", "spellcasting_ability_id",
    "cast_only_as_ritual", "ritual_casting_type", "restriction",
    "display_as_attack", "source_group", "character_class_id", "component_id",
    "component_type_id", "definition_is_legacy", "grant_type",
    "grant_feature_id", "grant_feature_name",
)
'''
    replace_exact(path, old, new, "Stage 3 주문 메타데이터")


def patch_roll20_spells(root: Path):
    path = root / ROLL20_SPELLS
    text = path.read_text(encoding="utf-8")
    text = text.replace(
        'STAGE7_VERSION = "2026-10-07-stage7-roll20-spells-v2.1-grouped-source-damage"',
        'STAGE7_VERSION = "2026-10-08-stage7-roll20-spells-v2.2-subclass-grants"',
        1,
    )
    path.write_text(text, encoding="utf-8")

    old_raw_tail = '''    grouped = raw_source.get("spells")
    if isinstance(grouped, dict):
        for entries in grouped.values():
            add_spells(entries)
    else:
        add_spells(grouped)

    return out
'''
    new_raw_tail = '''    grouped = raw_source.get("spells")
    if isinstance(grouped, dict):
        for entries in grouped.values():
            add_spells(entries)
    else:
        add_spells(grouped)

    # Feature-table fallback spells do not exist as character spell objects in
    # raw_source. Their exact DDB catalog definitions are preserved by the
    # source resolver under calculation_inputs for Stage 7 combat mapping.
    original = _dict(_dict(result_payload).get("original"))
    calculation_inputs = _dict(original.get("calculation_inputs"))
    resolution = _dict(calculation_inputs.get("subclass_spell_resolution"))
    for source_id, definition in _dict(resolution.get("resolved_definitions")).items():
        source_id = _text(source_id)
        definition = _dict(definition)
        if source_id and definition and source_id not in out:
            out[source_id] = definition

    return out
'''
    text = path.read_text(encoding="utf-8")
    if "Feature-table fallback spells do not exist" not in text:
        replace_exact(path, old_raw_tail, new_raw_tail, "Stage 7 synthetic raw definition")

    old_return = '''    return {
        "source_key": source_key,
        "source_id": _text(item.get("source_id")),
        "definition_id": _text(item.get("definition_id")),
        "level": level,
        "section": section,
        "row_id": row_id,
        "name": name,
        "source_save_ability": _ABILITY_BY_ID.get(item.get("save_dc_ability_id"), ""),
        "source_attack_type": item.get("attack_type"),
        "combat": combat,
        "fields": fields,
    }
'''
    new_return = '''    return {
        "source_key": source_key,
        "source_id": _text(item.get("source_id")),
        "definition_id": _text(item.get("definition_id")),
        "source_group": _text(item.get("source_group")),
        "character_class_id": _text(item.get("character_class_id")),
        "component_id": _text(item.get("component_id")),
        "component_type_id": _text(item.get("component_type_id")),
        "definition_is_legacy": item.get("definition_is_legacy"),
        "grant_type": _text(item.get("grant_type")),
        "grant_feature_id": _text(item.get("grant_feature_id")),
        "grant_feature_name": _text(item.get("grant_feature_name")),
        "level": level,
        "section": section,
        "row_id": row_id,
        "name": name,
        "source_save_ability": _ABILITY_BY_ID.get(item.get("save_dc_ability_id"), ""),
        "source_attack_type": item.get("attack_type"),
        "combat": combat,
        "fields": fields,
    }
'''
    replace_exact(path, old_return, new_return, "Stage 7 주문 provenance report")



def patch_roll20_proficiencies(root: Path):
    path = root / ROLL20_PROFICIENCIES
    text = path.read_text(encoding="utf-8")

    text = text.replace(
        'STAGE11_VERSION = "2026-10-07-stage11-roll20-proficiencies-v2.3-resilient-batches"',
        'STAGE11_VERSION = "2026-10-08-stage11-roll20-proficiencies-v2.4-source-integrity"',
        1,
    )

    old_languages = """def _translated_languages(result_payload):
    translated = _translated_character(result_payload)
    original = _original_character(result_payload)

    translated_values = [
        _text(v) for v in _list(translated.get("languages")) if _text(v)
    ]
    if translated_values:
        return translated_values

    return [
        _text(v) for v in _list(original.get("languages")) if _text(v)
    ]
"""
    new_languages = """def _translated_languages(result_payload):
    translated = _translated_character(result_payload)
    original = _original_character(result_payload)

    original_raw = _list(original.get("languages"))
    translated_raw = _list(translated.get("languages"))
    original_values = [_text(v) for v in original_raw if _text(v)]

    # A partially translated list must never make a source language disappear.
    # Use translated values only when the list shape still matches DDB.
    if original_raw and len(translated_raw) == len(original_raw):
        resolved = []
        for original_value, translated_value in zip(original_raw, translated_raw):
            value = _text(translated_value) or _text(original_value)
            if value:
                resolved.append(value)
        if len(resolved) == len(original_values):
            return resolved

    if not original_values:
        return [_text(v) for v in translated_raw if _text(v)]

    return original_values
"""
    if new_languages not in text:
        if old_languages not in text:
            raise RuntimeError("Stage 11 언어 fallback 위치를 찾지 못했습니다.")
        text = text.replace(old_languages, new_languages, 1)

    integrity_helper = """

def _source_non_skill_integrity(result_payload, tools, other_proficiencies):
    original = _original_character(result_payload)

    expected_tools = set()
    expected_other = set()
    for entry in _list(original.get("proficiency_entries")):
        entry = _dict(entry)
        category = _classify_non_skill_entry(entry)
        if not category:
            continue
        key = _entry_key(entry)
        if not key or key.startswith("choose_a_"):
            continue
        if category == "TOOL":
            expected_tools.add(key)
        else:
            expected_other.add((category, key))

    actual_tools = {
        _text(row.get("key"))
        for row in _list(tools)
        if _text(row.get("key"))
    }
    actual_other = {
        (_text(row.get("prof_type")), _text(row.get("key")))
        for row in _list(other_proficiencies)
        if _text(row.get("prof_type")) != "LANGUAGE"
        and _text(row.get("key"))
    }

    expected_languages = sorted(_translated_languages(result_payload))
    actual_languages = sorted(
        _text(row.get("name"))
        for row in _list(other_proficiencies)
        if _text(row.get("prof_type")) == "LANGUAGE"
        and _text(row.get("name"))
    )

    mismatches = []
    if expected_tools != actual_tools:
        mismatches.append({
            "kind": "tools",
            "expected": sorted(expected_tools),
            "actual": sorted(actual_tools),
        })
    if expected_other != actual_other:
        mismatches.append({
            "kind": "non_skill_proficiencies",
            "expected": sorted([list(value) for value in expected_other]),
            "actual": sorted([list(value) for value in actual_other]),
        })
    if expected_languages != actual_languages:
        mismatches.append({
            "kind": "languages",
            "expected": expected_languages,
            "actual": actual_languages,
        })

    if mismatches:
        raise RuntimeError(
            "11단계 D&D Beyond 숙련/언어 source→plan 무결성 실패: "
            + json.dumps(mismatches, ensure_ascii=False)
        )

    return {
        "status": "pass",
        "source_language_count": len(expected_languages),
        "planned_language_count": len(actual_languages),
        "source_tool_count": len(expected_tools),
        "planned_tool_count": len(actual_tools),
        "source_other_proficiency_count": len(expected_other),
        "planned_other_proficiency_count": len(actual_other),
    }
"""
    anchor = "\n\ndef build_proficiency_plan(result_payload: dict[str, Any]) -> dict[str, Any]:\n"
    if '_source_non_skill_integrity' not in text:
        if anchor not in text:
            raise RuntimeError("Stage 11 source integrity 삽입 위치를 찾지 못했습니다.")
        text = text.replace(anchor, integrity_helper + anchor, 1)

    old_build = """    tools, other_proficiencies = _non_skill_proficiency_rows(
        result_payload,
        pb,
    )

    return {
"""
    new_build = """    tools, other_proficiencies = _non_skill_proficiency_rows(
        result_payload,
        pb,
    )
    source_integrity = _source_non_skill_integrity(
        result_payload,
        tools,
        other_proficiencies,
    )

    return {
"""
    if new_build not in text:
        if old_build not in text:
            raise RuntimeError("Stage 11 plan integrity 호출 위치를 찾지 못했습니다.")
        text = text.replace(old_build, new_build, 1)

    old_field = """        "other_proficiencies": other_proficiencies,
        "skill_proficiency_count": sum(1 for row in skills if row["proficient"]),
"""
    new_field = """        "other_proficiencies": other_proficiencies,
        "source_integrity": source_integrity,
        "skill_proficiency_count": sum(1 for row in skills if row["proficient"]),
"""
    if new_field not in text:
        if old_field not in text:
            raise RuntimeError("Stage 11 integrity report 위치를 찾지 못했습니다.")
        text = text.replace(old_field, new_field, 1)

    ast.parse(text, filename=str(path))
    path.write_text(text, encoding="utf-8")

def patch_full_run(root: Path):
    path = root / FULL_RUN
    old = '''# runtime-integrity-v2.6.2 cache-refresh hook
from .runtime_integrity_v262 import install_reusable_result_refresh as _install_reusable_result_refresh_v262
_find_reusable_result = _install_reusable_result_refresh_v262(_find_reusable_result)

# single-current-log-v1
'''
    new = '''# runtime-integrity-v2.6.2 cache-refresh hook
from .runtime_integrity_v262 import install_reusable_result_refresh as _install_reusable_result_refresh_v262
_find_reusable_result = _install_reusable_result_refresh_v262(_find_reusable_result)

# runtime-integrity-v2.6.4 subclass-spell cache-refresh hook
from .runtime_integrity_v264 import install_reusable_result_refresh as _install_reusable_result_refresh_v264
_find_reusable_result = _install_reusable_result_refresh_v264(_find_reusable_result)

# single-current-log-v1
'''
    replace_exact(path, old, new, "v2.6.4 cache refresh hook")


def apply_tree(root: Path):
    required = (SOURCE, ROLL20_PAYLOAD, ROLL20_SPELLS, ROLL20_PROFICIENCIES, FULL_RUN)
    for relative in required:
        if not (root / relative).is_file():
            raise RuntimeError(f"필수 프로젝트 파일 없음: {root / relative}")

    copy_payload(root)
    patch_source(root)
    patch_roll20_payload(root)
    patch_roll20_spells(root)
    patch_roll20_proficiencies(root)
    patch_full_run(root)

    for relative in PATCH_TARGETS:
        parse(root / relative)


def _ignore_copy(_path, names):
    ignored = set()
    for name in names:
        if name in {
            ".git", ".venv", "__pycache__", "results", "output",
            "output_archive", ".roll20_chrome_profile", "stage4_assets",
        }:
            ignored.add(name)
        elif name.endswith(".pyc") or name.endswith(".pyo"):
            ignored.add(name)
    return ignored


def run_tests(root: Path):
    env = os.environ.copy()
    env["PYTHONPATH"] = str(root) + os.pathsep + env.get("PYTHONPATH", "")
    completed = subprocess.run(
        [sys.executable, "-m", "unittest", "discover", "-s", "tests", "-v"],
        cwd=root,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=540,
    )
    output = completed.stdout or ""
    if completed.returncode != 0:
        raise RuntimeError(
            "복제본 전체 테스트 실패. 실제 프로젝트는 수정하지 않았습니다.\n"
            + "\n".join(output.splitlines()[-420:])
        )
    match = re.search(r"Ran\s+(\d+)\s+tests?", output)
    return int(match.group(1)) if match else None


def backup(path: Path):
    if not path.exists():
        return
    target = path.with_suffix(path.suffix + ".pre-v2.6.4.1.bak")
    if not target.exists():
        shutil.copy2(path, target)


def install_from_sandbox(sandbox: Path):
    for relative in PATCH_TARGETS:
        src = sandbox / relative
        dst = ROOT / relative
        backup(dst)
        dst.parent.mkdir(parents=True, exist_ok=True)
        tmp = dst.with_name(dst.name + ".v2641.tmp")
        shutil.copy2(src, tmp)
        os.replace(tmp, dst)
        parse(dst)


def main():
    print("[시트 이동기] v2.6.4.1 주문 + 숙련/언어 무결성 패치 준비")
    print("- 언어/도구/무기/방어구 숙련 source→plan 누락 방지")
    print("- 번역 언어 배열이 불완전하면 DDB 원문 목록으로 안전 fallback")
    print("- 구조화 spell source/component/판본/부여 방식 보존")
    print("- Oath/Domain 등 always-prepared feature table 보완")
    print("- Expanded Spell List 자동 추가 금지")
    print("- D&D Beyond 실제 spell definition만 사용 · 모호하면 미추가")
    print("- 기존 번역 캐시 재사용 · 새 주문만 영어 원문 유지 가능")
    print("- 실제 파일 수정 전 복제본 전체 unittest 실행")

    if not (ROOT / SOURCE).is_file():
        raise RuntimeError("시트 이동기 프로젝트 폴더에서 실행해 주세요.")
    if not PAYLOAD_ROOT.is_dir():
        raise RuntimeError(f"패치 payload 폴더가 없습니다: {PAYLOAD_ROOT}")

    with tempfile.TemporaryDirectory(prefix="sheetmover-v264-") as temp:
        sandbox = Path(temp) / "repo"
        shutil.copytree(ROOT, sandbox, ignore=_ignore_copy)
        apply_tree(sandbox)

        print("[시트 이동기] 복제본 전체 unittest 실행...")
        count = run_tests(sandbox)
        print(
            "[시트 이동기] 복제본 전체 테스트 통과"
            + (f": {count}개" if count is not None else "")
        )

        install_from_sandbox(sandbox)

    print("[시트 이동기] v2.6.4.1 패치 적용 완료")
    print("- Stage 11 언어/숙련 source 무결성 검증을 포함했습니다.")
    print("- 다음 기존 Paladin 실행에서는 맹세 주문 표의 현재 레벨 항목을 보완합니다.")
    print("- 5e Warlock Expanded Spell List는 실제 Known Spell만 유지합니다.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"[시트 이동기] v2.6.4 패치 실패: {exc}", file=sys.stderr)
        raise
