# apply_stage4.py
from __future__ import annotations

import argparse
from pathlib import Path
import shutil

ROOT = Path.cwd()
MAIN = ROOT / "sheet_mover" / "__main__.py"
HYBRID = ROOT / "sheet_mover" / "hybrid_translator.py"
CONNECTION = ROOT / "sheet_mover" / "roll20_connection.py"
GITIGNORE = ROOT / ".gitignore"
REQUIREMENTS = ROOT / "requirements.txt"
TEST_CLEANUP = ROOT / "tests" / "test_result_cleanup.py"
TEST_CLI = ROOT / "tests" / "test_cli_output_save.py"

REQUIRED_STAGE4_FILES = [
    ROOT / "sheet_mover" / "result_store.py",
    ROOT / "sheet_mover" / "result_cleanup.py",
    ROOT / "sheet_mover" / "roll20_browser.py",
    CONNECTION,
    ROOT / "tests" / "test_stage4_results.py",
    ROOT / "tests" / "test_roll20_connection.py",
]


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected exactly one source marker, found {count}")
    return text.replace(old, new, 1)


def verify_base() -> None:
    required = [
        MAIN,
        HYBRID,
        CONNECTION,
        GITIGNORE,
        REQUIREMENTS,
        TEST_CLEANUP,
        TEST_CLI,
        *REQUIRED_STAGE4_FILES,
    ]
    missing = [str(path) for path in required if not path.is_file()]
    if missing:
        raise RuntimeError("4단계 적용에 필요한 파일이 없습니다: " + ", ".join(missing))

    mover = (ROOT / "sheet_mover" / "mover.py").read_text(encoding="utf-8")
    if "roll20_payload=roll20_payload" not in mover:
        raise RuntimeError("3단계 roll20_payload 적용 상태를 확인할 수 없습니다.")


