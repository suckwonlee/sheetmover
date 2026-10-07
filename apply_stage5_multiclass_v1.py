# -*- coding: utf-8 -*-
from __future__ import annotations
import argparse
from pathlib import Path
import shutil
import sys

ROOT = Path.cwd()
SOURCE = ROOT / "sheet_mover" / "source.py"
STAGE5 = ROOT / "stage5_basic_writer_v2.py"
TEST_DST = ROOT / "tests" / "test_stage5_multiclass.py"

def replace_once(text, old, new, label):
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: 예상 코드 1개가 필요한데 {count}개를 찾았습니다.")
    return text.replace(old, new, 1)

def patch_source(text):
    if '"is_starting_class": bool(entry.get("isStartingClass", False))' in text:
        return text
    old = '        "level": _number(entry.get("level")),\n        "hit_die": _number(definition.get("hitDice")),\n'
    new = (
        '        "level": _number(entry.get("level")),\n'
        '        "is_starting_class": bool(entry.get("isStartingClass", False)),\n'
        '        "hit_die": _number(definition.get("hitDice")),\n'
    )
    return replace_once(text, old, new, "source starting class")

def patch_stage5(text):
    if "stage5-basic-fields-v2.1-multiclass" in text:
        return text

    text = replace_once(
        text,
        'VERSION = "2026-10-06-stage5-basic-fields-v2"',
        'VERSION = "2026-10-06-stage5-basic-fields-v2.1-multiclass"',
        "stage5 version",
    )

    old = '    "class",\n    "subclass",\n    "base_level",\n'
    new = (
        '    "class",\n'
        '    "class_display",\n'
        '    "subclass",\n'
        '    "base_level",\n'
        '    "multiclass1_flag",\n'
        '    "multiclass1",\n'
        '    "multiclass1_lvl",\n'
        '    "multiclass1_subclass",\n'
        '    "multiclass2_flag",\n'
        '    "multiclass2",\n'
        '    "multiclass2_lvl",\n'
        '    "multiclass2_subclass",\n'
        '    "multiclass3_flag",\n'
        '    "multiclass3",\n'
        '    "multiclass3_lvl",\n'
        '    "multiclass3_subclass",\n'
    )
    text = replace_once(text, old, new, "stage5 whitelist")

    start = text.find("def _single_class(character):\n")
    end = text.find("\n\ndef _original_name(", start)
    if start < 0 or end < 0:
        raise RuntimeError("_single_class 위치를 찾지 못했습니다.")
    new_func = r'''def _ordered_classes(character):
    rows = [
        row for row in _list(character.get("classes"))
        if isinstance(row, dict)
    ]
    if not rows:
        raise RuntimeError("클래스 정보가 없습니다.")
    if len(rows) > 4:
        raise RuntimeError(
            "Roll20 Legacy는 최대 4개 클래스(기본 1 + 멀티 3)까지만 "
            f"지원합니다. 클래스 수: {len(rows)}"
        )

    starting = [row for row in rows if row.get("is_starting_class") is True]
    if len(starting) > 1:
        raise RuntimeError("D&D Beyond 시작 클래스가 둘 이상입니다.")

    if starting:
        base = starting[0]
        return [base, *[row for row in rows if row is not base]]

    return rows
'''
    text = text[:start] + new_func + text[end:]

    start = text.find("def _caster_level_for_single_class(cls):\n")
    end = text.find("\n\ndef build_plan(", start)
    if start < 0 or end < 0:
        raise RuntimeError("_caster_level_for_single_class 위치를 찾지 못했습니다.")
    new_caster = r'''def _caster_type(cls):
    name = _original_name(cls).casefold()
    level = cls.get("level")
    if type(level) is not int or level < 1:
        return 0.0

    if name in {"bard", "cleric", "druid", "sorcerer", "wizard"}:
        return 1.0

    if name in {"artificer", "paladin", "ranger"}:
        if name == "artificer" and level == 1:
            return 1.0
        return 0.0 if level == 1 else 0.5

    subclass = _text(
        cls.get("original_subclass_name") or cls.get("subclass_name")
    ).casefold()
    if (
        (name == "fighter" and subclass == "eldritch knight")
        or (name == "rogue" and subclass == "arcane trickster")
    ):
        return 0.0 if level in {1, 2} else (1.0 / 3.0)

    return 0.0


def _caster_level_for_classes(classes):
    multicaster = sum(1 for cls in classes if _caster_type(cls) > 0) > 1
    total = 0
    for cls in classes:
        level = cls.get("level")
        if type(level) is not int or level < 1:
            continue
        value = level * _caster_type(cls)
        total += math.floor(value) if multicaster else math.ceil(value)
    return total


def _is_arcane_fighter(classes):
    return any(
        _original_name(cls).casefold() == "fighter"
        and _text(
            cls.get("original_subclass_name") or cls.get("subclass_name")
        ).casefold() == "eldritch knight"
        for cls in classes
    )


def _is_arcane_rogue(classes):
    return any(
        _original_name(cls).casefold() == "rogue"
        and _text(
            cls.get("original_subclass_name") or cls.get("subclass_name")
        ).casefold() == "arcane trickster"
        for cls in classes
    )
'''
    text = text[:start] + new_caster + text[end:]

    text = replace_once(
        text,
        "    character = _character(payload)\n    cls = _single_class(character)\n",
        "    character = _character(payload)\n    classes = _ordered_classes(character)\n    cls = classes[0]\n",
        "build_plan class selection",
    )

    old = (
        '    subclass = _text(cls.get("subclass_name") or cls.get("original_subclass_name"))\n'
        '    if subclass:\n'
        '        put("subclass", subclass)\n\n'
        '    subclass_original = _text(\n'
        '        cls.get("original_subclass_name") or cls.get("subclass_name")\n'
        '    ).casefold()\n'
        '    put("arcane_fighter", 1 if subclass_original == "eldritch knight" else 0)\n'
        '    put("arcane_rogue", 1 if subclass_original == "arcane trickster" else 0)\n'
    )
    new = r'''    subclass = _text(
        cls.get("subclass_name") or cls.get("original_subclass_name")
    )
    put("subclass", subclass)

    display_parts = [
        f"{subclass} {class_name} {level}"
        if subclass else f"{class_name} {level}"
    ]

    secondary = classes[1:]
    for slot in range(1, 4):
        row = secondary[slot - 1] if slot <= len(secondary) else None
        prefix = f"multiclass{slot}"

        if row is None:
            put(f"{prefix}_flag", 0)
            put(prefix, "")
            put(f"{prefix}_lvl", "")
            put(f"{prefix}_subclass", "")
            continue

        multi_name = _original_name(row)
        multi_level = row.get("level")
        if not multi_name:
            raise RuntimeError(f"{slot + 1}번째 클래스 원문 이름이 없습니다.")
        if type(multi_level) is not int or multi_level < 1:
            raise RuntimeError(f"{multi_name} 클래스 레벨이 확정되지 않았습니다.")

        multi_subclass = _text(
            row.get("subclass_name") or row.get("original_subclass_name")
        )

        put(f"{prefix}_flag", 1)
        put(prefix, multi_name)
        put(f"{prefix}_lvl", multi_level)
        put(f"{prefix}_subclass", multi_subclass)

        display_parts.append(
            f"{multi_subclass} {multi_name} {multi_level}"
            if multi_subclass else f"{multi_name} {multi_level}"
        )

    put("class_display", ", ".join(display_parts))
    put("arcane_fighter", 1 if _is_arcane_fighter(classes) else 0)
    put("arcane_rogue", 1 if _is_arcane_rogue(classes) else 0)
'''
    text = replace_once(text, old, new, "multiclass mapping")

    text = replace_once(
        text,
        '    put("caster_level", _caster_level_for_single_class(cls))\n',
        '    put("caster_level", _caster_level_for_classes(classes))\n',
        "caster level",
    )
    return text

