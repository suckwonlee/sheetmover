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

SOURCE = ROOT / "sheet_mover" / "source.py"
FULL_RUN = ROOT / "sheet_mover" / "full_run.py"
RUNTIME = ROOT / "sheet_mover" / "runtime_integrity_v262.py"
TEST = ROOT / "tests" / "test_runtime_integrity_v262.py"

SOURCE_HOOK_MARKER = "# runtime-integrity-v2.6.2 source hook"
FULL_RUN_HOOK_MARKER = "# runtime-integrity-v2.6.2 cache-refresh hook"

SOURCE_HOOK = r"""
# runtime-integrity-v2.6.2 source hook
from .runtime_integrity_v262 import install_source_integrity as _install_source_integrity_v262
normalize_character = _install_source_integrity_v262(normalize_character)
"""

FULL_RUN_HOOK = r"""
# runtime-integrity-v2.6.2 cache-refresh hook
from .runtime_integrity_v262 import install_reusable_result_refresh as _install_reusable_result_refresh_v262
_find_reusable_result = _install_reusable_result_refresh_v262(_find_reusable_result)
"""

OLD_V261_SOURCE_HOOK = r"""
# runtime-integrity-v2.6.1 source hook
from .runtime_integrity_v261 import install_source_integrity as _install_source_integrity_v261
normalize_character = _install_source_integrity_v261(normalize_character)
"""

OLD_V261_FULL_RUN_HOOK = r"""
# runtime-integrity-v2.6.1 cache-refresh hook
from .runtime_integrity_v261 import install_reusable_result_refresh as _install_reusable_result_refresh_v261
_find_reusable_result = _install_reusable_result_refresh_v261(_find_reusable_result)
"""


def parse(path):
    ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def _remove_hook_text(text, hook):
    block = hook.strip()
    if not block:
        return text
    text = text.replace("\n" + block + "\n", "\n")
    text = text.replace(block + "\n", "")
    text = text.replace("\n" + block, "\n")
    text = text.replace(block, "")
    return text


def remove_v261_hooks(path):
    text = path.read_text(encoding="utf-8")
    original = text

    if path.name == "source.py":
        text = _remove_hook_text(text, OLD_V261_SOURCE_HOOK)
        marker = "# runtime-integrity-v2.6.1 source hook"
    else:
        text = _remove_hook_text(text, OLD_V261_FULL_RUN_HOOK)
        marker = "# runtime-integrity-v2.6.1 cache-refresh hook"

    if marker in text:
        raise RuntimeError(
            f"{path}: 기존 v2.6.1 hook을 안전하게 제거하지 못했습니다."
        )

    if text != original:
        ast.parse(text, filename=str(path))
        path.write_text(text, encoding="utf-8")
        return True
    return False


def inject(path, marker, hook, required):
    text = path.read_text(encoding="utf-8")
    if marker in text:
        return False

    for wanted in required:
        if wanted not in text:
            raise RuntimeError(
                f"{path}: 예상한 시트 이동기 코드가 없습니다: {wanted}"
            )

    main_guard = '\nif __name__ == "__main__":\n'
    pos = text.rfind(main_guard)

    if pos >= 0:
        patched = text[:pos] + "\n" + hook.strip() + "\n" + text[pos:]
    else:
        patched = text.rstrip() + "\n\n" + hook.strip() + "\n"

    ast.parse(patched, filename=str(path))
    path.write_text(patched, encoding="utf-8")
    return True


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


