# -*- coding: utf-8 -*-
from __future__ import annotations

import ast
from hashlib import sha256
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile

ROOT = Path.cwd()
PACKAGE_ROOT = Path(__file__).resolve().parent
PAYLOAD_ROOT = PACKAGE_ROOT / "v2651_payload"

UI = Path("sheet_mover/ui.py")
MOVER = Path("sheet_mover/mover.py")
FULL_RUN = Path("sheet_mover/full_run.py")
RUN_LOG = Path("sheet_mover/run_log.py")
HYBRID = Path("sheet_mover/hybrid_translator.py")
UPDATE_MODE = Path("sheet_mover/update_mode.py")
TEST = Path("tests/test_update_mode_v2651.py")

PATCH_TARGETS = (UI, MOVER, FULL_RUN, RUN_LOG, UPDATE_MODE, TEST)


def parse(path: Path):
    ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def digest(path: Path):
    return sha256(path.read_bytes()).hexdigest()


def replace_once(text: str, old: str, new: str, label: str):
    if new in text:
        return text
    if old not in text:
        raise RuntimeError(f"{label} 위치를 찾지 못했습니다.")
    return text.replace(old, new, 1)


def copy_payload(root: Path):
    for relative in (UPDATE_MODE, TEST):
        src = PAYLOAD_ROOT / relative
        if not src.is_file():
            raise RuntimeError(f"패치 payload 파일 없음: {src}")
        dst = root / relative
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        parse(dst)


def patch_ui(root: Path):
    path = root / UI
    text = path.read_text(encoding="utf-8")

    old_vars = '''        self.source_var = tk.StringVar(value=source_url)\n        self.progress_var = tk.DoubleVar(value=0)\n'''
    new_vars = '''        self.source_var = tk.StringVar(value=source_url)\n        self.update_existing_var = tk.BooleanVar(value=False)\n        self.progress_var = tk.DoubleVar(value=0)\n'''
    text = replace_once(text, old_vars, new_vars, "UI 업데이트 체크 변수")

    old_left = '''        ttk.Label(\n            left,\n            text="Roll20에는 동일한 이름의 대상 캐릭터가 미리 있어야 합니다.",\n            style="Muted.TLabel",\n        ).pack(anchor="w", pady=(8, 0))\n'''
    new_left = old_left + '''        ttk.Checkbutton(\n            left,\n            text="기존 시트 이동 결과를 기준으로 변경사항 업데이트",\n            variable=self.update_existing_var,\n        ).pack(anchor="w", pady=(12, 0))\n        ttk.Label(\n            left,\n            text=(\n                "체크 시 같은 D&D Beyond 캐릭터의 이전 결과와 비교하고, "\n                "검증된 동일 원문 번역은 재사용합니다."\n            ),\n            style="Muted.TLabel",\n            wraplength=430,\n            justify="left",\n        ).pack(anchor="w", pady=(4, 0))\n'''
    if "기존 시트 이동 결과를 기준으로 변경사항 업데이트" not in text:
        if old_left not in text:
            raise RuntimeError("UI 업데이트 체크박스 삽입 위치를 찾지 못했습니다.")
        text = text.replace(old_left, new_left, 1)

    old_command = '''    def _worker_command(self, settings_file=None):\n        if getattr(sys, "frozen", False):\n            return [\n                sys.executable,\n                "--worker-full-run",\n                "--source",\n                self.source_var.get().strip(),\n                "--settings",\n                str(settings_file or settings_path()),\n            ]\n        return [\n            sys.executable,\n            "-m",\n            "sheet_mover.full_run",\n            "--source",\n            self.source_var.get().strip(),\n            "--settings",\n            str(settings_file or settings_path()),\n        ]\n'''
    old_v265_command = '''    def _worker_command(self, settings_file=None):\n        if getattr(sys, "frozen", False):\n            command = [\n                sys.executable,\n                "--worker-full-run",\n                "--source",\n                self.source_var.get().strip(),\n                "--settings",\n                str(settings_file or settings_path()),\n            ]\n        else:\n            command = [\n                sys.executable,\n                "-m",\n                "sheet_mover.full_run",\n                "--source",\n                self.source_var.get().strip(),\n                "--settings",\n                str(settings_file or settings_path()),\n            ]\n        if self.update_existing_var.get():\n            command.append("--update-existing")\n        return command\n'''
    new_command = '''    def _worker_command(self, settings_file=None):\n        if getattr(sys, "frozen", False):\n            command = [\n                sys.executable,\n                "--worker-full-run",\n                "--source",\n                self.source_var.get().strip(),\n                "--settings",\n                str(settings_file or settings_path()),\n            ]\n        else:\n            command = [\n                sys.executable,\n                "-m",\n                "sheet_mover.full_run",\n                "--source",\n                self.source_var.get().strip(),\n                "--settings",\n                str(settings_file or settings_path()),\n            ]\n\n        update_var = getattr(self, "update_existing_var", None)\n        try:\n            update_existing = bool(update_var.get()) if update_var is not None else False\n        except Exception:\n            update_existing = False\n        if update_existing:\n            command.append("--update-existing")\n        return command\n'''
    if new_command not in text:
        if old_v265_command in text:
            text = text.replace(old_v265_command, new_command, 1)
        elif old_command in text:
            text = text.replace(old_command, new_command, 1)
        else:
            raise RuntimeError("UI worker command 위치를 찾지 못했습니다.")

    ast.parse(text, filename=str(path))
    path.write_text(text, encoding="utf-8")


