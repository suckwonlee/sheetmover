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
PACKAGE_ROOT = Path(__file__).resolve().parent
PAYLOAD_ROOT = PACKAGE_ROOT / "v265_payload"

UI = Path("sheet_mover/ui.py")
FULL_RUN = Path("sheet_mover/full_run.py")
HYBRID = Path("sheet_mover/hybrid_translator.py")
UPDATE_MODE = Path("sheet_mover/update_mode.py")
TEST = Path("tests/test_update_mode_v265.py")

PATCH_TARGETS = (UI, FULL_RUN, HYBRID, UPDATE_MODE, TEST)


def parse(path: Path):
    ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def replace_exact(path: Path, old: str, new: str, label: str):
    text = path.read_text(encoding="utf-8")
    if new in text:
        return False
    if old not in text:
        raise RuntimeError(f"{label} 위치를 찾지 못했습니다: {path}")
    text = text.replace(old, new, 1)
    ast.parse(text, filename=str(path))
    path.write_text(text, encoding="utf-8")
    return True


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

    old_vars = '''        self.source_var = tk.StringVar(value=source_url)
        self.progress_var = tk.DoubleVar(value=0)
'''
    new_vars = '''        self.source_var = tk.StringVar(value=source_url)
        self.update_existing_var = tk.BooleanVar(value=False)
        self.progress_var = tk.DoubleVar(value=0)
'''
    if new_vars not in text:
        if old_vars not in text:
            raise RuntimeError("UI update mode 변수 위치를 찾지 못했습니다.")
        text = text.replace(old_vars, new_vars, 1)

    old_style = '''        style.configure(
            "Primary.TButton",
            background="#347fd1",
            foreground="#ffffff",
            bordercolor="#4c98e9",
            padding=(15, 11),
            font=("Segoe UI", 11, "bold"),
        )
'''
    new_style = old_style + '''        style.configure(
            "Update.TCheckbutton",
            background="#171e28",
            foreground="#d8e0e8",
            font=("Segoe UI", 9),
        )
        style.map(
            "Update.TCheckbutton",
            background=[("active", "#171e28")],
            foreground=[("disabled", "#6f7b88"), ("active", "#f2f5f8")],
        )
'''
    if '"Update.TCheckbutton"' not in text:
        if old_style not in text:
            raise RuntimeError("UI 체크박스 스타일 위치를 찾지 못했습니다.")
        text = text.replace(old_style, new_style, 1)

    old_left = '''        ttk.Label(
            left,
            text="Roll20에는 동일한 이름의 대상 캐릭터가 미리 있어야 합니다.",
            style="Muted.TLabel",
        ).pack(anchor="w", pady=(8, 0))
'''
    new_left = old_left + '''        ttk.Checkbutton(
            left,
            text="기존 시트 이동 결과를 기준으로 변경사항 업데이트",
            variable=self.update_existing_var,
            style="Update.TCheckbutton",
        ).pack(anchor="w", pady=(12, 0))
        ttk.Label(
            left,
            text=(
                "체크 시 같은 D&D Beyond 캐릭터의 이전 성공 결과와 비교해 "
                "기존 번역을 재사용하고 변경된 내용을 다시 반영합니다."
            ),
            style="Muted.TLabel",
            wraplength=430,
            justify="left",
        ).pack(anchor="w", pady=(4, 0))
'''
    if "기존 시트 이동 결과를 기준으로 변경사항 업데이트" not in text:
        if old_left not in text:
            raise RuntimeError("UI 업데이트 체크박스 삽입 위치를 찾지 못했습니다.")
        text = text.replace(old_left, new_left, 1)

    old_command = '''    def _worker_command(self, settings_file=None):
        if getattr(sys, "frozen", False):
            return [
                sys.executable,
                "--worker-full-run",
                "--source",
                self.source_var.get().strip(),
                "--settings",
                str(settings_file or settings_path()),
            ]
        return [
            sys.executable,
            "-m",
            "sheet_mover.full_run",
            "--source",
            self.source_var.get().strip(),
            "--settings",
            str(settings_file or settings_path()),
        ]
'''
    new_command = '''    def _worker_command(self, settings_file=None):
        if getattr(sys, "frozen", False):
            command = [
                sys.executable,
                "--worker-full-run",
                "--source",
                self.source_var.get().strip(),
                "--settings",
                str(settings_file or settings_path()),
            ]
        else:
            command = [
                sys.executable,
                "-m",
                "sheet_mover.full_run",
                "--source",
                self.source_var.get().strip(),
                "--settings",
                str(settings_file or settings_path()),
            ]
        if self.update_existing_var.get():
            command.append("--update-existing")
        return command
'''
    if 'command.append("--update-existing")' not in text:
        if old_command not in text:
            raise RuntimeError("UI worker command 위치를 찾지 못했습니다.")
        text = text.replace(old_command, new_command, 1)

    ast.parse(text, filename=str(path))
    path.write_text(text, encoding="utf-8")


