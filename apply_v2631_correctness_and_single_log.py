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
PAYLOAD = Path(__file__).resolve().parent / "payload"

FILES = {
    "run_log": Path("sheet_mover/run_log.py"),
    "v263": Path("sheet_mover/runtime_integrity_v263.py"),
    "test_log": Path("tests/test_single_current_log.py"),
    "test_v263": Path("tests/test_runtime_integrity_v263.py"),
    "test_resources": Path("tests/test_roll20_resources_v14.py"),
    "test_basic": Path("tests/test_stage5_correctness_v31.py"),
}

SOURCE = Path("sheet_mover/source.py")
FULL_RUN = Path("sheet_mover/full_run.py")
RESULT_STORE = Path("sheet_mover/result_store.py")
PROF = Path("sheet_mover/roll20_proficiencies.py")
RESOURCES = Path("sheet_mover/roll20_resources.py")
BASIC = Path("stage5_basic_writer_v2.py")
WORKER_TEST = Path("tests/test_worker_protocol.py")

V263_MARKER = "# runtime-integrity-v2.6.3 selected-option hook"
SINGLE_LOG_MARKER = "# single-current-log-v1"
BASIC_MARKER = "# stage5-correctness-v3.1"


def parse(path):
    ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def ignore(_dir, names):
    result = set()
    for name in names:
        if name in {
            ".git", ".venv", "venv", "dist", "build",
            "__pycache__", ".idea", ".roll20_chrome_profile",
        }:
            result.add(name)
        elif name.endswith(".pyc"):
            result.add(name)
    return result


def replace_exact(path, old, new, label):
    text = path.read_text(encoding="utf-8")
    if new in text:
        return False
    count = text.count(old)
    if count != 1:
        raise RuntimeError(
            f"{path}: {label} 교체 위치가 {count}개입니다. "
            "예상한 현재 프로젝트와 달라 실제 파일을 수정하지 않습니다."
        )
    patched = text.replace(old, new, 1)
    ast.parse(patched, filename=str(path))
    path.write_text(patched, encoding="utf-8")
    return True


def copy_payload(root):
    for relative in FILES.values():
        src = PAYLOAD / relative
        dst = root / relative
        if not src.is_file():
            raise RuntimeError(f"payload 파일 없음: {src}")
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        parse(dst)


def patch_source(root):
    path = root / SOURCE
    text = path.read_text(encoding="utf-8")
    if V263_MARKER in text:
        return

    anchor = '''# runtime-integrity-v2.6.2 source hook
from .runtime_integrity_v262 import install_source_integrity as _install_source_integrity_v262
normalize_character = _install_source_integrity_v262(normalize_character)
'''
    if anchor not in text:
        raise RuntimeError(
            "source.py에서 적용된 v2.6.2 hook을 찾지 못했습니다."
        )

    replacement = anchor + '''
# runtime-integrity-v2.6.3 selected-option hook
from .runtime_integrity_v263 import install_source_integrity as _install_source_integrity_v263
normalize_character = _install_source_integrity_v263(normalize_character)
'''
    text = text.replace(anchor, replacement, 1)
    ast.parse(text, filename=str(path))
    path.write_text(text, encoding="utf-8")