def patch_mover(root: Path):
    path = root / MOVER
    text = path.read_text(encoding="utf-8")

    text = replace_once(
        text,
        '''    async def prepare(self, raw_source=None):\n''',
        '''    async def prepare(self, raw_source=None, translation_seed_path=None):\n''',
        "mover prepare 시그니처",
    )

    old_translator = '''        translator = Translator()\n        try:\n'''
    new_translator = '''        translator = Translator()\n        if translation_seed_path:\n            from .update_mode import prime_translator_from_previous_result\n\n            seed_report = prime_translator_from_previous_result(\n                translator,\n                translation_seed_path,\n                original.to_dict(),\n            )\n            seeded = int(seed_report.get("inserted_count") or 0)\n            self.report(35, f"기존 검증 번역 {seeded}개 재사용 준비")\n        try:\n'''
    text = replace_once(
        text,
        old_translator,
        new_translator,
        "mover 기존 번역 seed 연결",
    )

    old_run = '''def run(source_url=SOURCE_URL, cdp_url=None, on_progress=None, raw_source=None):\n    return asyncio.run(\n        SheetMover(\n            source_url,\n            cdp_url,\n            on_progress,\n        ).prepare(raw_source=raw_source)\n    )\n'''
    new_run = '''def run(\n    source_url=SOURCE_URL,\n    cdp_url=None,\n    on_progress=None,\n    raw_source=None,\n    translation_seed_path=None,\n):\n    return asyncio.run(\n        SheetMover(\n            source_url,\n            cdp_url,\n            on_progress,\n        ).prepare(\n            raw_source=raw_source,\n            translation_seed_path=translation_seed_path,\n        )\n    )\n'''
    text = replace_once(text, old_run, new_run, "mover run 시그니처")

    ast.parse(text, filename=str(path))
    path.write_text(text, encoding="utf-8")


