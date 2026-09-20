import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path

from .mover import SOURCE_URL, run
from .source import fetch_character, normalize_character


def _console_progress(percent, message):
    """Print live progress to stderr while keeping stdout as pure JSON."""
    try:
        percent_text = f"{float(percent):5.1f}%"
    except (TypeError, ValueError):
        percent_text = "  ... "

    print(
        f"[{percent_text}] {message}",
        file=sys.stderr,
        flush=True,
    )


def _default_output_path(payload, now=None):
    """Build a unique filename for a successful CLI result."""
    now = now or datetime.now()
    original = payload.get("original") or {}
    source_id = str(original.get("source_id") or "unknown")
    safe_source_id = "".join(
        ch for ch in source_id if ch.isalnum() or ch in ("-", "_")
    ) or "unknown"
    return Path(
        f"sheet-result-{safe_source_id}-{now:%Y%m%d-%H%M%S}.json"
    )


def _save_success_payload(payload, output=None):
    """Save the completed preparation payload as UTF-8 JSON."""
    path = Path(output).expanduser() if output else _default_output_path(payload)
    if path.exists() and path.is_dir():
        path = path / _default_output_path(payload).name

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            payload,
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return path.resolve()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--source",
        default=SOURCE_URL,
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
    )
    parser.add_argument(
        "--source-check",
        action="store_true",
        help="D&D Beyond 링크만으로 원본 데이터를 수집해 요약 출력",
    )
    parser.add_argument(
        "--cli",
        action="store_true",
        help="수집·번역 미리보기 JSON을 CLI로 저장·출력 (Roll20 미입력)",
    )
    parser.add_argument(
        "--output",
        help=(
            "--cli 성공 결과를 저장할 JSON 경로. "
            "생략하면 현재 폴더에 sheet-result-<source_id>-<시각>.json으로 저장"
        ),
    )
    parser.add_argument(
        "--setup-google-glossary",
        action="store_true",
        help="현재 glossary.json으로 Google Cloud Translation v3 용어집을 생성/확인",
    )

    args = parser.parse_args()

    if args.setup_google_glossary:
        from .google_glossary import setup_google_glossary

        result = setup_google_glossary()
        print(
            json.dumps(
                result,
                ensure_ascii=False,
                indent=2,
            )
        )
        return

    if args.dry_run:
        print(
            json.dumps(
                {
                    "source": args.source,
                    "translator": "google-cloud-translation-v3",
                    "project": os.environ.get("SHEETMOVER_GOOGLE_PROJECT")
                    or os.environ.get("GOOGLE_CLOUD_PROJECT"),
                    "glossary_setup_command": (
                        "python -m sheet_mover --setup-google-glossary"
                    ),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return

    if args.source_check:
        raw = fetch_character(args.source)
        sheet = normalize_character(raw)
        print(
            json.dumps(
                {
                    "source_id": sheet.source_id,
                    "name": sheet.name,
                    "race": sheet.race,
                    "background": sheet.background,
                    "classes": sheet.classes,
                    "ability_scores": sheet.ability_scores,
                    "hp": sheet.hp,
                    "max_hp": sheet.max_hp,
                    "temp_hp": sheet.temp_hp,
                    "equipment_count": len(sheet.equipment),
                    "spell_count": len(sheet.spells),
                    "feature_count": len(sheet.features),
                    "action_count": len(sheet.actions),
                    "resource_count": len(sheet.resources),
                    "warnings": sheet.warnings,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return

    if args.cli:
        print(
            "[시트 이동기] D&D Beyond 수집·번역을 시작합니다.",
            file=sys.stderr,
            flush=True,
        )
        try:
            result = run(
                args.source,
                on_progress=_console_progress,
            )
        except Exception as exc:
            partial = getattr(exc, "partial_payload", None)
            if partial is not None:
                filename = Path(
                    f"sheet-partial-{datetime.now():%Y%m%d-%H%M%S}.json"
                )
                filename.write_text(
                    json.dumps(
                        partial,
                        ensure_ascii=False,
                        indent=2,
                    ),
                    encoding="utf-8",
                )
                error = partial.get("error", {})
                current = error.get("progress_index")
                total = error.get("progress_total")
                progress = (
                    f" ({current}/{total})"
                    if current is not None and total is not None
                    else ""
                )
                print(
                    f"[시트 이동기] 번역 실패{progress}. "
                    f"현재까지의 결과를 저장했습니다: {filename.resolve()}",
                    file=sys.stderr,
                    flush=True,
                )
            raise

        payload = result.to_dict()
        saved_path = _save_success_payload(
            payload,
            args.output,
        )
        summary = payload.get("translation_summary") or {}
        preserved_count = int(summary.get("original_preserved_count") or 0)
        if summary.get("status") == "partial" or preserved_count:
            print(
                "[시트 이동기] 번역 부분 완료. "
                f"원문 유지 {preserved_count}개. "
                f"JSON 결과를 저장했습니다: {saved_path}",
                file=sys.stderr,
                flush=True,
            )
            preserved = summary.get("original_preserved") or []
            if preserved:
                print(
                    "[시트 이동기] 원문 유지 항목:",
                    file=sys.stderr,
                    flush=True,
                )
                for item in preserved:
                    if not isinstance(item, dict):
                        continue
                    preview = str(item.get("source_preview") or "").strip()
                    reason = str(item.get("reason") or "").strip()
                    detail = preview if not reason else f"{preview} ({reason})"
                    print(
                        f"  - {detail}",
                        file=sys.stderr,
                        flush=True,
                    )
        else:
            print(
                "[시트 이동기] 번역 완료. "
                f"JSON 결과를 저장했습니다: {saved_path}",
                file=sys.stderr,
                flush=True,
            )
        print(
            json.dumps(
                payload,
                ensure_ascii=False,
                indent=2,
            )
        )
        return

    from .ui import main as gui_main
    gui_main(source_url=args.source)


if __name__ == "__main__":
    main()