def patch_full_run(root: Path):
    path = root / FULL_RUN
    text = path.read_text(encoding="utf-8")

    old_start = '''def run_full_move(source_url: str, settings: AppSettings, *, emit=None, run_id=None):
    emit = emit or (lambda _event: None)
    run_id = run_id or uuid4().hex
'''
    new_start = '''def run_full_move(source_url: str, settings: AppSettings, *, emit=None, run_id=None):
    emit = emit or (lambda _event: None)
    run_id = run_id or uuid4().hex
    update_existing = str(os.getenv("SHEETMOVER_UPDATE_EXISTING") or "").strip().casefold() in {
        "1", "true", "yes", "on",
    }
'''
    if "update_existing = str(os.getenv(\"SHEETMOVER_UPDATE_EXISTING\")" not in text:
        if old_start not in text:
            raise RuntimeError("full_run update mode 시작 위치를 찾지 못했습니다.")
        text = text.replace(old_start, new_start, 1)

    old_state = '''        "started_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "paths": {}, "stages": list(STAGES), "reports": {},
'''
    new_state = '''        "started_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "run_mode": "update_existing" if update_existing else "initial_move",
        "paths": {}, "stages": list(STAGES), "reports": {},
'''
    if '"run_mode": "update_existing" if update_existing else "initial_move"' not in text:
        if old_state not in text:
            raise RuntimeError("full_run run_mode 기록 위치를 찾지 못했습니다.")
        text = text.replace(old_state, new_state, 1)

    old_imports = '''        # Import stages only after applying the worker's settings snapshot.
        from .mover import run as prepare
        from .result_store import default_result_path
'''
    new_imports = '''        # Import stages only after applying the worker's settings snapshot.
        # mover/hybrid translator is imported after update-mode seed selection.
        from .result_store import default_result_path
'''
    if "mover/hybrid translator is imported after update-mode seed selection" not in text:
        if old_imports not in text:
            raise RuntimeError("full_run mover import 분리 위치를 찾지 못했습니다.")
        text = text.replace(old_imports, new_imports, 1)

    old_after_target = '''        state["paths"]["target"] = str(Path(target_path).resolve())
        stage(2, "pass", "Roll20 게임 탭 · 대상 캐릭터 · Legacy OGL5e 시트 확인 완료")
        overall(5, "Roll20 준비 완료. 기존 번역 결과를 확인합니다.")

        payload, reusable_path = _find_reusable_result(source_id, identity_raw)
'''
    new_after_target = '''        state["paths"]["target"] = str(Path(target_path).resolve())
        stage(2, "pass", "Roll20 게임 탭 · 대상 캐릭터 · Legacy OGL5e 시트 확인 완료")

        from .update_mode import (
            activate_previous_translation_seed,
            prepare_update_context,
        )
        if update_existing:
            update_context = prepare_update_context(
                identity_raw,
                data_root=data_dir(),
            )
            state["update_mode"] = update_context
            previous_result_path = update_context["previous_result_path"]
            activate_previous_translation_seed(previous_result_path)
            level_delta = int(update_context.get("level_delta") or 0)
            classification = str(update_context.get("classification") or "")
            if classification == "probable_level_up":
                overall(
                    5,
                    "기존 결과 비교 완료 · "
                    f"레벨 {update_context.get('previous_total_level')} → "
                    f"{update_context.get('current_total_level')} "
                    f"(+{level_delta}) · 기존 번역 재사용 준비",
                )
            else:
                overall(
                    5,
                    "기존 결과 비교 완료 · "
                    f"변경 구역 {len(update_context.get('changed_sections') or [])}개 · "
                    "기존 번역 재사용 준비",
                )
        else:
            activate_previous_translation_seed(None)
            state["update_mode"] = {
                "enabled": False,
                "classification": "initial_move",
            }
            overall(5, "Roll20 준비 완료. 기존 번역 결과를 확인합니다.")

        # Import only after an explicit previous-result seed was selected.
        from .mover import run as prepare

        payload, reusable_path = _find_reusable_result(source_id, identity_raw)
'''
    if "prepare_update_context" not in text:
        if old_after_target not in text:
            raise RuntimeError("full_run 업데이트 비교 삽입 위치를 찾지 못했습니다.")
        text = text.replace(old_after_target, new_after_target, 1)

    old_parser = '''    parser.add_argument("--run-id")
    args = parser.parse_args(argv)
'''
    new_parser = '''    parser.add_argument("--run-id")
    parser.add_argument(
        "--update-existing",
        action="store_true",
        help="이전 시트 이동 성공 결과와 비교해 기존 캐릭터 변경사항을 업데이트합니다.",
    )
    args = parser.parse_args(argv)
'''
    if 'parser.add_argument(\n        "--update-existing"' not in text:
        if old_parser not in text:
            raise RuntimeError("full_run CLI flag 위치를 찾지 못했습니다.")
        text = text.replace(old_parser, new_parser, 1)

    old_execute = '''    def execute(emit):
        try:
            run_full_move(args.source, load_settings(args.settings),
                          emit=emit, run_id=args.run_id)
'''
    new_execute = '''    def execute(emit):
        if args.update_existing:
            os.environ["SHEETMOVER_UPDATE_EXISTING"] = "1"
        else:
            os.environ.pop("SHEETMOVER_UPDATE_EXISTING", None)
        try:
            run_full_move(args.source, load_settings(args.settings),
                          emit=emit, run_id=args.run_id)
'''
    if 'os.environ["SHEETMOVER_UPDATE_EXISTING"] = "1"' not in text:
        if old_execute not in text:
            raise RuntimeError("full_run CLI update env 위치를 찾지 못했습니다.")
        text = text.replace(old_execute, new_execute, 1)

    ast.parse(text, filename=str(path))
    path.write_text(text, encoding="utf-8")