def patch_main(text: str) -> str:
    if "\nimport re\n" not in text:
        text = replace_once(text, "import os\n", "import os\nimport re\n", "CLI re import")

    if "from .result_store import ARCHIVE_DIR, default_result_path" not in text:
        text = replace_once(
            text,
            "from .source import fetch_character, normalize_character\n",
            "from .source import fetch_character, normalize_character\n"
            "from .result_store import ARCHIVE_DIR, default_result_path\n",
            "CLI result_store import",
        )

    old_default = (
        "    return Path(\n"
        "        f\"sheet-result-{safe_source_id}-{now:%Y%m%d-%H%M%S}.json\"\n"
        "    )\n"
    )
    new_default = "    return default_result_path(safe_source_id, now=now)\n"
    if old_default in text:
        text = text.replace(old_default, new_default, 1)
    elif new_default not in text:
        raise RuntimeError("CLI 기본 결과 경로 마커가 예상 형태와 다릅니다.")

    text = text.replace(
        "생략하면 현재 폴더에 sheet-result-<source_id>-<시각>.json으로 저장",
        "생략하면 results/current/에 sheet-result-<source_id>-<시각>.json으로 저장",
    )

    helper = (
        "\n\ndef _source_id_from_source(source):\n"
        "    match = re.search(r\"/characters/(\\d+)\", str(source or \"\"))\n"
        "    return match.group(1) if match else None\n"
    )
    if "def _source_id_from_source(source):" not in text:
        text = replace_once(
            text,
            "\n\ndef main():\n",
            helper + "\n\ndef main():\n",
            "CLI source id helper",
        )

    if '"--roll20-check"' not in text:
        marker = (
            '    parser.add_argument(\n'
            '        "--setup-google-glossary",\n'
            '        action="store_true",\n'
            '        help="현재 glossary.json으로 Google Cloud Translation v3 용어집을 생성/확인",\n'
            '    )\n'
        )
        addition = marker + (
            '    parser.add_argument(\n'
            '        "--roll20-check",\n'
            '        action="store_true",\n'
            '        help="Roll20 시트를 수정하지 않고 동명 캐릭터와 내부 ID만 확인",\n'
            '    )\n'
            '    parser.add_argument(\n'
            '        "--roll20-result",\n'
            '        help="Roll20 확인에 사용할 sheet-result JSON. 생략하면 source ID 기준 최신 정상 결과 사용",\n'
            '    )\n'
            '    parser.add_argument(\n'
            '        "--roll20-cdp-url",\n'
            '        default="http://127.0.0.1:9222",\n'
            '        help="Roll20 전용 Chrome의 CDP 주소",\n'
            '    )\n'
        )
        text = replace_once(text, marker, addition, "CLI roll20 arguments")

    branch = (
        "\n"
        "    if args.roll20_check:\n"
        "        from .roll20_connection import check_roll20_target\n"
        "\n"
        "        source_id = _source_id_from_source(args.source)\n"
        "        target, target_path = check_roll20_target(\n"
        "            result_path=args.roll20_result,\n"
        "            source_id=source_id,\n"
        "            cdp_url=args.roll20_cdp_url,\n"
        "            save=True,\n"
        "            on_progress=_console_progress,\n"
        "        )\n"
        "        print(\n"
        "            f\"[시트 이동기] 대상 캐릭터: {target.character_name}\",\n"
        "            file=sys.stderr,\n"
        "            flush=True,\n"
        "        )\n"
        "        print(\n"
        "            f\"[시트 이동기] Roll20 Character ID: {target.roll20_character_id}\",\n"
        "            file=sys.stderr,\n"
        "            flush=True,\n"
        "        )\n"
        "        if target_path:\n"
        "            print(\n"
        "                f\"[시트 이동기] 연결 정보를 저장했습니다: {Path(target_path).resolve()}\",\n"
        "                file=sys.stderr,\n"
        "                flush=True,\n"
        "            )\n"
        "        print(\n"
        "            \"[시트 이동기] Roll20 시트 내용은 수정하지 않았습니다.\",\n"
        "            file=sys.stderr,\n"
        "            flush=True,\n"
        "        )\n"
        "        return\n"
    )
    if "if args.roll20_check:" not in text:
        text = replace_once(
            text,
            "    args = parser.parse_args()\n\n",
            "    args = parser.parse_args()\n" + branch + "\n",
            "CLI roll20 branch",
        )

    old_partial = (
        "                filename = Path(\n"
        "                    f\"sheet-partial-{datetime.now():%Y%m%d-%H%M%S}.json\"\n"
        "                )\n"
    )
    new_partial = (
        "                archive_dir = Path(ARCHIVE_DIR)\n"
        "                archive_dir.mkdir(parents=True, exist_ok=True)\n"
        "                filename = archive_dir / (\n"
        "                    f\"sheet-partial-{datetime.now():%Y%m%d-%H%M%S}.json\"\n"
        "                )\n"
    )
    if old_partial in text:
        text = text.replace(old_partial, new_partial, 1)
    elif "archive_dir = Path(ARCHIVE_DIR)" not in text:
        raise RuntimeError("CLI partial 저장 경로 마커가 예상 형태와 다릅니다.")

    return text


def patch_hybrid(text: str) -> str:
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
        "            current = sorted(\n"
        "                (root / \"results\" / \"current\").glob(\"sheet-result-*.json\"),\n"
        "                key=self._result_candidate_sort_key,\n"
        "                reverse=True,\n"
        "            )\n"
        "            legacy = sorted(\n"
        "                root.glob(\"sheet-result-*.json\"),\n"
        "                key=self._result_candidate_sort_key,\n"
        "                reverse=True,\n"
        "            )\n"
        "            return (current + legacy)[:24]\n"
        "        except OSError:\n"
        "            return []\n"
    )
    if old in text:
        return text.replace(old, new, 1)
    if '(root / "results" / "current").glob("sheet-result-*.json")' in text:
        return text
    raise RuntimeError("cache-only seed 후보 탐색 마커가 예상 형태와 다릅니다.")