def backup(path):
    dst = path.with_suffix(path.suffix + ".pre-multiclass.bak")
    if not dst.exists():
        shutil.copy2(path, dst)

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()

    if not SOURCE.is_file() or not STAGE5.is_file():
        raise RuntimeError("ZIP을 E:\\sheet_mover 루트에 풀어주세요.")

    source_new = patch_source(SOURCE.read_text(encoding="utf-8"))
    stage5_new = patch_stage5(STAGE5.read_text(encoding="utf-8"))

    if args.check:
        print("[시트 이동기] 멀티클래스 패치 사전 점검 통과")
        print("[시트 이동기] 파일은 아직 수정하지 않았습니다.")
        return 0

    backup(SOURCE)
    backup(STAGE5)
    SOURCE.write_text(source_new, encoding="utf-8")
    STAGE5.write_text(stage5_new, encoding="utf-8")

    bundled = Path(__file__).resolve().parent / "tests" / "test_stage5_multiclass.py"
    if bundled.is_file():
        TEST_DST.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(bundled, TEST_DST)

    print("[시트 이동기] Stage 5 멀티클래스 지원 적용 완료")
    print("- 시작 클래스 보존")
    print("- 기본 클래스 + 멀티클래스 3칸")
    print("- 클래스별 레벨/서브클래스")
    print("- class_display")
    print("- Roll20 Legacy 방식 caster_level")
    print()
    print("다음:")
    print("python -m unittest discover -s tests -v")
    print("python main.py")
    return 0

if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"[시트 이동기] 패치 실패: {exc}", file=sys.stderr)
        raise