def patch_hybrid(root: Path):
    path = root / HYBRID
    text = path.read_text(encoding="utf-8")

    old_prepare = '''    def _prepare_cache_only_seed(self, character):
        if not self.cache_only or self._semantic_seed_source:
            return
'''
    new_prepare = '''    def _previous_result_seed_enabled(self):
        return bool(self.cache_only or SEMANTIC_SEED_RESULT)

    def _prepare_cache_only_seed(self, character):
        if not self._previous_result_seed_enabled() or self._semantic_seed_source:
            return
'''
    if "def _previous_result_seed_enabled" not in text:
        if old_prepare not in text:
            raise RuntimeError("hybrid seed 준비 위치를 찾지 못했습니다.")
        text = text.replace(old_prepare, new_prepare, 1)

    old_warning = '''            warning = (
                f"cache-only 검증을 위해 이전 결과 {path.name}에서 "
                f"안전하게 검증된 번역 seed {sum(after)}개를 재사용합니다."
            )
'''
    new_warning = '''            seed_purpose = "cache-only 검증" if self.cache_only else "기존 시트 업데이트"
            warning = (
                f"{seed_purpose}를 위해 이전 결과 {path.name}에서 "
                f"안전하게 검증된 번역 seed {sum(after)}개를 재사용합니다."
            )
'''
    if "seed_purpose = \"cache-only 검증\"" not in text:
        if old_warning not in text:
            raise RuntimeError("hybrid seed 경고 위치를 찾지 못했습니다.")
        text = text.replace(old_warning, new_warning, 1)

    text = text.replace(
        '''        if not self.cache_only:\n            return\n        for canonical, members in self._equivalent_groups.items():\n''',
        '''        if not self._previous_result_seed_enabled():\n            return\n        for canonical, members in self._equivalent_groups.items():\n''',
        1,
    )

    old_dnd_target = '''    def _dnd_tag_target(self, tag, body):
        target = _base.Translator._dnd_tag_target(self, tag, body)
        if target is not None or not self.cache_only:
            return target
'''
    new_dnd_target = '''    def _dnd_tag_target(self, tag, body):
        target = _base.Translator._dnd_tag_target(self, tag, body)
        if target is not None or not self._previous_result_seed_enabled():
            return target
'''
    if new_dnd_target not in text:
        if old_dnd_target not in text:
            raise RuntimeError("hybrid D&D tag seed 위치를 찾지 못했습니다.")
        text = text.replace(old_dnd_target, new_dnd_target, 1)

    old_struct = '''    def _translate_structured(self, value):
        """Translate categorized HTML without giving markup ownership to Google.

        The new primary path strips real HTML tags into an immutable template,
        flattens known D&D semantic tags only for the translation sentence, and
        sends visible prose as ``text/plain``.  If safe deterministic
        reconstruction is impossible, fall back to the existing text-node path
        (which also keeps HTML outside the model) rather than guessing.
        """
        context = self._primary_unit_context(value)
'''
    new_struct = '''    def _translate_structured(self, value):
        """Translate categorized HTML without giving markup ownership to Google.

        The new primary path strips real HTML tags into an immutable template,
        flattens known D&D semantic tags only for the translation sentence, and
        sends visible prose as ``text/plain``.  If safe deterministic
        reconstruction is impossible, fall back to the existing text-node path
        (which also keeps HTML outside the model) rather than guessing.
        """
        if self._previous_result_seed_enabled():
            seeded = self._cache_only_structured_seed.get(value)
            if seeded is not None:
                self.review_stats["semantic_structured_seed_hits"] += 1
                self.review_stats["semantic_seed_hits"] += 1
                self._mark_source_recovered(value)
                return seeded

        context = self._primary_unit_context(value)
'''
    if 'if self._previous_result_seed_enabled():\n            seeded = self._cache_only_structured_seed.get(value)' not in text:
        if old_struct not in text:
            raise RuntimeError("hybrid structured seed 위치를 찾지 못했습니다.")
        text = text.replace(old_struct, new_struct, 1)

    text = text.replace(
        'seeded = self._cache_only_direct_seed.get(value) if self.cache_only else None',
        'seeded = self._cache_only_direct_seed.get(value) if self._previous_result_seed_enabled() else None',
        1,
    )
    text = text.replace(
        'if self.cache_only and not _base.STRUCTURE_PATTERN.search(value):',
        'if self._previous_result_seed_enabled() and not _base.STRUCTURE_PATTERN.search(value):',
        1,
    )

    old_batch = '''        for source in values:
            if source in self._equivalent_final:
                pre_resolved[source] = self._equivalent_final[source]
                continue

            canonical = self._equivalent_canonical.get(source)
'''
    new_batch = '''        for source in values:
            if source in self._equivalent_final:
                pre_resolved[source] = self._equivalent_final[source]
                continue

            if self._previous_result_seed_enabled() and (
                source in self._cache_only_direct_seed
                or source in self._cache_only_structured_seed
            ):
                pre_resolved[source] = self.translate(source)
                continue

            canonical = self._equivalent_canonical.get(source)
'''
    if "source in self._cache_only_direct_seed" not in text[text.index("def _translate_batch_once"):text.index("def _translate_batch_resilient")]:
        if old_batch not in text:
            raise RuntimeError("hybrid batch seed 위치를 찾지 못했습니다.")
        text = text.replace(old_batch, new_batch, 1)

    ast.parse(text, filename=str(path))
    path.write_text(text, encoding="utf-8")


