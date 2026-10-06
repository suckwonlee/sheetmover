"""Apply Stage 4 read-only Roll20 target discovery and result-folder layout.

Run from E:\\sheet_mover:
    python apply_stage4.py --check
    python apply_stage4.py

This patch does not write to Roll20. It only prepares a read-only target check.
"""
from __future__ import annotations

import argparse
from pathlib import Path
import shutil

ROOT = Path.cwd()
MAIN = ROOT / "sheet_mover" / "__main__.py"
MOVER = ROOT / "sheet_mover" / "mover.py"
HYBRID = ROOT / "sheet_mover" / "hybrid_translator.py"
GITIGNORE = ROOT / ".gitignore"
ASSET_ROOT = Path(__file__).resolve().parent / "stage4_assets"

NEW_FILES = [
    "sheet_mover/result_store.py",
    "sheet_mover/result_cleanup.py",
    "sheet_mover/roll20_browser.py",
    "sheet_mover/roll20_connection.py",
    "tests/test_stage4_results.py",
    "tests/test_roll20_connection.py",
]


def replace_once(text, old, new, label):
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected exactly one source marker, found {count}")
    return text.replace(old, new, 1)


def _verify_stage3():
    required = [MAIN, MOVER, HYBRID, ROOT / "sheet_mover" / "roll20_payload.py"]
    missing = [str(path) for path in required if not path.is_file()]
    if missing:
        raise RuntimeError("3단계 필수 파일이 없습니다: " + ", ".join(missing))
    mover = MOVER.read_text(encoding="utf-8")
    if "현재 3단계 미리보기입니다." not in mover or "roll20_payload=roll20_payload" not in mover:
        raise RuntimeError("4단계는 3단계 적용 완료 상태가 필요합니다.")
    for rel in NEW_FILES:
        if not (ASSET_ROOT / rel).is_file():
            raise RuntimeError(f"4단계 ZIP 자산이 없습니다: {rel}")


def patch_main(text):
    if "from .result_store import default_result_path" not in text:
        text = replace_once(
            text,
            "from .source import fetch_character, normalize_character\n",
            "from .source import fetch_character, normalize_character\n"
            "from .result_store import default_result_path\n",
            "CLI result_store import",
        )
    old = (
        "    return Path(\n"
        "        f\"sheet-result-{safe_source_id}-{now:%Y%m%d-%H%M%S}.json\"\n"
        "    )\n"
    )
    new = "    return default_result_path(safe_source_id, now=now)\n"
    if old in text:
        text = text.replace(old, new, 1)
    elif "return default_result_path(safe_source_id, now=now)" not in text:
        raise RuntimeError("CLI 기본 결과 경로 마커가 예상 형태와 다릅니다.")
    return text


def patch_hybrid(text):
    old = (
        "        root = Path(__file__).resolve().parent.parent\n"
        "        try:\n"
        "            return sorted(\n"
        "                root.glob(\"sheet-result-*.json\"),\n"
        "                key=self._result_candidate_sort_key,\n"
        "                reverse=True,\n"
        "            )[:24]\n"
        "        except OSError:\n"
        "            return []\n"
    )
    new = (
        "        root = Path(__file__).resolve().parent.parent\n"
        "        try:\n"
        "            candidates = []\n"
        "            seen = set()\n"
        "            for folder in (root / \"results\" / \"current\", root):\n"
        "                for path in folder.glob(\"sheet-result-*.json\"):\n"
        "                    try:\n"
        "                        key = path.resolve()\n"
        "                    except OSError:\n"
        "                        key = path\n"
        "                    if key in seen:\n"
        "                        continue\n"
        "                    seen.add(key)\n"
        "                    candidates.append(path)\n"
        "            return sorted(\n"
        "                candidates,\n"
        "                key=self._result_candidate_sort_key,\n"
        "                reverse=True,\n"
        "            )[:24]\n"
        "        except OSError:\n"
        "            return []\n"
    )
    if old in text:
        return text.replace(old, new, 1)
    if 'for folder in (root / "results" / "current", root):' in text:
        return text
    raise RuntimeError("cache-only seed 후보 탐색 마커가 예상 형태와 다릅니다.")


def patch_gitignore(text):
    additions = ["results/", ".roll20_chrome_profile/", "stage4_assets/", "*.stage*.bak"]
    lines = text.splitlines()
    existing = {line.strip() for line in lines}
    if lines and lines[-1].strip():
        lines.append("")
    if "# Stage 4 local runtime data" not in existing:
        lines.append("# Stage 4 local runtime data")
    for item in additions:
        if item not in existing:
            lines.append(item)
    return "\n".join(lines).rstrip() + "\n"


def backup(path):
    if not path.exists():
        return
    target = path.with_suffix(path.suffix + ".stage4.bak")
    if not target.exists():
        shutil.copy2(path, target)


def copy_assets():
    for rel in NEW_FILES:
        source = ASSET_ROOT / rel
        target = ROOT / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            backup(target)
        shutil.copy2(source, target)
    req = Path(__file__).resolve().parent / "requirements-stage4.txt"
    req_target = ROOT / "requirements-stage4.txt"
    if req.resolve() != req_target.resolve():
        shutil.copy2(req, req_target)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()

    _verify_stage3()
    main_text = MAIN.read_text(encoding="utf-8")
    hybrid_text = HYBRID.read_text(encoding="utf-8")
    ignore_text = GITIGNORE.read_text(encoding="utf-8") if GITIGNORE.exists() else ""

    new_main = patch_main(main_text)
    new_hybrid = patch_hybrid(hybrid_text)
    new_ignore = patch_gitignore(ignore_text)

    if args.check:
        print("[시트 이동기] 4단계 패치 사전 점검 통과.")
        print("[시트 이동기] Roll20 연결은 읽기 전용이며 시트 필드를 수정하지 않습니다.")
        print("[시트 이동기] 정상 결과는 results/current/로 정리되고 Git에서 제외됩니다.")
        return 0

    for path in (MAIN, HYBRID, GITIGNORE):
        backup(path)
    MAIN.write_text(new_main, encoding="utf-8")
    HYBRID.write_text(new_hybrid, encoding="utf-8")
    GITIGNORE.write_text(new_ignore, encoding="utf-8")
    copy_assets()

    print("[시트 이동기] 4단계 패치를 적용했습니다.")
    print("[시트 이동기] results/current 구조, cache-only seed 호환, Roll20 읽기 전용 동명 캐릭터 탐색을 추가했습니다.")
    print("[시트 이동기] Roll20 시트 내용은 아직 수정하지 않습니다.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
