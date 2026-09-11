import argparse
import json

from .mover import SOURCE_URL, run


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
        "--cli",
        action="store_true",
        help="미리보기 JSON을 CLI로 출력 (Roll20 미입력)",
    )

    args = parser.parse_args()

    if args.dry_run:
        print(
            json.dumps(
                {
                    "source": args.source,
                    "model": "qwen3.5:9b",
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return

    if args.cli:
        print(
            json.dumps(run(args.source).to_dict(), ensure_ascii=False, indent=2)
        )
        return

    from .ui import main as gui_main
    gui_main(source_url=args.source)


if __name__ == "__main__":
    main()
