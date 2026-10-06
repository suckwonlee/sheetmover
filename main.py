import sys

if __name__ == "__main__":
    if "--worker-full-run" in sys.argv:
        from sheet_mover.full_run import cli_main

        args = [arg for arg in sys.argv[1:] if arg != "--worker-full-run"]
        raise SystemExit(cli_main(args))

    from sheet_mover.ui import main
    main()
