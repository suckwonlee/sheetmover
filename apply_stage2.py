"""Apply Stage 2 final-value extraction to the current sheet_mover checkout.

Usage from repository root:
    python apply_stage2.py --check
    python apply_stage2.py

The patch is based on GitHub commit:
    ab691ad6050dc2dacaed335d374f1f64c08ecd54

It intentionally does NOT replace the Stage 1 translation files.  Only the
small integration points in source.py, mover.py and test_preparation.py are
edited.  The script fails closed if the expected code markers are not present.
"""
from __future__ import annotations

import argparse
from pathlib import Path
import shutil
import subprocess
import sys


EXPECTED_BASE_COMMIT = "ab691ad6050dc2dacaed335d374f1f64c08ecd54"

ROOT = Path.cwd()
SOURCE = ROOT / "sheet_mover" / "source.py"
MOVER = ROOT / "sheet_mover" / "mover.py"
TEST_PREP = ROOT / "tests" / "test_preparation.py"
CALCULATOR = ROOT / "sheet_mover" / "calculator.py"
TEST_STAGE2 = ROOT / "tests" / "test_stage2_calculator.py"


def replace_once(text, old, new, label):
    count = text.count(old)
    if count != 1:
        raise RuntimeError(
            f"{label}: expected exactly one source marker, found {count}"
        )
    return text.replace(old, new, 1)


def _already_applied(source_text, mover_text):
    return (
        "from .calculator import apply_stage2_calculations" in source_text
        and "apply_stage2_calculations(sheet)" in source_text
        and "armor_type_id=definition.get(\"armorTypeId\")" in source_text
        and '"class_feature_levels": [' in source_text
        and "현재 2단계 미리보기입니다." in mover_text
    )


