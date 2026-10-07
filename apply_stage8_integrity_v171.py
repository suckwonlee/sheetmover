# -*- coding: utf-8 -*-
from __future__ import annotations
import argparse
from pathlib import Path
import shutil
import sys

ROOT = Path.cwd()
SOURCE = ROOT / "sheet_mover" / "source.py"
FEATURES = ROOT / "sheet_mover" / "roll20_features.py"
TEST_V17 = ROOT / "tests" / "test_stage8_integrity_v17.py"

def replace_once(text, old, new, label):
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: 예상 코드 1개가 필요한데 {count}개를 찾았습니다.")
    return text.replace(old, new, 1)

def patch_source(text):
    if "D&D Beyond disguise/helper feats are not owned feats." in text:
        return text

    old = """    else:
        # Unselected top-level feats are candidates, not owned feats.
        # D&D Beyond can expose package/adventure helper feats (for example
        # Dark Bargain) in raw `feats` even when the character never selected
        # or received them. Fail closed: without an explicit choice/grant,
        # do not publish any top-level feat as an owned character feature.
        active_feats = []
"""

    new = """    else:
        # D&D Beyond disguise/helper feats are not owned feats.
        #
        # Older/alternate payloads do not always expose explicit choice/grant
        # links. Preserve ordinary top-level feats because actions/modifiers
        # can depend on them (for example Great Weapon Master). D&D Beyond
        # marks placeholder/helper entries such as Dark Bargain with the
        # __DISGUISE_FEAT category, so exclude only those.
        active_feats = []
        for entry in raw_feats:
            definition = _dict(_dict(entry).get("definition") or entry)
            category_tags = {
                _text(_dict(category).get("tagName"))
                for category in _list(definition.get("categories"))
            }
            if "__DISGUISE_FEAT" in category_tags:
                continue
            active_feats.append(entry)
"""
    return replace_once(text, old, new, "source.py feat fallback")

def patch_features(text):
    old = 'STAGE8_VERSION = "2026-10-06-stage8-roll20-features-v1.7-row-integrity"'
    new = 'STAGE8_VERSION = "2026-10-07-stage8-roll20-features-v1.7.1-disguise-feat-filter"'
    if new in text:
        return text
    return replace_once(text, old, new, "Stage 8 version")

def patch_test(text):
    if '"categories": [{"tagName": "__DISGUISE_FEAT"}]' in text:
        return text
    old = """                        "id": 2048517,
                        "name": "Dark Bargain",
                        "description": "<p>Candidate only</p>",
"""
    new = """                        "id": 2048517,
                        "name": "Dark Bargain",
                        "description": "<p>Candidate only</p>",
                        "categories": [{"tagName": "__DISGUISE_FEAT"}],
"""
    return replace_once(text, old, new, "v1.7 test fixture")

def backup(path):
    dst = path.with_suffix(path.suffix + ".pre-stage8-v1.7.1.bak")
    if not dst.exists():
        shutil.copy2(path, dst)

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()

    for path in (SOURCE, FEATURES, TEST_V17):
        if not path.is_file():
            raise RuntimeError(
                f"필수 파일이 없습니다: {path}. Stage 8 v1.7을 먼저 적용하세요."
            )

    source_new = patch_source(SOURCE.read_text(encoding="utf-8"))
    features_new = patch_features(FEATURES.read_text(encoding="utf-8"))
    test_new = patch_test(TEST_V17.read_text(encoding="utf-8"))

    if args.check:
        print("[시트 이동기] Stage 8 v1.7.1 보정 패치 사전 점검 통과")
        print("- 정상 top-level feat 호환성 복구 가능")
        print("- __DISGUISE_FEAT만 제외 가능")
        print("- 기존 순차 trait 저장/정리 로직 유지")
        print("[시트 이동기] 파일은 아직 수정하지 않았습니다.")
        return 0

    for path in (SOURCE, FEATURES, TEST_V17):
        backup(path)

    SOURCE.write_text(source_new, encoding="utf-8")
    FEATURES.write_text(features_new, encoding="utf-8")
    TEST_V17.write_text(test_new, encoding="utf-8")

    print("[시트 이동기] Stage 8 v1.7.1 보정 패치 적용 완료")
    print("- Great Weapon Master 같은 정상 feat 유지")
    print("- Dark Bargain 같은 __DISGUISE_FEAT만 제외")
    print("- Stage 8 순차 반복행 저장 유지")
    print("- stale Sheet Mover 행 정리 유지")
    print()
    print("다음:")
    print("python -m unittest discover -s tests -v")
    print("python main.py")
    return 0

if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"[시트 이동기] Stage 8 v1.7.1 패치 실패: {exc}", file=sys.stderr)
        raise
