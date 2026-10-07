# -*- coding: utf-8 -*-
from __future__ import annotations

import ast
import importlib.util
from pathlib import Path
import py_compile
import subprocess
import sys

ROOT = Path.cwd()
SOURCE = ROOT / "apply_runtime_integrity_v241.py"
FIXED = ROOT / "apply_runtime_integrity_v242.py"


def replace_exact(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(
            f"{label}: 예상 문자열 1개가 필요한데 {count}개를 찾았습니다."
        )
    return text.replace(old, new, 1)


def validate_installer(path: Path) -> int:
    py_compile.compile(str(path), doraise=True)

    spec = importlib.util.spec_from_file_location("runtime_v242_fixed", path)
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


def main() -> int:
    if not SOURCE.is_file():
        raise RuntimeError(
            f"{SOURCE}가 없습니다. 직전에 repair_and_apply_runtime_v241.py를 "
            "실행한 E:\\sheet_mover에서 실행하세요."
        )

    text = SOURCE.read_text(encoding="utf-8")

    # v2.4.1에서 불필요하게 full-run report 위치까지 workers 폴더로 옮긴 것이
    # 기존 worker protocol 테스트 1개를 깨뜨렸다. 로그 하나 요구와 무관한
    # 상태 파일이므로 기존 results/current 계약을 그대로 유지한다.
    text = replace_exact(
        text,
        'data_dir() / "workers" / run_id / "full-run.json"',
        'data_dir() / "results" / "current" / f"full-run-{run_id}.json"',
        "full-run report 경로 복구",
    )

    # test_full_run의 report()도 원래 계약을 그대로 보도록 되돌린다.
    text = replace_exact(
        text,
        'self.root / "workers/fixture-run/full-run.json"',
        'self.root / "results/current/full-run-fixture-run.json"',
        "test_full_run report 경로 복구",
    )

    # 출력에서 이번 보정본임을 구분한다. 코어 버전 문자열은 그대로 두어
    # 기존 v2.4.1 설치기의 idempotent 검사와 호환되게 한다.
    text = text.replace(
        "runtime-integrity v2.4.1 준비",
        "runtime-integrity v2.4.2 준비",
    )
    text = text.replace(
        "runtime-integrity v2.4.1 적용 완료",
        "runtime-integrity v2.4.2 적용 완료",
    )

    FIXED.write_text(text, encoding="utf-8")
    generated_count = validate_installer(FIXED)

    print("[시트 이동기] v2.4.2 설치기 보정 완료")
    print("- full-run report는 기존 results/current 위치 유지")
    print("- worker protocol 계약 유지")
    print("- worker 폴더의 run.log 하나 유지 로직은 그대로")
    print("- Roll20 최종 화면 reload 로직도 그대로")
    print("- 수정된 설치기 py_compile 통과")
    print(f"- 설치기가 생성할 테스트 코드 {generated_count}개 ast.parse 통과")
    print("- 이제 프로젝트 복제본 전체 unittest를 실행합니다.")

    completed = subprocess.run([sys.executable, str(FIXED)], cwd=ROOT)
    if completed.returncode != 0:
        return completed.returncode

    print("[시트 이동기] v2.4.2 적용까지 완료")
    print("다음: python main.py")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"[시트 이동기] v2.4.2 보정 실패: {exc}", file=sys.stderr)
        raise
