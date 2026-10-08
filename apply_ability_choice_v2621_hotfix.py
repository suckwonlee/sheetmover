# -*- coding: utf-8 -*-
from __future__ import annotations

import ast
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile


ROOT = Path.cwd()
PAYLOAD = Path(__file__).resolve().parent / "payload"

RUNTIME = ROOT / "sheet_mover" / "runtime_integrity_v262.py"
TEST = ROOT / "tests" / "test_runtime_integrity_v2621_hotfix.py"

SOURCE = ROOT / "sheet_mover" / "source.py"
FULL_RUN = ROOT / "sheet_mover" / "full_run.py"

SOURCE_HOOK_MARKER = "# runtime-integrity-v2.6.2 source hook"
FULL_RUN_HOOK_MARKER = "# runtime-integrity-v2.6.2 cache-refresh hook"


def parse(path):
    ast.parse(
        path.read_text(encoding="utf-8"),
        filename=str(path),
    )


def ignore(_dir, names):
    result = set()
    for name in names:
        if name in {
            ".git",
            ".venv",
            "venv",
            "dist",
            "build",
            "__pycache__",
            ".idea",
            ".roll20_chrome_profile",
        }:
            result.add(name)
        elif name.endswith(".pyc"):
            result.add(name)
    return result


def verify_existing_hooks(root):
    source = root / "sheet_mover" / "source.py"
    full_run = root / "sheet_mover" / "full_run.py"

    if not source.is_file() or not full_run.is_file():
        raise RuntimeError(
            "sheet_mover/source.py 또는 sheet_mover/full_run.py가 없습니다."
        )

    source_text = source.read_text(encoding="utf-8")
    full_run_text = full_run.read_text(encoding="utf-8")

    if SOURCE_HOOK_MARKER not in source_text:
        raise RuntimeError("v2.6.2 source hook을 찾지 못했습니다.")

    if FULL_RUN_HOOK_MARKER not in full_run_text:
        raise RuntimeError("v2.6.2 cache-refresh hook을 찾지 못했습니다.")

    if "runtime_integrity_v262" not in source_text:
        raise RuntimeError("source.py의 v2.6.2 runtime 연결이 없습니다.")

    if "runtime_integrity_v262" not in full_run_text:
        raise RuntimeError("full_run.py의 v2.6.2 runtime 연결이 없습니다.")


def apply_tree(root):
    verify_existing_hooks(root)

    runtime_src = (
        PAYLOAD
        / "sheet_mover"
        / "runtime_integrity_v262.py"
    )
    test_src = (
        PAYLOAD
        / "tests"
        / "test_runtime_integrity_v2621_hotfix.py"
    )

    if not runtime_src.is_file() or not test_src.is_file():
        raise RuntimeError(
            "payload 파일이 없습니다. ZIP 폴더 구조를 유지해 주세요."
        )

    runtime_dst = (
        root
        / "sheet_mover"
        / "runtime_integrity_v262.py"
    )
    test_dst = (
        root
        / "tests"
        / "test_runtime_integrity_v2621_hotfix.py"
    )

    runtime_dst.parent.mkdir(parents=True, exist_ok=True)
    test_dst.parent.mkdir(parents=True, exist_ok=True)

    shutil.copy2(runtime_src, runtime_dst)
    shutil.copy2(test_src, test_dst)

    parse(runtime_dst)
    parse(test_dst)


def run_tests(root):
    env = os.environ.copy()
    env["PYTHONPATH"] = (
        str(root)
        + os.pathsep
        + env.get("PYTHONPATH", "")
    )

    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "unittest",
            "discover",
            "-s",
            "tests",
            "-v",
        ],
        cwd=root,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=420,
    )

    output = completed.stdout or ""

    if completed.returncode != 0:
        raise RuntimeError(
            "복제본 전체 테스트 실패. 실제 프로젝트는 수정하지 않았습니다.\n"
            + "\n".join(output.splitlines()[-300:])
        )

    match = re.search(
        r"Ran\s+(\d+)\s+tests?",
        output,
    )
    return int(match.group(1)) if match else None


def backup(path):
    if not path.exists():
        return

    target = path.with_suffix(
        path.suffix + ".pre-v2.6.2.1.bak"
    )
    if not target.exists():
        shutil.copy2(path, target)


def main():
    print("[시트 이동기] ability-choice v2.6.2.1 hotfix 준비")
    print("- 실제 DDB 위치 raw_source.choices.choiceDefinitions 지원")
    print("- 실제 케이스 stale modifier 1729/1821만 제외")
    print("- 거인 힘의 벨트 착용/미착용 회귀 테스트 포함")
    print("- 실제 파일 수정 전 복제본 전체 unittest 실행")

    if not RUNTIME.is_file():
        raise RuntimeError(
            "기존 v2.6.2 runtime_integrity_v262.py가 없습니다."
        )

    with tempfile.TemporaryDirectory(
        prefix="sheetmover-v2621-"
    ) as temp:
        sandbox = Path(temp) / "repo"

        shutil.copytree(
            ROOT,
            sandbox,
            ignore=ignore,
        )

        apply_tree(sandbox)

        print("[시트 이동기] 복제본 전체 unittest 실행...")
        count = run_tests(sandbox)

        print(
            "[시트 이동기] 복제본 전체 테스트 통과"
            + (
                f": {count}개"
                if count is not None
                else ""
            )
        )

        backup(RUNTIME)
        backup(TEST)

        for relative in (
            "sheet_mover/runtime_integrity_v262.py",
            "tests/test_runtime_integrity_v2621_hotfix.py",
        ):
            src = sandbox / relative
            dst = ROOT / relative
            dst.parent.mkdir(parents=True, exist_ok=True)

            tmp = dst.with_name(
                dst.name + ".v2621.tmp"
            )
            shutil.copy2(src, tmp)
            os.replace(tmp, dst)
            parse(dst)

    print("[시트 이동기] ability-choice v2.6.2.1 hotfix 적용 완료")
    print("- 기존 번역 캐시는 그대로 재사용합니다.")
    print("- 다음 실행에서 stale modifier 2개 제외 문구가 나와야 정상입니다.")
    print("- 기대 능력치: STR 25 / DEX 8 / CON 16 / INT 8 / WIS 10 / CHA 21")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(
            f"[시트 이동기] ability-choice v2.6.2.1 hotfix 실패: {exc}",
            file=sys.stderr,
        )
        raise
