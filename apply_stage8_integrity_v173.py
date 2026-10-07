# -*- coding: utf-8 -*-
from __future__ import annotations

import argparse
from pathlib import Path
import shutil
import sys

ROOT = Path.cwd()
SOURCE = ROOT / "sheet_mover" / "source.py"
FEATURES = ROOT / "sheet_mover" / "roll20_features.py"

BAD_EXPR = '_text(_dict(category).get("tagName"))'
GOOD_EXPR = 'str(_dict(category).get("tagName") or "").strip()'


def backup(path: Path):
    dst = path.with_suffix(path.suffix + ".pre-stage8-v1.7.3.bak")
    if not dst.exists():
        shutil.copy2(path, dst)


def patch_source(text: str) -> tuple[str, str]:
    if GOOD_EXPR in text:
        return text, "already_fixed"

    count = text.count(BAD_EXPR)
    if count == 0:
        raise RuntimeError(
            "source.py에서 잘못된 _text(...) 호출을 찾지 못했습니다. "
            "현재 파일 상태가 예상과 다릅니다."
        )

    # Exact surrounding indentation/line-ending is intentionally ignored.
    return text.replace(BAD_EXPR, GOOD_EXPR), f"replaced:{count}"


def patch_features(text: str) -> str:
    candidates = (
        'STAGE8_VERSION = "2026-10-07-stage8-roll20-features-v1.7.1-disguise-feat-filter"',
        'STAGE8_VERSION = "2026-10-07-stage8-roll20-features-v1.7.2-tag-helper-hotfix"',
        'STAGE8_VERSION = "2026-10-06-stage8-roll20-features-v1.7-row-integrity"',
    )
    target = (
        'STAGE8_VERSION = '
        '"2026-10-07-stage8-roll20-features-v1.7.3-tag-helper-hotfix"'
    )

    if target in text:
        return text

    for old in candidates:
        if old in text:
            return text.replace(old, target, 1)

    # Version bump is diagnostic only; do not block the actual fix.
    return text


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()

    if not SOURCE.is_file():
        raise RuntimeError(f"필수 파일이 없습니다: {SOURCE}")

    source_old = SOURCE.read_text(encoding="utf-8")
    source_new, status = patch_source(source_old)

    features_old = FEATURES.read_text(encoding="utf-8") if FEATURES.is_file() else None
    features_new = patch_features(features_old) if features_old is not None else None

    if args.check:
        print("[시트 이동기] Stage 8 v1.7.3 핫픽스 사전 점검 통과")
        print(f"- source.py 상태: {status}")
        print("- 줄바꿈/들여쓰기와 무관하게 문제 표현식만 교체합니다.")
        print("[시트 이동기] 파일은 아직 수정하지 않았습니다.")
        return 0

    backup(SOURCE)
    SOURCE.write_text(source_new, encoding="utf-8")

    if FEATURES.is_file() and features_new is not None:
        backup(FEATURES)
        FEATURES.write_text(features_new, encoding="utf-8")

    print("[시트 이동기] Stage 8 v1.7.3 핫픽스 적용 완료")
    print(f"- source.py: {status}")
    print("- NameError _text 제거")
    print("- __DISGUISE_FEAT 필터 유지")
    print("- Great Weapon Master 정상 feat 유지")
    print()
    print("다음:")
    print("python -m unittest discover -s tests -v")
    print("python main.py")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"[시트 이동기] Stage 8 v1.7.3 핫픽스 실패: {exc}", file=sys.stderr)
        raise