def patch_full_run(root: Path):
    path = root / FULL_RUN
    text = path.read_text(encoding="utf-8")

    old_sig = '''def run_full_move(source_url: str, settings: AppSettings, *, emit=None, run_id=None):\n    emit = emit or (lambda _event: None)\n    run_id = run_id or uuid4().hex\n'''
    new_sig = '''def run_full_move(\n    source_url: str,\n    settings: AppSettings,\n    *,\n    emit=None,\n    run_id=None,\n    update_existing=False,\n):\n    emit = emit or (lambda _event: None)\n    run_id = run_id or uuid4().hex\n    update_existing = bool(update_existing)\n'''
    text = replace_once(text, old_sig, new_sig, "full_run update mode 시그니처")

    old_state = '''        "started_at": datetime.now().astimezone().isoformat(timespec="seconds"),\n        "paths": {}, "stages": list(STAGES), "reports": {},\n'''
    new_state = '''        "started_at": datetime.now().astimezone().isoformat(timespec="seconds"),\n        "run_mode": "update_existing" if update_existing else "initial_move",\n        "paths": {}, "stages": list(STAGES), "reports": {},\n'''
    text = replace_once(text, old_state, new_state, "full_run run_mode 기록")

    old_payload = '''    payload = None\n\n    def checkpoint():\n'''
    new_payload = '''    payload = None\n    previous_result_path = None\n\n    def checkpoint():\n'''
    text = replace_once(text, old_payload, new_payload, "full_run 이전 결과 경로 초기화")

    old_after_target = '''        state["paths"]["target"] = str(Path(target_path).resolve())\n        stage(2, "pass", "Roll20 게임 탭 · 대상 캐릭터 · Legacy OGL5e 시트 확인 완료")\n        overall(5, "Roll20 준비 완료. 기존 번역 결과를 확인합니다.")\n\n        payload, reusable_path = _find_reusable_result(source_id, identity_raw)\n'''
    new_after_target = '''        state["paths"]["target"] = str(Path(target_path).resolve())\n        stage(2, "pass", "Roll20 게임 탭 · 대상 캐릭터 · Legacy OGL5e 시트 확인 완료")\n\n        if update_existing:\n            from .update_mode import prepare_update_context\n\n            update_context = prepare_update_context(\n                identity_raw,\n                data_root=data_dir(),\n            )\n            state["update_mode"] = update_context\n            previous_result_path = update_context["previous_result_path"]\n            classification = str(update_context.get("classification") or "")\n            level_delta = int(update_context.get("level_delta") or 0)\n            if classification == "probable_level_up":\n                overall(\n                    5,\n                    "기존 결과 비교 완료 · "\n                    f"레벨 {update_context.get('previous_total_level')} → "\n                    f"{update_context.get('current_total_level')} "\n                    f"(+{level_delta}) · 변경사항 업데이트 준비",\n                )\n            else:\n                overall(\n                    5,\n                    "기존 결과 비교 완료 · "\n                    f"변경 구역 {len(update_context.get('changed_sections') or [])}개 · "\n                    "변경사항 업데이트 준비",\n                )\n        else:\n            state["update_mode"] = {\n                "enabled": False,\n                "classification": "initial_move",\n            }\n            overall(5, "Roll20 준비 완료. 기존 번역 결과를 확인합니다.")\n\n        payload, reusable_path = _find_reusable_result(source_id, identity_raw)\n'''
    text = replace_once(
        text,
        old_after_target,
        new_after_target,
        "full_run 업데이트 비교 연결",
    )

    old_prepare = '''            prepared = prepare(\n                source_url,\n                cdp_url=settings.roll20_cdp_url,\n                on_progress=lambda p, m: overall(5 + float(p) * .25, m),\n                raw_source=identity_raw,\n            )\n'''
    new_prepare = '''            if update_existing:\n                prepared = prepare(\n                    source_url,\n                    cdp_url=settings.roll20_cdp_url,\n                    on_progress=lambda p, m: overall(5 + float(p) * .25, m),\n                    raw_source=identity_raw,\n                    translation_seed_path=previous_result_path,\n                )\n            else:\n                prepared = prepare(\n                    source_url,\n                    cdp_url=settings.roll20_cdp_url,\n                    on_progress=lambda p, m: overall(5 + float(p) * .25, m),\n                    raw_source=identity_raw,\n                )\n'''
    text = replace_once(text, old_prepare, new_prepare, "full_run seed 전달")

    old_parser = '''    parser.add_argument("--run-id")\n    args = parser.parse_args(argv)\n'''
    new_parser = '''    parser.add_argument("--run-id")\n    parser.add_argument("--update-existing", action="store_true", help="이전 시트 이동 결과와 비교해 기존 캐릭터 변경사항을 업데이트합니다.")\n    args = parser.parse_args(argv)\n'''
    text = replace_once(text, old_parser, new_parser, "full_run CLI 업데이트 옵션")

    old_execute = '''    def execute(emit):\n        try:\n            run_full_move(args.source, load_settings(args.settings),\n                          emit=emit, run_id=args.run_id)\n            return 0\n'''
    new_execute = '''    def execute(emit):\n        try:\n            if args.update_existing:\n                run_full_move(\n                    args.source,\n                    load_settings(args.settings),\n                    emit=emit,\n                    run_id=args.run_id,\n                    update_existing=True,\n                )\n            else:\n                run_full_move(\n                    args.source,\n                    load_settings(args.settings),\n                    emit=emit,\n                    run_id=args.run_id,\n                )\n            return 0\n'''
    text = replace_once(text, old_execute, new_execute, "full_run CLI 전달")

    # v2.6.5 used a process-global env switch. It must not survive this patch.
    if "SHEETMOVER_UPDATE_EXISTING" in text:
        raise RuntimeError("full_run에 이전 v2.6.5 환경변수 방식이 남아 있습니다.")

    ast.parse(text, filename=str(path))
    path.write_text(text, encoding="utf-8")


