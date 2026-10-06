"""Organize generated results into results/current and output_archive."""
from __future__ import annotations

import argparse
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
import shutil

from .result_store import (
    ARCHIVE_DIR,
    CURRENT_RESULT_DIR,
    _PARTIAL_RE,
    _RESULT_RE,
    is_complete_result,
    result_sort_key,
)


@dataclass(frozen=True)
class CleanupPlan:
    keep_current: tuple[Path, ...]
    move_to_current: tuple[Path, ...]
    archive: tuple[Path, ...]


def _unique(paths):
    result = []
    seen = set()
    for path in paths:
        key = str(path.resolve()) if path.exists() else str(path)
        if key not in seen:
            result.append(path)
            seen.add(key)
    return result


def plan_cleanup(root=".", keep_complete_per_character=2):
    root = Path(root).resolve()
    current_dir = root / CURRENT_RESULT_DIR
    grouped = defaultdict(list)
    archive = []

    candidates = []
    candidates.extend(root.glob("sheet-result-*.json"))
    if current_dir.exists():
        candidates.extend(current_dir.glob("sheet-result-*.json"))

    for path in _unique(candidates):
        match = _RESULT_RE.match(path.name)
        if not match or not is_complete_result(path):
            archive.append(path)
            continue
        grouped[match.group("source")].append(path)

    partials = list(root.glob("sheet-partial-*.json"))
    if current_dir.exists():
        partials.extend(current_dir.glob("sheet-partial-*.json"))
    archive.extend(_unique(partials))

    keep_current = []
    move_to_current = []
    count = max(1, int(keep_complete_per_character))
    for paths in grouped.values():
        paths.sort(key=result_sort_key, reverse=True)
        chosen = paths[:count]
        archive.extend(paths[count:])
        for path in chosen:
            if path.parent.resolve() == current_dir.resolve():
                keep_current.append(path)
            else:
                move_to_current.append(path)

    return CleanupPlan(
        keep_current=tuple(sorted(_unique(keep_current), key=lambda p: p.name)),
        move_to_current=tuple(sorted(_unique(move_to_current), key=lambda p: p.name)),
        archive=tuple(sorted(_unique(archive), key=lambda p: p.name)),
    )


def _unused_destination(folder: Path, name: str):
    target = folder / name
    if not target.exists():
        return target
    stem, suffix = target.stem, target.suffix
    index = 2
    while True:
        candidate = folder / f"{stem}({index}){suffix}"
        if not candidate.exists():
            return candidate
        index += 1


def execute_cleanup(root=".", archive_dir=ARCHIVE_DIR, keep_complete_per_character=2, dry_run=False):
    root = Path(root).resolve()
    current_dir = root / CURRENT_RESULT_DIR
    archive_dir = Path(archive_dir)
    if not archive_dir.is_absolute():
        archive_dir = root / archive_dir
    plan = plan_cleanup(root, keep_complete_per_character)

    moved_current = []
    moved_archive = []
    if not dry_run:
        current_dir.mkdir(parents=True, exist_ok=True)
        archive_dir.mkdir(parents=True, exist_ok=True)

        # Archive first so stale files with the same name cannot block current.
        for source in plan.archive:
            if not source.exists():
                continue
            destination = _unused_destination(archive_dir, source.name)
            shutil.move(str(source), str(destination))
            moved_archive.append(destination)

        for source in plan.move_to_current:
            if not source.exists():
                continue
            destination = _unused_destination(current_dir, source.name)
            shutil.move(str(source), str(destination))
            moved_current.append(destination)

    return {
        "keep_current": [str(p) for p in plan.keep_current],
        "move_to_current": [str(p) for p in plan.move_to_current],
        "archive": [str(p) for p in plan.archive],
        "moved_current": [str(p) for p in moved_current],
        "moved_archive": [str(p) for p in moved_archive],
        "current_dir": str(current_dir),
        "archive_dir": str(archive_dir),
        "dry_run": bool(dry_run),
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default=".")
    parser.add_argument("--archive-dir", default=str(ARCHIVE_DIR))
    parser.add_argument("--keep", type=int, default=2, help="캐릭터별 최신 정상 결과 유지 개수")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    result = execute_cleanup(args.root, args.archive_dir, args.keep, args.dry_run)

    print(f"[시트 이동기] current 유지: {len(result['keep_current'])}개")
    current_action = "current 이동 예정" if args.dry_run else "current 이동"
    archive_action = "archive 이동 예정" if args.dry_run else "archive 이동"
    print(f"[시트 이동기] {current_action}: {len(result['move_to_current'])}개")
    print(f"[시트 이동기] {archive_action}: {len(result['archive'])}개")
    print(f"[시트 이동기] current: {result['current_dir']}")
    print(f"[시트 이동기] archive: {result['archive_dir']}")


if __name__ == "__main__":
    main()