def apply_tree(root: Path):
    for relative in (UI, FULL_RUN, HYBRID):
        if not (root / relative).is_file():
            raise RuntimeError(f"필수 프로젝트 파일 없음: {root / relative}")
    copy_payload(root)
    patch_ui(root)
    patch_full_run(root)
    patch_hybrid(root)
    for relative in PATCH_TARGETS:
        parse(root / relative)


def _ignore_copy(_path, names):
    ignored = set()
    for name in names:
        if name in {
            ".git", ".venv", "__pycache__", "results", "output",
            "output_archive", ".roll20_chrome_profile", "stage4_assets",
        }:
            ignored.add(name)
        elif name.endswith(".pyc") or name.endswith(".pyo"):
            ignored.add(name)
    return ignored


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
        raise RuntimeError(
            "복제본 전체 테스트 실패. 실제 프로젝트는 수정하지 않았습니다.\n"
            + "\n".join(output.splitlines()[-180:])
        )
    match = re.search(r"Ran\s+(\d+)\s+tests?", output)
    return int(match.group(1)) if match else None


def backup(path: Path):
    if not path.exists():
        return
    target = path.with_suffix(path.suffix + ".pre-v2.6.5.bak")
    if not target.exists():
        shutil.copy2(path, target)


def install_from_sandbox(sandbox: Path):
    for relative in PATCH_TARGETS:
        src = sandbox / relative
        dst = ROOT / relative
        backup(dst)
        dst.parent.mkdir(parents=True, exist_ok=True)
        tmp = dst.with_name(dst.name + ".v265.tmp")
        shutil.copy2(src, tmp)
        os.replace(tmp, dst)
        parse(dst)