def patch_connection(text: str) -> str:
    text = text.replace(
        'STAGE4_VERSION = "2026-10-06-stage4-roll20-target-v1"',
        'STAGE4_VERSION = "2026-10-06-stage4-roll20-target-v2"',
    )
    text = text.replace(
        "Selenium이 필요합니다. `.venv`에서 `pip install -r requirements-stage4.txt`를 한 번 실행하세요.",
        "Selenium이 필요합니다. `.venv`에서 `pip install -r requirements.txt`를 실행하세요.",
    )

    old_sig = (
        "def check_roll20_target(result_path=None, cdp_url=DEFAULT_CDP_URL, save=True, "
        "driver_factory=None, on_progress=None):\n"
    )
    new_sig = (
        "def check_roll20_target(result_path=None, source_id=None, cdp_url=DEFAULT_CDP_URL, save=True, "
        "driver_factory=None, on_progress=None):\n"
    )
    if old_sig in text:
        text = text.replace(old_sig, new_sig, 1)
    elif new_sig not in text:
        raise RuntimeError("Roll20 check 함수 시그니처가 예상 형태와 다릅니다.")

    old_path = "    path = Path(result_path) if result_path else latest_complete_result()\n"
    new_path = (
        "    path = Path(result_path) if result_path else "
        "latest_complete_result(source_id=source_id)\n"
    )
    if old_path in text:
        text = text.replace(old_path, new_path, 1)
    elif new_path not in text:
        raise RuntimeError("Roll20 결과 선택 마커가 예상 형태와 다릅니다.")

    if 'parser.add_argument("--source-id"' not in text:
        marker = (
            '    parser.add_argument("--result", help="사용할 sheet-result JSON. '
            '생략하면 최신 정상 결과 자동 선택")\n'
        )
        addition = marker + (
            '    parser.add_argument("--source-id", '
            'help="자동 선택 시 사용할 D&D Beyond source_character_id")\n'
        )
        text = replace_once(text, marker, addition, "Roll20 source-id argument")

    call_marker = (
        "    target, target_path = check_roll20_target(\n"
        "        result_path=args.result,\n"
        "        cdp_url=args.cdp_url,\n"
    )
    call_new = (
        "    target, target_path = check_roll20_target(\n"
        "        result_path=args.result,\n"
        "        source_id=args.source_id,\n"
        "        cdp_url=args.cdp_url,\n"
    )
    if call_marker in text:
        text = text.replace(call_marker, call_new, 1)
    elif "source_id=args.source_id" not in text:
        raise RuntimeError("Roll20 CLI 호출 마커가 예상 형태와 다릅니다.")

    return text


def patch_gitignore(text: str) -> str:
    additions = [
        "results/",
        ".roll20_chrome_profile/",
        "stage4_assets/",
        "*.stage4.bak",
    ]
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


def patch_requirements(text: str) -> str:
    lines = text.splitlines()
    if not any(line.strip().lower().startswith("selenium") for line in lines):
        lines.append("selenium>=4.20,<5")
    return "\n".join(lines).rstrip() + "\n"


