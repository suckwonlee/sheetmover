# -*- coding: utf-8 -*-
from __future__ import annotations

import argparse
from pathlib import Path
import shutil
import sys

ROOT = Path.cwd()
MOD = ROOT / "sheet_mover" / "roll20_spell_attacks.py"
TEST = ROOT / "tests" / "test_stage10b_link_dispatch.py"


def replace_once(text, old, new, label):
    count = text.count(old)
    if count != 1:
        raise RuntimeError(
            f"{label}: 예상 코드 1개가 필요한데 {count}개를 찾았습니다."
        )
    return text.replace(old, new, 1)


def patch_module(text):
    if "stage10b-roll20-spell-attacks-v1.3-link-dispatch" in text:
        return text

    text = replace_once(
        text,
        'STAGE10B_VERSION = "2026-10-07-stage10b-roll20-spell-attacks-v1.2-rollcontent-dispatch"',
        'STAGE10B_VERSION = "2026-10-07-stage10b-roll20-spell-attacks-v1.3-link-dispatch"',
        "Stage 10B version",
    )

    old_split = '''def _split_rollcontent_attrs(attrs):
    normal = {}
    rollcontent = {}
    for name, spec in attrs.items():
        if name.endswith("_rollcontent"):
            rollcontent[name] = spec
        else:
            normal[name] = spec
    return normal, rollcontent
'''

    new_split = '''def _split_link_attrs(attrs):
    normal = {}
    links = {}
    for name, spec in attrs.items():
        if name.endswith("_spellattackid") or name.endswith("_rollcontent"):
            links[name] = spec
        else:
            normal[name] = spec
    return normal, links
'''
    text = replace_once(text, old_split, new_split, "link attr split")

    old_writer = '''def _write_rollcontent_and_verify(driver, target, attrs, label):
    if not attrs:
        return {"ok": True, "action": "none"}, {}

    if len(attrs) != 1:
        raise RuntimeError(
            f"{label}: rollcontent 속성은 한 번에 1개여야 합니다. "
            f"현재 {len(attrs)}개"
        )

    name, spec = next(iter(attrs.items()))
    driver.set_script_timeout(30)
    outcome = driver.execute_async_script(
        ROLLCONTENT_DISPATCH_SCRIPT,
        _text(target.get("roll20_character_id")),
        _text(target.get("character_name")),
        name,
        spec,
    )
    if not isinstance(outcome, dict) or not outcome.get("ok"):
        raise RuntimeError(
            f"{label} dispatch 실패: "
            + json.dumps(outcome, ensure_ascii=False)
        )

    actual, attempts = _poll_persisted(
        driver,
        target,
        attrs,
        label,
    )
    outcome = dict(outcome)
    outcome["persisted_verify_attempts"] = attempts
    return outcome, actual
'''

    new_writer = '''def _write_link_attrs_and_verify(driver, target, attrs, label):
    if not attrs:
        return {"ok": True, "action": "none", "results": []}, {}

    ordered_names = sorted(
        attrs,
        key=lambda name: (
            0 if name.endswith("_spellattackid") else 1,
            name,
        ),
    )

    results = []
    merged_actual = {}
    driver.set_script_timeout(30)

    for name in ordered_names:
        spec = attrs[name]
        outcome = driver.execute_async_script(
            ROLLCONTENT_DISPATCH_SCRIPT,
            _text(target.get("roll20_character_id")),
            _text(target.get("character_name")),
            name,
            spec,
        )
        if not isinstance(outcome, dict) or not outcome.get("ok"):
            raise RuntimeError(
                f"{label} dispatch 실패: "
                + json.dumps(
                    {"attribute": name, "outcome": outcome},
                    ensure_ascii=False,
                )
            )

        actual, attempts = _poll_persisted(
            driver,
            target,
            {name: spec},
            f"{label} [{name}]",
        )
        row_result = dict(outcome)
        row_result["attribute"] = name
        row_result["persisted_verify_attempts"] = attempts
        results.append(row_result)
        merged_actual.update(actual)

    return {
        "ok": True,
        "action": "link_dispatch",
        "results": results,
    }, merged_actual
'''
    text = replace_once(text, old_writer, new_writer, "link attr writer")

    old_policy = '''            "rollcontent_no_wait_dispatch": True,
            "rollcontent_persisted_poll_verify": True,
'''
    new_policy = '''            "rollcontent_no_wait_dispatch": True,
            "rollcontent_persisted_poll_verify": True,
            "spellattackid_no_wait_dispatch": True,
            "spellattackid_persisted_poll_verify": True,
'''
    text = replace_once(text, old_policy, new_policy, "Stage 10B link policy")

    old_call = '''            normal_attrs, rollcontent_attrs = _split_rollcontent_attrs(
                spell_attrs
            )
'''
    new_call = '''            normal_attrs, link_attrs = _split_link_attrs(
                spell_attrs
            )
'''
    text = replace_once(text, old_call, new_call, "link attr call")

    old_write = '''            _write_rollcontent_and_verify(
                driver,
                target,
                rollcontent_attrs,
                f"주문 출력 '{spell_row['name']}' rollcontent",
            )
'''
    new_write = '''            _write_link_attrs_and_verify(
                driver,
                target,
                link_attrs,
                f"주문 출력 '{spell_row['name']}' 링크",
            )
'''
    text = replace_once(text, old_write, new_write, "link writer call")

    return text


def backup(path):
    dst = path.with_suffix(path.suffix + ".pre-stage10b-v1.3.bak")
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
        print("[시트 이동기] Stage 10B v1.3 사전 점검 통과")
        print("- spellattackid를 일반 wait:true writer에서 분리 가능")
        print("- rollcontent와 함께 wait:false dispatch + persisted 검증")
        print("- spellattackid를 먼저 저장하고 rollcontent를 나중에 저장")
        print("- 파일은 아직 수정하지 않았습니다.")
        return 0

    backup(MOD)
    MOD.write_text(new_text, encoding="utf-8")

    bundled = Path(__file__).resolve().parent / "tests" / "test_stage10b_link_dispatch.py"
    if bundled.is_file():
        TEST.parent.mkdir(parents=True, exist_ok=True)
        if bundled.resolve() != TEST.resolve():
            shutil.copy2(bundled, TEST)

    print("[시트 이동기] Stage 10B v1.3 적용 완료")
    print("- spellattackid: no-wait dispatch + persisted 검증")
    print("- rollcontent: no-wait dispatch + persisted 검증")
    print("- 일반 주문 전투 필드는 기존 writer 유지")
    print("python -m unittest discover -s tests -v")
    print("python main.py")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"[시트 이동기] Stage 10B v1.3 패치 실패: {exc}", file=sys.stderr)
        raise
