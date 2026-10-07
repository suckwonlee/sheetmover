# -*- coding: utf-8 -*-
from __future__ import annotations

import argparse
from pathlib import Path
import re
import shutil
import sys

ROOT = Path.cwd()
MOD = ROOT / "sheet_mover" / "roll20_spell_attacks.py"
TEST_DST = ROOT / "tests" / "test_stage10b_sequential_hotfix.py"


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: 예상 코드 1개가 필요한데 {count}개를 찾았습니다.")
    return text.replace(old, new, 1)


def patch_module(text: str) -> str:
    if "stage10b-roll20-spell-attacks-v1.1-sequential-link" in text:
        return text

    text = replace_once(
        text,
        'STAGE10B_VERSION = "2026-10-06-stage10b-roll20-spell-attacks-v1"',
        'STAGE10B_VERSION = "2026-10-07-stage10b-roll20-spell-attacks-v1.1-sequential-link"',
        "Stage 10B 버전",
    )

    helper = '''\n\ndef _build_sequential_spell_upsert_script():\n    """ATTACK 주문의 sibling repeating-spell 필드를 순차 저장합니다."""\n    script = UPSERT_ROW_SCRIPT\n    jobs_marker = "    const jobs=[];\\n\\n"\n    push_marker = """      jobs.push(\n        existing.length===1\n          ? saveExisting(existing[0],name,spec)\n          : createNew(collection,name,spec)\n      );\n"""\n    finish_marker = """    const completed=await Promise.all(jobs);\n    log.push(...completed);\n"""\n\n    for label, needle in (("jobs", jobs_marker), ("push", push_marker), ("finish", finish_marker)):\n        if needle not in script:\n            raise RuntimeError(f"공용 Roll20 upsert 스크립트 구조가 변경되었습니다: {label}")\n\n    script = script.replace(jobs_marker, "", 1)\n    script = script.replace(\n        push_marker,\n        """      log.push(\n        existing.length===1\n          ? await saveExisting(existing[0],name,spec)\n          : await createNew(collection,name,spec)\n      );\n""",\n        1,\n    )\n    script = script.replace(finish_marker, "    await fetchCollection(collection);\\n", 1)\n    return script\n\n\nSPELL_LINK_UPSERT_SEQUENTIAL_SCRIPT = _build_sequential_spell_upsert_script()\n'''
    marker = "\n\ndef spell_attack_row_id(source_key: str) -> str:\n"
    if marker not in text:
        raise RuntimeError("spell_attack_row_id 위치를 찾지 못했습니다.")
    text = text.replace(marker, helper + marker, 1)

    text = replace_once(
        text,
        '            "as_part_of_weapon_attack_stays_spellcard": True,\n',
        '            "as_part_of_weapon_attack_stays_spellcard": True,\n'
        '            "sequential_spell_link_writes": True,\n'
        '            "persisted_state_wins_over_save_callback_timeout": True,\n',
        "Stage 10B policy",
    )

    new_func = '''def _upsert_and_verify(driver, target, attrs, label, *, sequential=False):\n    script = SPELL_LINK_UPSERT_SEQUENTIAL_SCRIPT if sequential else UPSERT_ROW_SCRIPT\n    driver.set_script_timeout(120 if sequential else 45)\n    outcome = driver.execute_async_script(\n        script,\n        _text(target.get("roll20_character_id")),\n        _text(target.get("character_name")),\n        attrs,\n    )\n\n    # Roll20이 실제 저장은 끝냈지만 Backbone save callback만 반환하지 않는 경우가 있습니다.\n    # 이때는 서버 persisted read를 최종 진실로 사용합니다.\n    if not isinstance(outcome, dict) or not outcome.get("ok"):\n        after = _snapshot(driver, target, attrs.keys())\n        actual, mismatches = _verify(after, attrs)\n        if not mismatches:\n            return {\n                "ok": True,\n                "recovered_after_upsert_error": True,\n                "original_outcome": outcome,\n            }, actual\n\n        if sequential:\n            retry = driver.execute_async_script(\n                script,\n                _text(target.get("roll20_character_id")),\n                _text(target.get("character_name")),\n                attrs,\n            )\n            after = _snapshot(driver, target, attrs.keys())\n            actual, mismatches = _verify(after, attrs)\n            if not mismatches:\n                return {\n                    "ok": True,\n                    "recovered_after_retry": True,\n                    "original_outcome": outcome,\n                    "retry_outcome": retry,\n                }, actual\n            outcome = {\n                "first": outcome,\n                "retry": retry,\n                "mismatches": mismatches,\n            }\n\n        raise RuntimeError(\n            f"{label} 저장 실패: "\n            + json.dumps(outcome, ensure_ascii=False)\n        )\n\n    after = _snapshot(driver, target, attrs.keys())\n    actual, mismatches = _verify(after, attrs)\n    if mismatches:\n        raise RuntimeError(\n            f"{label} 서버 재검증 실패: "\n            + json.dumps(mismatches, ensure_ascii=False)\n        )\n    return outcome, actual\n'''

    pattern = re.compile(r"def _upsert_and_verify\(driver, target, attrs, label\):\n.*?\n\ndef apply_spell_attacks\(", re.S)
    match = pattern.search(text)
    if not match:
        raise RuntimeError("_upsert_and_verify 함수 블록을 찾지 못했습니다.")
    text = text[:match.start()] + new_func + "\n\ndef apply_spell_attacks(" + text[match.end():]

    old_call = '''                f"주문 출력 '{spell_row['name']}'",\n            )\n'''
    new_call = '''                f"주문 출력 '{spell_row['name']}'",\n                sequential=True,\n            )\n'''
    text = replace_once(text, old_call, new_call, "주문 출력 저장 호출")
    return text


def backup(path: Path):
    dst = path.with_suffix(path.suffix + ".pre-stage10b-v1.1.bak")
    if not dst.exists():
        shutil.copy2(path, dst)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()

    if not MOD.is_file():
        raise RuntimeError(f"필수 파일이 없습니다: {MOD}")

    new_text = patch_module(MOD.read_text(encoding="utf-8"))

    if args.check:
        print("[시트 이동기] Stage 10B v1.1 사전 점검 통과")
        print("- spellattackid/rollcontent 등 주문행 링크 필드 순차 저장")
        print("- callback timeout 후 persisted server state 재검증")
        print("- 파일은 아직 수정하지 않았습니다.")
        return

    backup(MOD)
    MOD.write_text(new_text, encoding="utf-8")

    bundled = Path(__file__).resolve().parent / "patch_files" / "test_stage10b_sequential_hotfix.py"
    TEST_DST.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(bundled, TEST_DST)

    print("[시트 이동기] Stage 10B v1.1 적용 완료")
    print("- 공격행 writer는 기존 방식 유지")
    print("- 주문행 링크 필드만 순차 저장")
    print("- callback timeout이어도 실제 서버 저장 상태가 맞으면 성공 처리")
    print("- 실제 미저장 시 1회 재시도")
    print("python -m unittest discover -s tests -v")
    print("python main.py")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"[시트 이동기] Stage 10B v1.1 패치 실패: {exc}", file=sys.stderr)
        raise
