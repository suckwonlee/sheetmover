"""Apply Stage 3 Roll20 payload preparation to a Stage-2 checkout.

Run from E:\\sheet_mover:
    python apply_stage3.py --check
    python apply_stage3.py

The script fails closed unless Stage 2 markers are present.  It does not touch
translation implementation files or calculator.py.
"""
from __future__ import annotations

import argparse
from pathlib import Path
import shutil


ROOT = Path.cwd()
MOVER = ROOT / "sheet_mover" / "mover.py"
SOURCE = ROOT / "sheet_mover" / "source.py"
MAIN = ROOT / "sheet_mover" / "__main__.py"
TEST_STATUS = ROOT / "tests" / "test_translation_status.py"
PAYLOAD_MODULE = ROOT / "sheet_mover" / "roll20_payload.py"
CLEANUP_MODULE = ROOT / "sheet_mover" / "result_cleanup.py"
TEST_PAYLOAD = ROOT / "tests" / "test_stage3_payload.py"
TEST_CLEANUP = ROOT / "tests" / "test_result_cleanup.py"
GITIGNORE = ROOT / ".gitignore"


def replace_once(text, old, new, label):
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected exactly one source marker, found {count}")
    return text.replace(old, new, 1)


def _required_files():
    required = [
        MOVER, SOURCE, MAIN, TEST_STATUS, PAYLOAD_MODULE,
        CLEANUP_MODULE, TEST_PAYLOAD, TEST_CLEANUP,
    ]
    missing = [str(path) for path in required if not path.is_file()]
    if missing:
        raise RuntimeError(
            "필수 파일이 없습니다. ZIP을 E:\\sheet_mover 루트에 풀었는지 확인하세요: "
            + ", ".join(missing)
        )


def _verify_stage2(source_text, mover_text):
    required = [
        ("from .calculator import apply_stage2_calculations", source_text, "2단계 calculator import"),
        ("apply_stage2_calculations(sheet)", source_text, "2단계 calculator 적용"),
        ("현재 2단계 미리보기입니다.", mover_text, "2단계 mover 상태"),
    ]
    missing = [label for marker, text, label in required if marker not in text]
    if missing:
        raise RuntimeError(
            "3단계는 2단계 적용 완료 상태가 필요합니다. 누락: " + ", ".join(missing)
        )


def patch_mover(text):
    if "from .roll20_payload import build_roll20_payload" not in text:
        text = replace_once(
            text,
            "from dataclasses import asdict, dataclass\n",
            "from dataclasses import asdict, dataclass, field\n",
            "mover dataclass import",
        )
        text = replace_once(
            text,
            "from .hybrid_translator import TranslationError, Translator\n",
            "from .hybrid_translator import TranslationError, Translator\n"
            "from .roll20_payload import build_roll20_payload\n",
            "mover payload import",
        )

    if "roll20_payload: dict = field(default_factory=dict)" not in text:
        text = replace_once(
            text,
            "    applied: bool = False\n",
            "    applied: bool = False\n"
            "    roll20_payload: dict = field(default_factory=dict)\n",
            "PreparationResult payload field",
        )

    if '"roll20_payload": {}' not in text:
        text = replace_once(
            text,
            '                "roll20_tabs": [],\n                "raw_source": raw,\n',
            '                "roll20_tabs": [],\n'
            '                "roll20_payload": {},\n'
            '                "raw_source": raw,\n',
            "partial payload stage3 field",
        )

    if "roll20_payload = build_roll20_payload" not in text:
        text = replace_once(
            text,
            "        warnings = list(original.warnings)\n",
            "        self.report(97, \"Roll20용 반복 구조를 정리합니다.\")\n"
            "        roll20_payload = build_roll20_payload(\n"
            "            translated,\n"
            "            original.to_dict(),\n"
            "        )\n\n"
            "        warnings = list(original.warnings)\n",
            "stage3 payload build",
        )

    if "현재 3단계 미리보기입니다." not in text:
        old = (
            '        warnings.append(\n'
            '            "현재 2단계 미리보기입니다. 최종 능력치·HP·AC·레벨·숙련 보너스 "\n'
            '            "계산까지 완료했으며, Roll20 캐릭터 탐색과 입력은 아직 수행하지 않습니다."\n'
            '        )\n'
        )
        new = (
            '        warnings.append(\n'
            '            "현재 3단계 미리보기입니다. 최종 수치와 장비·주문·특성·행동 반복 구조를 "\n'
            '            "Roll20 입력 직전 payload로 정리했으며, Roll20에는 아직 입력하지 않습니다."\n'
            '        )\n'
        )
        text = replace_once(text, old, new, "stage3 warning")

    if "roll20_payload=roll20_payload" not in text:
        text = replace_once(
            text,
            "            [],\n            raw,\n        )\n",
            "            [],\n            raw,\n            roll20_payload=roll20_payload,\n        )\n",
            "PreparationResult stage3 payload",
        )

    return text