TEST_CLEANUP_CONTENT = '''import json
import tempfile
import unittest
from pathlib import Path

from sheet_mover.result_cleanup import execute_cleanup, plan_cleanup


def _write_result(path, status="complete"):
    path.write_text(
        json.dumps({"translation_summary": {"status": status}}),
        encoding="utf-8",
    )


class ResultCleanupTests(unittest.TestCase):
    def test_keeps_latest_two_complete_results_in_current_and_archives_rest(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            names = [
                "sheet-result-123-20261001-010101.json",
                "sheet-result-123-20261002-010101.json",
                "sheet-result-123-20261003-010101.json",
            ]
            for name in names:
                _write_result(root / name)
            _write_result(
                root / "sheet-partial-20261003-020202.json",
                "partial",
            )

            plan = plan_cleanup(root, 2)
            self.assertEqual(
                {p.name for p in plan.move_to_current},
                set(names[-2:]),
            )
            self.assertIn(names[0], {p.name for p in plan.archive})
            self.assertIn(
                "sheet-partial-20261003-020202.json",
                {p.name for p in plan.archive},
            )

    def test_dry_run_moves_nothing(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            old = root / "sheet-result-123-20261001-010101.json"
            new = root / "sheet-result-123-20261002-010101.json"
            _write_result(old)
            _write_result(new)

            result = execute_cleanup(
                root,
                keep_complete_per_character=1,
                dry_run=True,
            )

            self.assertTrue(old.exists())
            self.assertTrue(new.exists())
            self.assertEqual(result["moved_current"], [])
            self.assertEqual(result["moved_archive"], [])

    def test_execute_moves_latest_to_current_and_old_to_archive(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            old = root / "sheet-result-123-20261001-010101.json"
            new = root / "sheet-result-123-20261002-010101.json"
            _write_result(old)
            _write_result(new)

            result = execute_cleanup(
                root,
                keep_complete_per_character=1,
            )

            self.assertFalse(old.exists())
            self.assertFalse(new.exists())
            self.assertTrue(
                (root / "output_archive" / old.name).is_file()
            )
            self.assertTrue(
                (root / "results" / "current" / new.name).is_file()
            )
            self.assertEqual(len(result["moved_archive"]), 1)
            self.assertEqual(len(result["moved_current"]), 1)


if __name__ == "__main__":
    unittest.main()
'''


def patch_cli_test(text: str) -> str:
    marker = (
        "        self.assertEqual(\n"
        "            path.name,\n"
        "            \"sheet-result-170892133-20260911-163304.json\",\n"
        "        )\n"
    )
    addition = marker + (
        "        self.assertEqual(\n"
        "            path.as_posix(),\n"
        "            \"results/current/sheet-result-170892133-20260911-163304.json\",\n"
        "        )\n"
    )
    if "results/current/sheet-result-170892133-20260911-163304.json" not in text:
        text = replace_once(text, marker, addition, "CLI output path test")
    return text


def backup(path: Path) -> None:
    if not path.exists():
        return
    target = path.with_name(path.name + ".stage4.bak")
    if not target.exists():
        shutil.copy2(path, target)


def planned_contents():
    return {
        MAIN: patch_main(MAIN.read_text(encoding="utf-8")),
        HYBRID: patch_hybrid(HYBRID.read_text(encoding="utf-8")),
        CONNECTION: patch_connection(CONNECTION.read_text(encoding="utf-8")),
        GITIGNORE: patch_gitignore(GITIGNORE.read_text(encoding="utf-8")),
        REQUIREMENTS: patch_requirements(REQUIREMENTS.read_text(encoding="utf-8")),
        TEST_CLEANUP: TEST_CLEANUP_CONTENT,
        TEST_CLI: patch_cli_test(TEST_CLI.read_text(encoding="utf-8")),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()

    verify_base()
    contents = planned_contents()

    if args.check:
        print("[시트 이동기] 4단계 적용 사전 점검 통과.")
        print("[시트 이동기] 3단계 payload와 4단계 모듈 파일을 확인했습니다.")
        print("[시트 이동기] 적용 후 Roll20은 동명 캐릭터 탐색/ID 확인만 수행합니다.")
        print("[시트 이동기] Roll20 시트 값은 수정하지 않습니다.")
        print("[시트 이동기] Google Translation API 호출을 추가하지 않습니다.")
        return 0

    for path, content in contents.items():
        backup(path)
        path.write_text(content, encoding="utf-8")

    print("[시트 이동기] 4단계 코드를 적용했습니다.")
    print("[시트 이동기] 정상 결과 기본 경로: results/current/")
    print("[시트 이동기] partial 결과 기본 경로: output_archive/")
    print("[시트 이동기] cache-only seed: results/current 우선 + 기존 루트 호환")
    print("[시트 이동기] Roll20 연결: read-only 동명 캐릭터 탐색/Character ID 확보")
    print("[시트 이동기] Roll20 시트 내용은 수정하지 않습니다.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