def patch_single_log(root):
    full = root / FULL_RUN
    store = root / RESULT_STORE
    prof = root / PROF
    resources = root / RESOURCES

    full_text = full.read_text(encoding="utf-8")
    if 'data_dir() / "results" / "cache"' not in full_text:
        old = '''    # Reuse only a result produced from the exact same D&D Beyond raw payload.
    folder = data_dir() / "results" / "current"
    if not folder.is_dir():
        return None, None

    wanted_raw = _stable_json(raw_source)
    if not wanted_raw:
        return None, None

    candidates = sorted(
        folder.glob(f"sheet-result-{source_id}-*.json"),
        key=lambda path: path.name,
        reverse=True,
    )
'''
        new = '''    # Reuse only a result produced from the exact same D&D Beyond raw payload.
    # results/current is reserved for one consolidated execution log.
    folders = [
        data_dir() / "results" / "cache",
        data_dir() / "results" / "current",
    ]

    wanted_raw = _stable_json(raw_source)
    if not wanted_raw:
        return None, None

    candidates = []
    for folder in folders:
        if not folder.is_dir():
            continue
        candidates.extend(
            folder.glob(f"sheet-result-{source_id}-*.json")
        )
    candidates = sorted(
        candidates,
        key=lambda path: path.name,
        reverse=True,
    )
'''
        replace_exact(full, old, new, "번역 캐시 검색 경로")

    full_text = full.read_text(encoding="utf-8")
    if SINGLE_LOG_MARKER not in full_text:
        anchor = '''# runtime-integrity-v2.6.2 cache-refresh hook
from .runtime_integrity_v262 import install_reusable_result_refresh as _install_reusable_result_refresh_v262
_find_reusable_result = _install_reusable_result_refresh_v262(_find_reusable_result)

if __name__ == "__main__":
'''
        replacement = '''# runtime-integrity-v2.6.2 cache-refresh hook
from .runtime_integrity_v262 import install_reusable_result_refresh as _install_reusable_result_refresh_v262
_find_reusable_result = _install_reusable_result_refresh_v262(_find_reusable_result)

# single-current-log-v1
from .run_log import install_single_current_log as _install_single_current_log_v1
run_full_move = _install_single_current_log_v1(run_full_move)

if __name__ == "__main__":
'''
        if anchor not in full_text:
            raise RuntimeError(
                "full_run.py에서 v2.6.2 cache hook을 찾지 못했습니다."
            )
        full_text = full_text.replace(anchor, replacement, 1)
        ast.parse(full_text, filename=str(full))
        full.write_text(full_text, encoding="utf-8")

    store_text = store.read_text(encoding="utf-8")
    if 'data_root / "results" / "cache"' not in store_text:
        old = '''    seen: set[Path] = set()
    folders = [data_root / "results" / "current"]

    # Backward compatibility for the source checkout layout.
'''
        new = '''    seen: set[Path] = set()
    folders = [
        data_root / "results" / "cache",
        data_root / "results" / "current",
    ]

    # Backward compatibility for the source checkout layout.
'''
        replace_exact(store, old, new, "result_store 캐시 검색")

    prof_text = prof.read_text(encoding="utf-8")
    if "previous_stage_report" not in prof_text:
        old = '''def _previous_managed_rows(output_path):
    if not output_path.is_file():
        return {"tool": [], "proficiencies": []}
    try:
        previous = json.loads(output_path.read_text(encoding="utf-8"))
    except Exception:
        return {"tool": [], "proficiencies": []}
    rows = _dict(previous.get("managed_repeating_rows"))
'''
        new = '''def _previous_managed_rows(output_path):
    if output_path.is_file():
        try:
            previous = json.loads(output_path.read_text(encoding="utf-8"))
        except Exception:
            previous = {}
    else:
        try:
            from .run_log import previous_stage_report
            previous = previous_stage_report(
                output_path.parent,
                "proficiencies",
            )
        except Exception:
            previous = {}
    rows = _dict(previous.get("managed_repeating_rows"))
'''
        replace_exact(prof, old, new, "숙련 이전 관리행 fallback")

    res_text = resources.read_text(encoding="utf-8")
    if "previous_stage_report" not in res_text:
        old = '''def _previous_report(output_path):
    if not output_path.is_file():
        return {}
    try:
        return json.loads(output_path.read_text(encoding="utf-8"))
    except Exception:
        return {}
'''
        new = '''def _previous_report(output_path):
    if output_path.is_file():
        try:
            return json.loads(output_path.read_text(encoding="utf-8"))
        except Exception:
            return {}
    try:
        from .run_log import previous_stage_report
        return previous_stage_report(
            output_path.parent,
            "resources",
        )
    except Exception:
        return {}
'''
        replace_exact(resources, old, new, "자원 이전 report fallback")