def main():
    print("[시트 이동기] v2.6.5 기존 시트 업데이트 모드 패치 준비")
    print("- 메인 화면에 기존 시트 업데이트 체크박스 추가")
    print("- 이전 성공 결과와 현재 D&D Beyond raw 데이터 비교")
    print("- 레벨 증가 시 probable_level_up 기록")
    print("- 동일 원문 번역은 이전 검증 결과에서 재사용")
    print("- 새/변경 원문만 기존 Google/Ollama 번역 경로 사용")
    print("- 같은 이름이어도 D&D Beyond ID가 다르면 업데이트 금지")
    print("- 실제 파일 수정 전 복제본 전체 unittest 실행")

    if not (ROOT / UI).is_file():
        raise RuntimeError("시트 이동기 프로젝트 폴더에서 실행해 주세요.")
    if not PAYLOAD_ROOT.is_dir():
        raise RuntimeError(f"패치 payload 폴더가 없습니다: {PAYLOAD_ROOT}")

    with tempfile.TemporaryDirectory(prefix="sheetmover-v265-") as temp:
        sandbox = Path(temp) / "repo"
        shutil.copytree(ROOT, sandbox, ignore=_ignore_copy)
        apply_tree(sandbox)
        print("[시트 이동기] 복제본 전체 unittest 실행...")
        count = run_tests(sandbox)
        print(
            "[시트 이동기] 복제본 전체 테스트 통과"
            + (f": {count}개" if count is not None else "")
        )
        install_from_sandbox(sandbox)

    print("[시트 이동기] v2.6.5 패치 적용 완료")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"[시트 이동기] v2.6.5 패치 실패: {exc}", file=sys.stderr)
        raise