def patch_run_log(root: Path):
    path = root / RUN_LOG
    text = path.read_text(encoding="utf-8")

    old_sig = '''    def run_full_move_single_log(\n        source_url,\n        settings,\n        *,\n        emit=None,\n        run_id=None,\n    ):\n'''
    new_sig = '''    def run_full_move_single_log(\n        source_url,\n        settings,\n        *,\n        emit=None,\n        run_id=None,\n        update_existing=False,\n    ):\n'''
    text = replace_once(text, old_sig, new_sig, "single log update flag 시그니처")

    old_call = '''        try:\n            state = original_run_full_move(\n                source_url,\n                settings,\n                emit=proxy_emit,\n                run_id=run_id,\n            )\n'''
    new_call = '''        try:\n            if update_existing:\n                state = original_run_full_move(\n                    source_url,\n                    settings,\n                    emit=proxy_emit,\n                    run_id=run_id,\n                    update_existing=True,\n                )\n            else:\n                state = original_run_full_move(\n                    source_url,\n                    settings,\n                    emit=proxy_emit,\n                    run_id=run_id,\n                )\n'''
    text = replace_once(text, old_call, new_call, "single log update flag 전달")

    ast.parse(text, filename=str(path))
    path.write_text(text, encoding="utf-8")


def apply_tree(root: Path):
    required = (UI, MOVER, FULL_RUN, RUN_LOG, HYBRID)
    for relative in required:
        if not (root / relative).is_file():
            raise RuntimeError(f"필수 프로젝트 파일 없음: {root / relative}")

    # v2.6.5.1 is intentionally based on the successful v2.6.4.2 state.
    full_run_text = (root / FULL_RUN).read_text(encoding="utf-8")
    if "runtime-integrity-v2.6.4 subclass-spell cache-refresh hook" not in full_run_text:
        raise RuntimeError("v2.6.4.2 서브클래스 주문 패치가 먼저 적용되어 있어야 합니다.")

    hybrid_before = digest(root / HYBRID)
    copy_payload(root)
    patch_ui(root)
    patch_mover(root)
    patch_full_run(root)
    patch_run_log(root)

    if digest(root / HYBRID) != hybrid_before:
        raise RuntimeError("업데이트 모드가 hybrid_translator.py를 변경했습니다. 적용을 중단합니다.")

    for relative in PATCH_TARGETS:
        parse(root / relative)