def patch_main(text):
    # Partial results: keep the concise failure summary, but detailed preserved
    # rows live only in the JSON file.
    start = (
        "            preserved = summary.get(\"original_preserved\") or []\n"
        "            if preserved:\n"
        "                print(\n"
        "                    \"[시트 이동기] 원문 유지 항목:\",\n"
        "                    file=sys.stderr,\n"
        "                    flush=True,\n"
        "                )\n"
        "                for item in preserved:\n"
        "                    if not isinstance(item, dict):\n"
        "                        continue\n"
        "                    preview = str(item.get(\"source_preview\") or \"\").strip()\n"
        "                    reason = str(item.get(\"reason\") or \"\").strip()\n"
        "                    detail = preview if not reason else f\"{preview} ({reason})\"\n"
        "                    print(\n"
        "                        f\"  - {detail}\",\n"
        "                        file=sys.stderr,\n"
        "                        flush=True,\n"
        "                    )\n"
    )
    if start in text:
        text = text.replace(start, "", 1)

    verbose_payload = (
        "        print(\n"
        "            json.dumps(\n"
        "                payload,\n"
        "                ensure_ascii=False,\n"
        "                indent=2,\n"
        "            )\n"
        "        )\n"
        "        return\n"
    )
    if verbose_payload in text:
        text = text.replace(verbose_payload, "        return\n", 1)
    elif "print(\n            json.dumps(\n                payload," in text:
        raise RuntimeError("CLI payload 출력 마커가 예상 형태와 다릅니다.")
    return text


def patch_status_test(text):
    if "self.assertIn(\"Thunderwave source preview\", text)" in text:
        text = text.replace(
            '        self.assertIn("Thunderwave source preview", text)\n',
            '        self.assertNotIn("Thunderwave source preview", text)\n'
            '        self.assertEqual(stdout.getvalue(), "")\n',
            1,
        )
    return text


def patch_gitignore(text):
    additions = [
        "sheet-result-*.json",
        "sheet-partial-*.json",
        "output_archive/",
    ]
    lines = text.splitlines()
    existing = {line.strip() for line in lines}
    if lines and lines[-1].strip():
        lines.append("")
    if "# Generated sheet-mover results" not in existing:
        lines.append("# Generated sheet-mover results")
    for item in additions:
        if item not in existing:
            lines.append(item)
    return "\n".join(lines).rstrip() + "\n"


def backup(path):
    backup_path = path.with_suffix(path.suffix + ".stage3.bak")
    if not backup_path.exists():
        shutil.copy2(path, backup_path)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()

    _required_files()
    source_text = SOURCE.read_text(encoding="utf-8")
    mover_text = MOVER.read_text(encoding="utf-8")
    main_text = MAIN.read_text(encoding="utf-8")
    status_text = TEST_STATUS.read_text(encoding="utf-8")
    ignore_text = GITIGNORE.read_text(encoding="utf-8") if GITIGNORE.exists() else ""

    if "현재 3단계 미리보기입니다." in mover_text and "roll20_payload=roll20_payload" in mover_text:
        print("[시트 이동기] 3단계 패치가 이미 적용되어 있습니다.")
        return 0

    _verify_stage2(source_text, mover_text)
    new_mover = patch_mover(mover_text)
    new_main = patch_main(main_text)
    new_status = patch_status_test(status_text)
    new_ignore = patch_gitignore(ignore_text)

    if args.check:
        print("[시트 이동기] 3단계 패치 사전 점검 통과.")
        print("[시트 이동기] 2단계 계산기와 v16.7 번역 코드는 수정하지 않습니다.")
        print("[시트 이동기] 실행 결과 JSON은 Git에서 제외됩니다.")
        return 0

    for path in (MOVER, MAIN, TEST_STATUS):
        backup(path)
    if GITIGNORE.exists():
        backup(GITIGNORE)

    MOVER.write_text(new_mover, encoding="utf-8")
    MAIN.write_text(new_main, encoding="utf-8")
    TEST_STATUS.write_text(new_status, encoding="utf-8")
    GITIGNORE.write_text(new_ignore, encoding="utf-8")

    print("[시트 이동기] 3단계 패치를 적용했습니다.")
    print("[시트 이동기] Roll20용 payload 생성, 결과 archive 도구, 콘솔 출력 정리, .gitignore 반영 완료.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