def patch_resources(root):
    path = root / RESOURCES
    text = path.read_text(encoding="utf-8")

    text = text.replace(
        'STAGE12_VERSION = "2026-10-07-stage12-roll20-resources-v1.3-ability-modifier-uses"',
        'STAGE12_VERSION = "2026-10-08-stage12-roll20-resources-v1.4-dynamic-uses"',
        1,
    )

    old_values = '''def _resource_values(result_payload):
    translated = _dict(_dict(result_payload).get("translated"))
    original = _dict(_dict(result_payload).get("original"))

    values = _list(translated.get("resources"))
    if values:
        return values
    return _list(original.get("resources"))
'''
    new_values = '''def _resource_values(result_payload):
    translated = _dict(_dict(result_payload).get("translated"))
    original = _dict(_dict(result_payload).get("original"))

    translated_values = _list(translated.get("resources"))
    original_values = _list(original.get("resources"))

    if not translated_values:
        return original_values
    if not original_values:
        return translated_values

    merged = list(translated_values)
    seen = set()

    for item in translated_values:
        item = _dict(item)
        seen.add(
            (
                _text(item.get("source_id")),
                _text(item.get("kind")),
                _text(item.get("original_name") or item.get("name")),
            )
        )

    for item in original_values:
        item = _dict(item)
        identity = (
            _text(item.get("source_id")),
            _text(item.get("kind")),
            _text(item.get("original_name") or item.get("name")),
        )
        if identity in seen:
            continue
        seen.add(identity)
        merged.append(item)

    return merged
'''
    if new_values not in text:
        if old_values not in text:
            raise RuntimeError(
                "roll20_resources.py의 resource merge 위치를 찾지 못했습니다."
            )
        text = text.replace(old_values, new_values, 1)

    old_max = '''def _ability_modifier(score):
    score = _as_int(score, 10)
    return (score - 10) // 2


def _max_uses(limited_use, proficiency_bonus, ability_scores=None):
    limited_use = _dict(limited_use)
    ability_scores = _dict(ability_scores)
    raw_max = limited_use.get("maxUses")

    if raw_max not in (None, ""):
        maximum = _as_int(raw_max, 0)
        if maximum > 0:
            return maximum

    stat_id = _as_int(limited_use.get("statModifierUsesId"), 0)
    ability = STAT_ID_TO_ABILITY.get(stat_id)
    if ability and ability in ability_scores:
        return max(1, _ability_modifier(ability_scores.get(ability)))

    if limited_use.get("useProficiencyBonus") is True:
        return max(0, _as_int(proficiency_bonus, 0))

    return 0
'''
    new_max = '''def _ability_modifier(score):
    if type(score) not in (int, float):
        return None
    return (int(score) - 10) // 2


def _max_uses(limited_use, proficiency_bonus, ability_scores=None):
    limited_use = _dict(limited_use)
    ability_scores = _dict(ability_scores)

    raw_max = limited_use.get("maxUses")
    base = (
        _as_int(raw_max, 0)
        if raw_max not in (None, "")
        else 0
    )

    dynamic = False

    stat_id = _as_int(limited_use.get("statModifierUsesId"), 0)
    ability = STAT_ID_TO_ABILITY.get(stat_id)
    if ability:
        dynamic = True
        modifier = _ability_modifier(ability_scores.get(ability))
        operator = limited_use.get("operator")
        if modifier is None:
            return 0
        if operator == 1:
            base += modifier
        elif operator in (None, "") and raw_max in (None, "", 0):
            # Backward-compatible DDB shape: a stat modifier with no explicit
            # operator and no fixed base meant "ability modifier uses".
            base += modifier
        else:
            return 0

    if limited_use.get("useProficiencyBonus") is True:
        dynamic = True
        pb_operator = limited_use.get("proficiencyBonusOperator")
        if pb_operator not in (None, "", 1):
            return 0
        pb = _as_int(proficiency_bonus, -1)
        if pb < 0:
            return 0
        base += pb

    if dynamic:
        return max(0, base)

    if raw_max not in (None, ""):
        return max(0, base)

    return 0
'''
    if new_max not in text:
        if old_max not in text:
            raise RuntimeError(
                "roll20_resources.py의 maxUses 계산 위치를 찾지 못했습니다."
            )
        text = text.replace(old_max, new_max, 1)

    ast.parse(text, filename=str(path))
    path.write_text(text, encoding="utf-8")


