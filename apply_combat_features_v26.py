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
PAYLOAD_DIR = Path(__file__).resolve().parent / "payload"

TARGETS = {
    "sheet_mover/roll20_features.py": (
        'STAGE8_VERSION = "2026-10-07-stage8-roll20-features-v1.7.3-tag-helper-hotfix"',
        "# runtime-integrity-v2.6 feature hook",
        '''
# runtime-integrity-v2.6 feature hook
from .runtime_integrity_v26 import install_feature_integrity as _install_feature_integrity_v26
build_feature_plan = _install_feature_integrity_v26(build_feature_plan, globals())
''',
    ),
    "sheet_mover/roll20_attacks.py": (
        'STAGE10_VERSION = "2026-10-06-stage10a-roll20-weapon-attacks-v1"',
        "# runtime-integrity-v2.6 attack hook",
        '''
# runtime-integrity-v2.6 attack hook
from .runtime_integrity_v26 import install_attack_integrity as _install_attack_integrity_v26
map_weapon_attack, build_attack_plan = _install_attack_integrity_v26(
    map_weapon_attack,
    build_attack_plan,
    globals(),
)
''',
    ),
    "sheet_mover/calculator.py": (
        'STAGE2_CALCULATOR_VERSION = "2026-10-06-stage2-final-values-v1"',
        "# runtime-integrity-v2.6 AC hook",
        '''
# runtime-integrity-v2.6 AC hook
from .runtime_integrity_v26 import install_ac_integrity as _install_ac_integrity_v26
_calculate_armor_class = _install_ac_integrity_v26(
    _calculate_armor_class,
    globals(),
)
''',
    ),
    "stage5_basic_writer_v2.py": (
        'VERSION = "2026-10-06-stage5-basic-fields-v2.1-multiclass"',
        "# runtime-integrity-v2.6 Stage 5 cache-refresh hook",
        '''
# runtime-integrity-v2.6 Stage 5 cache-refresh hook
from sheet_mover.runtime_integrity_v26 import install_basic_plan_integrity as _install_basic_plan_integrity_v26
build_plan = _install_basic_plan_integrity_v26(build_plan, globals())
''',
    ),
    "sheet_mover/roll20_resources.py": (
        'stage12-roll20-resources-v1.3-ability-modifier-uses',
        "# runtime-integrity-v2.6 resource-cleanup hook",
        '''
# runtime-integrity-v2.6 resource-cleanup hook
from .runtime_integrity_v26 import install_resource_integrity as _install_resource_integrity_v26
apply_resources = _install_resource_integrity_v26(apply_resources, globals())
''',
    ),
}


def compile_path(path):
    ast.parse(
        path.read_text(encoding="utf-8"),
        filename=str(path),
    )


def inject_hook(path, expected_marker, hook_marker, hook_text):
    text = path.read_text(encoding="utf-8")
    if hook_marker in text:
        return False

    if expected_marker not in text:
        raise RuntimeError(
            f"{path}: 검토한 기준 버전이 아닙니다. "
            "실제 파일을 수정하지 않습니다."
        )

    main_guard = '\nif __name__ == "__main__":\n'
    pos = text.rfind(main_guard)
    if pos >= 0:
        patched = (
            text[:pos]
            + "\n"
            + hook_text.strip()
            + "\n"
            + text[pos:]
        )
    else:
        patched = text.rstrip() + "\n\n" + hook_text.strip() + "\n"

    ast.parse(patched, filename=str(path))
    path.write_text(patched, encoding="utf-8")
    return True


def sandbox_ignore(_dir, names):
    ignored = set()
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
            ignored.add(name)
        elif name.endswith(".pyc"):
            ignored.add(name)
    return ignored


def apply_to_tree(root):
    runtime_src = (
        PAYLOAD_DIR
        / "sheet_mover"
        / "runtime_integrity_v26.py"
    )
    test_src = (
        PAYLOAD_DIR
        / "tests"
        / "test_runtime_integrity_v26.py"
    )
    if not runtime_src.is_file() or not test_src.is_file():
        raise RuntimeError("패치 payload 파일이 없습니다.")

    runtime_dst = (
        root
        / "sheet_mover"
        / "runtime_integrity_v26.py"
    )
    test_dst = (
        root
        / "tests"
        / "test_runtime_integrity_v26.py"
    )
    runtime_dst.parent.mkdir(parents=True, exist_ok=True)
    test_dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(runtime_src, runtime_dst)
    shutil.copy2(test_src, test_dst)

    changed = [runtime_dst, test_dst]
    for relative, spec in TARGETS.items():
        path = root / relative
        if not path.is_file():
            raise RuntimeError(f"필수 파일이 없습니다: {path}")
        if inject_hook(path, *spec):
            changed.append(path)

    for path in changed:
        compile_path(path)
    return changed


def run_full_tests(root):
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
        tail = "\n".join(output.splitlines()[-240:])
        raise RuntimeError(
            "복제본 전체 테스트 실패. "
            "실제 프로젝트는 수정하지 않았습니다.\n"
            + tail
        )
    match = re.search(r"Ran\s+(\d+)\s+tests?", output)
    return int(match.group(1)) if match else None


def backup(path):
    backup_path = path.with_suffix(
        path.suffix + ".pre-v2.6.bak"
    )
    if path.exists() and not backup_path.exists():
        shutil.copy2(path, backup_path)


def main():
    print("[시트 이동기] combat/features v2.6 준비")
    print("- Weapon Mastery 실제 선택 무기/숙달 표시")
    print("- Pact of the Chain 선택 특성/Attack 표시")
    print("- +N 마법 무기 및 Archery 명중/피해 보정")
    print("- Unarmed Strike 추가")
    print("- AC explicit/custom 계산 보강")
    print("- 오래된 중복 자원 반복행 정리")
    print("- 번역 API 호출 없음")
    print("- 실제 파일 수정 전 복제본 전체 unittest 실행")

    with tempfile.TemporaryDirectory(
        prefix="sheetmover-v26-"
    ) as temp:
        sandbox = Path(temp) / "repo"
        shutil.copytree(
            ROOT,
            sandbox,
            ignore=sandbox_ignore,
        )
        apply_to_tree(sandbox)

        print("[시트 이동기] 복제본 전체 unittest 실행...")
        count = run_full_tests(sandbox)
        print(
            "[시트 이동기] 복제본 전체 테스트 통과"
            + (f": {count}개" if count is not None else "")
        )

        # No user files are touched until the tested sandbox has passed.
        for relative in TARGETS:
            backup(ROOT / relative)
        backup(ROOT / "sheet_mover" / "runtime_integrity_v26.py")
        backup(ROOT / "tests" / "test_runtime_integrity_v26.py")

        for relative in [
            "sheet_mover/runtime_integrity_v26.py",
            "tests/test_runtime_integrity_v26.py",
            *TARGETS.keys(),
        ]:
            src = sandbox / relative
            dst = ROOT / relative
            dst.parent.mkdir(parents=True, exist_ok=True)
            temp_dst = dst.with_name(dst.name + ".v26.tmp")
            shutil.copy2(src, temp_dst)
            os.replace(temp_dst, dst)
            compile_path(dst)

    print("[시트 이동기] combat/features v2.6 적용 완료")
    print("- 같은 D&D Beyond 원본이면 기존 번역 캐시를 재사용합니다.")
    print("- 다음: python main.py")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(
            f"[시트 이동기] v2.6 실패: {exc}",
            file=sys.stderr,
        )
        raise
