# -*- coding: utf-8 -*-
from __future__ import annotations

import argparse
from pathlib import Path
import shutil
import sys

ROOT = Path.cwd()
SOURCE = ROOT / "sheet_mover" / "source.py"
FEATURES = ROOT / "sheet_mover" / "roll20_features.py"


def replace_once(text, old, new, label):
    count = text.count(old)
    if count != 1:
        raise RuntimeError(
            f"{label}: 예상 코드 1개가 필요한데 {count}개를 찾았습니다."
        )
    return text.replace(old, new, 1)


def patch_source(text):
    bad = (
        '            category_tags = {\\n'
        '                _text(_dict(category).get("tagName"))\\n'
        '                for category in _list(definition.get("categories"))\\n'
        '            }\\n'
    )
    fixed = (
        '            category_tags = {\\n'
        '                str(_dict(category).get("tagName") or "").strip()\\n'
        '                for category in _list(definition.get("categories"))\\n'
        '            }\\n'
    )
    if fixed in text:
        return text
    return replace_once(
        text,
        bad,
        fixed,
        "source.py __DISGUISE_FEAT 태그 읽기",
    )


def patch_features(text):
    old = (
        'STAGE8_VERSION = '
        '"2026-10-07-stage8-roll20-features-v1.7.1-disguise-feat-filter"'
    )
    new = (
        'STAGE8_VERSION = '
        '"2026-10-07-stage8-roll20-features-v1.7.2-tag-helper-hotfix"'
    )
    if new in text:
        return text
    return replace_once(text, old, new, "Stage 8 version")


def backup(path):
    dst = path.with_suffix(path.suffix + ".pre-stage8-v1.7.2.bak")
    if not dst.exists():
        shutil.copy2(path, dst)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()

    for path in (SOURCE, FEATURES):
        if not path.is_file():
            raise RuntimeError(
                f"필수 파일이 없습니다: {path}. "
                "ZIP을 E:\\sheet_mover 루트에 풀어주세요."
            )

    source_new = patch_source(SOURCE.read_text(encoding="utf-8"))
    features_new = patch_features(FEATURES.read_text(encoding="utf-8"))

    if args.check:
        print("[시트 이동기] Stage 8 v1.7.2 핫픽스 사전 점검 통과")
        print("- source.py의 존재하지 않는 _text 호출 제거 가능")
        print("- __DISGUISE_FEAT 필터 로직은 그대로 유지")
        print("[시트 이동기] 파일은 아직 수정하지 않았습니다.")
        return 0

    for path in (SOURCE, FEATURES):
        backup(path)

    SOURCE.write_text(source_new, encoding="utf-8")
    FEATURES.write_text(features_new, encoding="utf-8")

    print("[시트 이동기] Stage 8 v1.7.2 핫픽스 적용 완료")
    print("- NameError: _text 제거")
    print("- Great Weapon Master 정상 feat 유지")
    print("- Dark Bargain(__DISGUISE_FEAT) 제외 유지")
    print("- Stage 8 순차 반복행 저장 유지")
    print()
    print("다음:")
    print("python -m unittest discover -s tests -v")
    print("python main.py")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"[시트 이동기] Stage 8 v1.7.2 핫픽스 실패: {exc}", file=sys.stderr)
        raise