def patch_basic(root):
    path = root / BASIC
    text = path.read_text(encoding="utf-8")
    if BASIC_MARKER in text:
        return

    text = text.replace(
        'VERSION = "2026-10-06-stage5-basic-fields-v2.1-multiclass"',
        'VERSION = "2026-10-08-stage5-basic-fields-v3.1-correctness"',
        1,
    )

    old_names = '''    "race",
    "subrace",
    "background",
    "alignment",
    "experience",
'''
    new_names = '''    "race",
    "race_display",
    "subrace",
    "background",
    "alignment",
    "experience",
    "cp",
    "sp",
    "ep",
    "gp",
    "pp",
'''
    if old_names not in text:
        raise RuntimeError("stage5 기본 필드 whitelist 위치를 찾지 못했습니다.")
    text = text.replace(old_names, new_names, 1)

    old_tail_names = '''    "spell_attack_bonus",
    "caster_level",
}
'''
    new_tail_names = '''    "spell_attack_bonus",
    "spellclass",
    "caster_level",
    "hit_dice",
    "hit_dice_max",
    "passiveperceptionmod",
}
'''
    if old_tail_names not in text:
        raise RuntimeError("stage5 추가 필드 whitelist 위치를 찾지 못했습니다.")
    text = text.replace(old_tail_names, new_tail_names, 1)

    helper_anchor = '''def _spell_ability(character, cls):
'''
    helpers = r'''# stage5-correctness-v3.1
def _source_classes_for_hit_dice(payload, character):
    original = _dict(_dict(payload).get("original"))
    rows = [
        row for row in _list(original.get("classes"))
        if isinstance(row, dict)
    ]
    if rows:
        return rows
    return [
        row for row in _list(character.get("classes"))
        if isinstance(row, dict)
    ]


def _hit_dice_values(payload, character):
    rows = _source_classes_for_hit_dice(payload, character)
    if not rows:
        return None

    die_types = set()
    total = 0
    remaining = 0

    for row in rows:
        level = row.get("level")
        hit_die = row.get("hit_die")
        used = row.get("hit_dice_used")

        if (
            type(level) is not int
            or level < 1
            or type(hit_die) is not int
            or hit_die < 1
            or type(used) is not int
            or used < 0
            or used > level
        ):
            return None

        die_types.add(hit_die)
        total += level
        remaining += level - used

    if len(die_types) != 1:
        return None

    return remaining, total


def _item_effect_active(item):
    item = _dict(item)
    equipped = item.get("equipped") is True
    attuned = item.get("attuned") is True
    can_equip = item.get("can_equip") is True
    can_attune = item.get("can_attune") is True
    consumable = item.get("is_consumable") is True

    return (
        (not can_equip and not can_attune and not consumable)
        or (attuned and equipped)
        or (attuned and not can_equip)
        or (not can_attune and equipped)
    )


def _passive_perception_item_bonus(payload):
    original = _dict(_dict(payload).get("original"))
    seen = False
    total = 0

    for item in _list(original.get("equipment")):
        if not isinstance(item, dict) or not _item_effect_active(item):
            continue

        for modifier in _list(item.get("granted_modifiers")):
            modifier = _dict(modifier)
            if (
                _text(modifier.get("type")).casefold() != "bonus"
                or _text(modifier.get("subType")).casefold()
                != "passive-perception"
            ):
                continue

            seen = True
            if _text(modifier.get("restriction")):
                return None

            value = modifier.get("value")
            if _number(value) is None:
                value = modifier.get("fixedValue")
            if _number(value) is None:
                return None

            total += value

    return total if seen else None


def _spellclass_name(character):
    spellcasting = _dict(character.get("spellcasting"))
    names = {
        _text(row.get("class_name"))
        for row in _list(spellcasting.get("class_calculations"))
        if isinstance(row, dict) and _text(row.get("class_name"))
    }
    if len(names) == 1:
        return next(iter(names))
    return None


'''
    if helper_anchor not in text:
        raise RuntimeError("stage5 helper 삽입 위치를 찾지 못했습니다.")
    text = text.replace(helper_anchor, helpers + helper_anchor, 1)

    old_display = '''    display_parts = [
        f"{subclass} {class_name} {level}"
        if subclass else f"{class_name} {level}"
    ]
'''
    new_display = '''    display_parts = [
        (
            f"{class_name} {level} / {subclass}"
            if subclass
            else f"{class_name} {level}"
        )
    ]
'''
    if old_display not in text:
        raise RuntimeError("stage5 class_display 기본 위치를 찾지 못했습니다.")
    text = text.replace(old_display, new_display, 1)

    old_multi = '''        display_parts.append(
            f"{multi_subclass} {multi_name} {multi_level}"
            if multi_subclass else f"{multi_name} {multi_level}"
        )

    put("class_display", ", ".join(display_parts))
'''
    new_multi = '''        display_parts.append(
            (
                f"{multi_name} {multi_level} / {multi_subclass}"
                if multi_subclass
                else f"{multi_name} {multi_level}"
            )
        )

    put("class_display", " | ".join(display_parts))
'''
    if old_multi not in text:
        raise RuntimeError("stage5 multiclass display 위치를 찾지 못했습니다.")
    text = text.replace(old_multi, new_multi, 1)

    old_race = '''    if race_name:
        put("race", race_name)
    if _text(race.get("subrace_name")):
'''
    new_race = '''    if race_name:
        put("race", race_name)
        put("race_display", race_name)
    if _text(race.get("subrace_name")):
'''
    if old_race not in text:
        raise RuntimeError("stage5 race 표시 위치를 찾지 못했습니다.")
    text = text.replace(old_race, new_race, 1)

    old_exp = '''    if _number(character.get("experience")) is not None:
        put("experience", character.get("experience"))

    scores = _dict(character.get("ability_scores"))
'''
    new_exp = '''    if _number(character.get("experience")) is not None:
        put("experience", character.get("experience"))

    currencies = _dict(character.get("currencies"))
    for currency in ("cp", "sp", "ep", "gp", "pp"):
        value = currencies.get(currency)
        if _number(value) is not None:
            put(currency, value)

    hit_dice = _hit_dice_values(payload, character)
    if hit_dice is not None:
        remaining_hit_dice, total_hit_dice = hit_dice
        put("hit_dice", remaining_hit_dice)
        put("hit_dice_max", total_hit_dice)

    passive_bonus = _passive_perception_item_bonus(payload)
    if _number(passive_bonus) is not None:
        put("passiveperceptionmod", passive_bonus)

    scores = _dict(character.get("ability_scores"))
'''
    if old_exp not in text:
        raise RuntimeError("stage5 화폐/Hit Dice 삽입 위치를 찾지 못했습니다.")
    text = text.replace(old_exp, new_exp, 1)

    old_caster = '''    put("caster_level", _caster_level_for_classes(classes))

    return {
'''
    new_caster = '''    spellclass = _spellclass_name(character)
    if spellclass:
        put("spellclass", spellclass)

    put("caster_level", _caster_level_for_classes(classes))

    return {
'''
    if old_caster not in text:
        raise RuntimeError("stage5 spellclass 삽입 위치를 찾지 못했습니다.")
    text = text.replace(old_caster, new_caster, 1)

    ast.parse(text, filename=str(path))
    path.write_text(text, encoding="utf-8")