def apply_tree(root):
    runtime_src = PAYLOAD / "sheet_mover" / "runtime_integrity_v262.py"
    test_src = PAYLOAD / "tests" / "test_runtime_integrity_v262.py"

    if not runtime_src.is_file() or not test_src.is_file():
        raise RuntimeError(
            "payload 파일이 없습니다. ZIP의 폴더 구조를 유지한 채 압축을 풀어 주세요."
        )

    runtime_dst = root / "sheet_mover" / "runtime_integrity_v262.py"
    test_dst = root / "tests" / "test_runtime_integrity_v262.py"

    runtime_dst.parent.mkdir(parents=True, exist_ok=True)
    test_dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(runtime_src, runtime_dst)
    shutil.copy2(test_src, test_dst)

    source = root / "sheet_mover" / "source.py"
    full_run = root / "sheet_mover" / "full_run.py"

    if not source.is_file() or not full_run.is_file():
        raise RuntimeError(
            "sheet_mover/source.py 또는 sheet_mover/full_run.py가 없습니다."
        )

    # v2.6.1이 혹시 이미 적용된 로컬에서도 이중 wrapper가 되지 않도록
    # 복제본에서 먼저 기존 v2.6.1 hook만 제거합니다.
    remove_v261_hooks(source)
    remove_v261_hooks(full_run)

    inject(
        source,
        SOURCE_HOOK_MARKER,
        SOURCE_HOOK,
        [
            "def normalize_character(data):",
            "apply_stage2_calculations(sheet)",
        ],
    )
    inject(
        full_run,
        FULL_RUN_HOOK_MARKER,
        FULL_RUN_HOOK,
        [
            "def _find_reusable_result(",
            "def run_full_move(",
        ],
    )

    for path in (runtime_dst, test_dst, source, full_run):
        parse(path)


def run_tests(root):
    env = os.environ.copy()
    env["PYTHONPATH"] = str(root) + os.pathsep + env.get("PYTHONPATH", "")

    completed = subprocess.run(
        [sys.executable, "-m", "unittest", "discover", "-s", "tests", "-v"],
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
            + "\n".join(output.splitlines()[-280:])
        )

    match = re.search(r"Ran\s+(\d+)\s+tests?", output)
    return int(match.group(1)) if match else None


def backup(path):
    if not path.exists():
        return
    target = path.with_suffix(path.suffix + ".pre-v2.6.2.bak")
    if not target.exists():
        shutil.copy2(path, target)


def main():
    print("[시트 이동기] ability-choice v2.6.2 준비")
    print("- ASI에서 Feat을 고른 경우의 stale generic +1/+1만 제외")
    print("- choice group + componentTypeId + componentId를 함께 검증")
    print("- Belt of Fire Giant Strength STR 25 유지")
    print("- 실제 선택된 ASI와 Resilient 보너스 유지")
    print("- 같은 D&D 원본이면 기존 번역 결과 재사용")
    print("- 실제 파일 수정 전 복제본 전체 unittest 실행")

    if not SOURCE.is_file() or not FULL_RUN.is_file():
        raise RuntimeError("E:\\sheet_mover 폴더에서 실행해 주세요.")

    with tempfile.TemporaryDirectory(prefix="sheetmover-v262-") as temp:
        sandbox = Path(temp) / "repo"

        shutil.copytree(ROOT, sandbox, ignore=ignore)
        apply_tree(sandbox)

        print("[시트 이동기] 복제본 전체 unittest 실행...")
        count = run_tests(sandbox)
        print(
            "[시트 이동기] 복제본 전체 테스트 통과"
            + (f": {count}개" if count is not None else "")
        )

        # 여기까지 성공한 뒤에만 실제 프로젝트를 수정합니다.
        for path in (SOURCE, FULL_RUN, RUNTIME, TEST):
            backup(path)

        for relative in (
            "sheet_mover/runtime_integrity_v262.py",
            "tests/test_runtime_integrity_v262.py",
            "sheet_mover/source.py",
            "sheet_mover/full_run.py",
        ):
            src = sandbox / relative
            dst = ROOT / relative
            dst.parent.mkdir(parents=True, exist_ok=True)

            tmp = dst.with_name(dst.name + ".v262.tmp")
            shutil.copy2(src, tmp)
            os.replace(tmp, dst)
            parse(dst)

    print("[시트 이동기] ability-choice v2.6.2 적용 완료")
    print("- 기존 번역 결과는 다시 번역하지 않습니다.")
    print("- 같은 raw_source 캐시에서 계산값과 Roll20 payload만 갱신합니다.")
    print("- 기대 능력치: STR 25 / DEX 8 / CON 16 / INT 8 / WIS 10 / CHA 21")
    print("- 다음: python main.py")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(
            f"[시트 이동기] ability-choice v2.6.2 실패: {exc}",
            file=sys.stderr,
        )
        raise