def _ignore_copy(_path, names):
    ignored = set()
    for name in names:
        if name in {
            ".git",
            ".venv",
            "__pycache__",
            "results",
            "output",
            "output_archive",
            ".roll20_chrome_profile",
            "stage4_assets",
        }:
            ignored.add(name)
        elif name.endswith(".pyc") or name.endswith(".pyo"):
            ignored.add(name)
    return ignored


def compile_package(root: Path):
    completed = subprocess.run(
        [sys.executable, "-m", "compileall", "-q", "sheet_mover"],
        cwd=root,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
    )
    if completed.returncode != 0:
        raise RuntimeError(
            "복제본 Python 컴파일 검증 실패. 실제 프로젝트는 수정하지 않았습니다.\n"
            + (completed.stdout or "")
        )


def run_tests(root: Path):
    env = os.environ.copy()
    env["PYTHONPATH"] = str(root) + os.pathsep + env.get("PYTHONPATH", "")
    completed = subprocess.run(
        [sys.executable, "-m", "unittest", "discover", "-s", "tests", "-q"],
        cwd=root,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=540,
    )
    output = completed.stdout or ""
    if completed.returncode != 0:
        lines = output.splitlines()
        raise RuntimeError(
            "복제본 전체 테스트 실패. 실제 프로젝트는 수정하지 않았습니다.\n"
            + "\n".join(lines[-220:])
        )
    match = re.search(r"Ran\s+(\d+)\s+tests?", output)
    return int(match.group(1)) if match else None


def backup(path: Path):
    if not path.exists():
        return
    target = path.with_suffix(path.suffix + ".pre-v2.6.5.1.bak")
    if not target.exists():
        shutil.copy2(path, target)


def install_from_sandbox(sandbox: Path):
    for relative in PATCH_TARGETS:
        src = sandbox / relative
        dst = ROOT / relative
        backup(dst)
        dst.parent.mkdir(parents=True, exist_ok=True)
        tmp = dst.with_name(dst.name + ".v2651.tmp")
        shutil.copy2(src, tmp)
        os.replace(tmp, dst)
        parse(dst)


def main():
    print("[시트 이동기] v2.6.5.1 기존 시트 업데이트 모드 패치 준비")
    print("- 업데이트 모드는 명시적 인자로만 전달")
    print("- hybrid_translator/cache-only 로직은 변경하지 않음")
    print("- 동일 DDB ID + 이름의 이전 결과만 비교")
    print("- 이전 결과와 현재 DDB raw 데이터의 레벨/주문/장비 변동 기록")
    print("- 완전히 동일한 영어 원문만 이전 검증 번역 재사용")
    print("- 새/변경 원문은 기존 Google/Ollama 경로 사용")
    print("- 실제 파일 수정 전 복제본 컴파일 + 전체 unittest 실행")

    if not (ROOT / UI).is_file():
        raise RuntimeError("시트 이동기 프로젝트 폴더에서 실행해 주세요.")
    if not PAYLOAD_ROOT.is_dir():
        raise RuntimeError(f"패치 payload 폴더가 없습니다: {PAYLOAD_ROOT}")

    with tempfile.TemporaryDirectory(prefix="sheetmover-v2651-") as temp:
        sandbox = Path(temp) / "repo"
        shutil.copytree(ROOT, sandbox, ignore=_ignore_copy)
        apply_tree(sandbox)
        print("[시트 이동기] 복제본 Python 컴파일 검증...")
        compile_package(sandbox)
        print("[시트 이동기] 복제본 전체 unittest 실행...")
        count = run_tests(sandbox)
        print(
            "[시트 이동기] 복제본 전체 테스트 통과"
            + (f": {count}개" if count is not None else "")
        )
        install_from_sandbox(sandbox)

    print("[시트 이동기] v2.6.5.1 패치 적용 완료")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"[시트 이동기] v2.6.5.1 패치 실패: {exc}", file=sys.stderr)
        raise
