"""Точка входа: python -m fly_pet."""

from __future__ import annotations

import argparse
import sys

from fly_pet import __version__


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="fly-pet",
        description="Питомец-муха на рабочем столе.",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=__version__,
    )
    parser.add_argument(
        "--gui",
        action="store_true",
        help="Запустить окно питомца (прозрачный спрайт на рабочем столе).",
    )
    args = parser.parse_args(argv)

    # В сборке PyInstaller exe без аргументов сразу открывает GUI.
    if args.gui or getattr(sys, "frozen", False):
        from fly_pet.app import run

        return run()

    py = sys.version_info
    print(
        f"fly-pet ok, версия {__version__}, "
        f"Python {py.major}.{py.minor}.{py.micro}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