def patch_worker_test(root):
    """Update the existing regression test to the new one-file current policy."""
    path = root / WORKER_TEST
    if not path.is_file():
        return False

    text = path.read_text(encoding="utf-8")
    old = "SheetMover/results/current/full-run-run1.json"
    new = "SheetMover/results/current/sheetmover-run-latest.json"

    if new in text:
        return True
    if old not in text:
        raise RuntimeError(
            "test_worker_protocol.py의 기존 full-run 경로 검사를 찾지 못했습니다."
        )

    text = text.replace(old, new, 1)
    ast.parse(text, filename=str(path))
    path.write_text(text, encoding="utf-8")
    return True


def apply_tree(root):
    copy_payload(root)

    required = [
        SOURCE, FULL_RUN, RESULT_STORE, PROF, RESOURCES, BASIC
    ]
    for relative in required:
        if not (root / relative).is_file():
            raise RuntimeError(f"필수 프로젝트 파일 없음: {root / relative}")

    patch_source(root)
    patch_single_log(root)
    patch_resources(root)
    patch_basic(root)
    patch_worker_test(root)

    parse_targets = list(FILES.values()) + required
    if (root / WORKER_TEST).is_file():
        parse_targets.append(WORKER_TEST)

    for relative in parse_targets:
        parse(root / relative)


