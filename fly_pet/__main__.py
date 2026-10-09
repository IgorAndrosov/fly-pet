"""Точка входа: python -m fly_pet."""

from __future__ import annotations

import argparse
import sys

from fly_pet import __version__


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="fly-pet",
        description="Питомец-муха на рабочем столе. В этом шаге — только скелет, без GUI.",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=__version__,
    )
    parser.parse_args(argv)

    py = sys.version_info
    print(
        f"fly-pet ok, версия {__version__}, "
        f"Python {py.major}.{py.minor}.{py.micro}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