def _git_output(*args):
    try:
        proc = subprocess.run(
            ["git", *args],
            cwd=ROOT,
            check=True,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
    except (OSError, subprocess.CalledProcessError):
        return None
    return proc.stdout.strip()


def _verify_git_base():
    head = _git_output("rev-parse", "HEAD")
    if head is None:
        return

    if head != EXPECTED_BASE_COMMIT:
        raise RuntimeError(
            "현재 Git HEAD가 이 패치의 기준 커밋과 다릅니다.\n"
            f"  현재: {head}\n"
            f"  기준: {EXPECTED_BASE_COMMIT}\n"
            "새 커밋이 있다면 그 상태를 기준으로 패치를 다시 만들어야 합니다."
        )

    changed = _git_output(
        "diff",
        "--name-only",
        "--",
        "sheet_mover/source.py",
        "sheet_mover/mover.py",
        "tests/test_preparation.py",
    )
    if changed:
        raise RuntimeError(
            "2단계가 수정할 기존 파일에 커밋되지 않은 변경이 있습니다: "
            + ", ".join(changed.splitlines())
        )


def patch_source_text(text):
    if "from .calculator import apply_stage2_calculations" not in text:
        text = replace_once(
            text,
            "from .models import CharacterSheet\n",
            "from .models import CharacterSheet\n"
            "from .calculator import apply_stage2_calculations\n",
            "source import",
        )

    if "armor_type_id=definition.get(\"armorTypeId\")" not in text:
        text = replace_once(
            text,
            "        armor_class=definition.get(\"armorClass\"),\n"
            "        damage=deepcopy(definition.get(\"damage\")),\n",
            "        armor_class=definition.get(\"armorClass\"),\n"
            "        armor_type_id=definition.get(\"armorTypeId\"),\n"
            "        base_armor_name=definition.get(\"baseArmorName\"),\n"
            "        can_equip=definition.get(\"canEquip\"),\n"
            "        can_attune=definition.get(\"canAttune\"),\n"
            "        is_consumable=definition.get(\"isConsumable\"),\n"
            "        granted_modifiers=deepcopy(definition.get(\"grantedModifiers\") or []),\n"
            "        damage=deepcopy(definition.get(\"damage\")),\n",
            "inventory final-AC metadata",
        )

    if '"class_feature_levels": [' not in text:
        text = replace_once(
            text,
            '        "class_spellcasting": [\n',
            '        "class_feature_levels": [\n'
            '            {\n'
            '                "feature_id": str(\n'
            '                    _dict(_dict(feature).get("definition") or feature).get("id")\n'
            '                    or ""\n'
            '                ),\n'
            '                "class_name": str(\n'
            '                    _dict(character_class.get("definition")).get("name")\n'
            '                    or ""\n'
            '                ),\n'
            '                "class_level": _number(character_class.get("level")),\n'
            '            }\n'
            '            for character_class in _list(data.get("classes"))\n'
            '            if isinstance(character_class, dict)\n'
            '            for feature in _active_class_feature_entries(character_class)\n'
            '        ],\n'
            '        "class_spellcasting": [\n',
            "class feature level ownership",
        )

    if "apply_stage2_calculations(sheet)" not in text:
        text = replace_once(
            text,
            '    sheet.warnings.append(\n'
            '        "공격, 스킬/내성/전문화, 클래스 자원의 Roll20 변환은 각각 10·11·12단계에서 "\n'
            '        "구현합니다."\n'
            '    )\n'
            '    return sheet\n',
            '    sheet.warnings.append(\n'
            '        "공격, 스킬/내성/전문화, 클래스 자원의 Roll20 변환은 각각 10·11·12단계에서 "\n'
            '        "구현합니다."\n'
            '    )\n'
            '\n'
            '    # Stage 2: produce Roll20-ready final numeric values from the\n'
            '    # concrete D&D Beyond source facts preserved above. Unsupported\n'
            '    # conditional rules fail closed instead of being guessed.\n'
            '    apply_stage2_calculations(sheet)\n'
            '    return sheet\n',
            "stage2 calculation integration",
        )

    return text


def patch_mover_text(text):
    old = (
        '        warnings.append(\n'
        '            "현재 1단계 미리보기입니다. Roll20 캐릭터 탐색과 입력은 아직 수행하지 않습니다."\n'
        '        )\n'
    )
    new = (
        '        warnings.append(\n'
        '            "현재 2단계 미리보기입니다. 최종 능력치·HP·AC·레벨·숙련 보너스 "\n'
        '            "계산까지 완료했으며, Roll20 캐릭터 탐색과 입력은 아직 수행하지 않습니다."\n'
        '        )\n'
    )
    if "현재 2단계 미리보기입니다." not in text:
        text = replace_once(text, old, new, "mover stage warning")
    return text


def patch_test_text(text):
    if "def test_stage2_applies_direct_score_modifier" not in text:
        text = replace_once(
            text,
            "    def test_unresolved_stats_are_not_base_scores(self):\n",
            "    def test_stage2_applies_direct_score_modifier(self):\n",
            "stage2 ability test name",
        )

    if 'self.assertEqual(sheet.ability_scores["strength"], 14)' not in text:
        text = replace_once(
            text,
            '        self.assertIsNone(sheet.ability_scores["strength"])\n',
            '        self.assertEqual(sheet.ability_scores["strength"], 14)\n',
            "stage2 ability expectation",
        )

    if "self.assertEqual(sheet.total_level, 5)" not in text:
        text = replace_once(
            text,
            "        self.assertIsNone(sheet.total_level)\n",
            "        self.assertEqual(sheet.total_level, 5)\n",
            "stage2 total-level expectation",
        )

    return text


def _read_required_files():
    required = [SOURCE, MOVER, TEST_PREP, CALCULATOR, TEST_STAGE2]
    missing = [str(path) for path in required if not path.is_file()]
    if missing:
        raise RuntimeError(
            "필수 파일이 없습니다. ZIP을 E:\\sheet_mover 루트에 풀었는지 확인하세요: "
            + ", ".join(missing)
        )

    return (
        SOURCE.read_text(encoding="utf-8"),
        MOVER.read_text(encoding="utf-8"),
        TEST_PREP.read_text(encoding="utf-8"),
    )


def _backup(path):
    backup = path.with_suffix(path.suffix + ".stage2.bak")
    if not backup.exists():
        shutil.copy2(path, backup)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--check",
        action="store_true",
        help="수정하지 않고 현재 체크아웃에 패치를 적용할 수 있는지만 검사합니다.",
    )
    args = parser.parse_args()

    source_text, mover_text, test_text = _read_required_files()

    if _already_applied(source_text, mover_text):
        print("[시트 이동기] 2단계 패치가 이미 적용되어 있습니다.")
        return 0

    _verify_git_base()

    # Build every edit in memory first. Any marker mismatch stops before files
    # are touched.
    new_source = patch_source_text(source_text)
    new_mover = patch_mover_text(mover_text)
    new_test = patch_test_text(test_text)

    if args.check:
        print("[시트 이동기] 2단계 패치 사전 점검 통과.")
        print(f"기준 커밋: {EXPECTED_BASE_COMMIT}")
        print("기존 번역 파일은 수정하지 않습니다.")
        return 0

    for path in (SOURCE, MOVER, TEST_PREP):
        _backup(path)

    SOURCE.write_text(new_source, encoding="utf-8")
    MOVER.write_text(new_mover, encoding="utf-8")
    TEST_PREP.write_text(new_test, encoding="utf-8")

    print("[시트 이동기] 2단계 최종 수치 추출 패치를 적용했습니다.")
    print("백업 파일: *.stage2.bak")
    print("다음 명령으로 전체 테스트를 실행하세요:")
    print("python -m unittest discover -s tests -v")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"[시트 이동기] 2단계 패치 적용 실패: {exc}", file=sys.stderr)
        raise