def run_tests(root):
    env = os.environ.copy()
    env["PYTHONPATH"] = (
        str(root) + os.pathsep + env.get("PYTHONPATH", "")
    )
    completed = subprocess.run(
        [
            sys.executable, "-m", "unittest",
            "discover", "-s", "tests", "-v",
        ],
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
            + "\n".join(output.splitlines()[-360:])
        )

    match = re.search(r"Ran\s+(\d+)\s+tests?", output)
    return int(match.group(1)) if match else None


def backup(path):
    if not path.exists():
        return
    target = path.with_suffix(path.suffix + ".pre-v2.6.3.1.bak")
    if not target.exists():
        shutil.copy2(path, target)


def main():
    print("[시트 이동기] v2.6.3.1 정확도 + 단일 로그 패치 준비")
    print("- 선택 옵션 component 연결 보정")
    print("- CON/CHA · HP · AC 재계산")
    print("- Divine Sense / Breath Weapon 사용 횟수 보정")
    print("- race_display / spellclass / 화폐 / Hit Dice / 상시 감지 보정")
    print("- results/current 최종 통합 JSON 1개")
    print("- 실제 파일 수정 전 복제본 전체 unittest 실행")

    if not (ROOT / SOURCE).is_file():
        raise RuntimeError("E:\\sheet_mover 폴더에서 실행해 주세요.")

    with tempfile.TemporaryDirectory(
        prefix="sheetmover-v263-"
    ) as temp:
        sandbox = Path(temp) / "repo"
        shutil.copytree(ROOT, sandbox, ignore=ignore)

        apply_tree(sandbox)

        print("[시트 이동기] 복제본 전체 unittest 실행...")
        count = run_tests(sandbox)
        print(
            "[시트 이동기] 복제본 전체 테스트 통과"
            + (f": {count}개" if count is not None else "")
        )

        targets = list(FILES.values()) + [
            SOURCE, FULL_RUN, RESULT_STORE, PROF, RESOURCES, BASIC
        ]
        if (sandbox / WORKER_TEST).is_file():
            targets.append(WORKER_TEST)

        for relative in targets:
            backup(ROOT / relative)

        for relative in targets:
            src = sandbox / relative
            dst = ROOT / relative
            dst.parent.mkdir(parents=True, exist_ok=True)
            tmp = dst.with_name(dst.name + ".v2631.tmp")
            shutil.copy2(src, tmp)
            os.replace(tmp, dst)
            parse(dst)

    print("[시트 이동기] v2.6.3.1 패치 적용 완료")
    print("- 다음 실제 실행에서 정상 기대값:")
    print("  STR 25 / DEX 8 / CON 18 / INT 8 / WIS 10 / CHA 22")
    print("  HP 134 / AC 21 / Passive Perception 15")
    print("  Divine Sense 7 / Breath Weapon 5 / GP 10 / Hit Dice 13")
    print("- results/current에는 sheetmover-run-latest.json 하나만 남아야 합니다.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"[시트 이동기] v2.6.3.1 패치 실패: {exc}", file=sys.stderr)
        raise
