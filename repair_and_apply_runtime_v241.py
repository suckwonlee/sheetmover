# -*- coding: utf-8 -*-
from __future__ import annotations

import ast
import importlib.util
from pathlib import Path
import py_compile
import subprocess
import sys


ROOT = Path.cwd()
BROKEN = ROOT / "apply_runtime_integrity_v24.py"
FIXED = ROOT / "apply_runtime_integrity_v241.py"


def _replace_generated_assertions(text: str) -> tuple[str, int]:
    lines = text.splitlines()
    out = []
    replaced = 0
    i = 0

    while i < len(lines):
        line = lines[i]

        if "self.assertNotIn(" not in line:
            out.append(line)
            i += 1
            continue

        # Collect one assertNotIn call.  The broken v2.4 generated-test
        # assertions are small multi-line calls ending at a line containing
        # only ")".
        block = [line]
        j = i + 1
        while j < len(lines) and len(block) < 10:
            block.append(lines[j])
            if lines[j].strip() == ")":
                break
            j += 1

        blob = "\n".join(block)
        indent = line[: len(line) - len(line.lstrip())]

        if "실패 기록:" in blob:
            out.append(
                indent + 'self.assertNotIn("실패 기록:", source)'
            )
            replaced += 1
            i = j + 1
            continue

        if "저장된 결과 [" in blob:
            out.append(
                indent + 'self.assertNotIn("저장된 결과 [", source)'
            )
            replaced += 1
            i = j + 1
            continue

        out.extend(block)
        i = j + 1

    return "\n".join(out) + ("\n" if text.endswith("\n") else ""), replaced


def _validate_installer(path: Path):
    py_compile.compile(str(path), doraise=True)

    spec = importlib.util.spec_from_file_location(
        "sheetmover_runtime_v241_fixed",
        path,
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("수정된 설치기를 불러오지 못했습니다.")

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    generated = module.generated_tests()
    if not isinstance(generated, dict) or not generated:
        raise RuntimeError("설치기가 생성할 테스트 목록을 확인하지 못했습니다.")

    for test_path, source in generated.items():
        ast.parse(source, filename=str(test_path))

    return len(generated)


def main():
    if not BROKEN.is_file():
        raise RuntimeError(
            f"{BROKEN} 파일이 없습니다. "
            "v2.4 ZIP을 E:\\sheet_mover에 풀어둔 상태에서 실행하세요."
        )

    original = BROKEN.read_text(encoding="utf-8")
    fixed, replaced = _replace_generated_assertions(original)

    if replaced == 0:
        raise RuntimeError(
            "v2.4의 잘못된 테스트 assertion을 찾지 못했습니다. "
            "현재 설치기 상태가 예상과 다릅니다."
        )

    FIXED.write_text(fixed, encoding="utf-8")
    count = _validate_installer(FIXED)

    print("[시트 이동기] v2.4.1 설치기 복구 완료")
    print(f"- 잘못된 테스트 assertion {replaced}개 수정")
    print(f"- 설치기 Python 구문 검증 통과")
    print(f"- 설치기가 생성할 테스트 코드 {count}개 구문 검증 통과")
    print("- 이제 복구된 설치기의 복제본 전체 테스트를 실행합니다.")

    completed = subprocess.run(
        [sys.executable, str(FIXED)],
        cwd=ROOT,
    )
    if completed.returncode != 0:
        raise SystemExit(completed.returncode)

    print("[시트 이동기] v2.4.1 적용까지 완료")
    print("다음: python main.py")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(
            f"[시트 이동기] v2.4.1 복구 실패: {exc}",
            file=sys.stderr,
        )
        raise
