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
    parser.add_argument(
        "--demo-eat",
        action="store_true",
        help="После старта GUI — демо: создать файл на столе и съесть.",
    )
    args = parser.parse_args(argv)

    # В сборке PyInstaller exe без аргументов сразу открывает GUI.
    if args.gui or args.demo_eat or getattr(sys, "frozen", False):
        from fly_pet.app import run

        return run(demo_eat=bool(args.demo_eat))

    py = sys.version_info
    print(
        f"fly-pet ok, версия {__version__}, "
        f"Python {py.major}.{py.minor}.{py.micro}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
