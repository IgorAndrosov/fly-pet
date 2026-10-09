"""Сборка fly-pet в exe через PyInstaller (onedir + onefile).

Запуск из корня репозитория проектным питоном::

    .venv/Scripts/python.exe tools/build_exe.py
    .venv/Scripts/python.exe tools/build_exe.py --dry-run
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

# Тяжёлые модули Qt, которые питомец не использует — уменьшают размер сборки.
_EXCLUDE_MODULES = (
    # Веб-движок (Chromium) — самый жирный, GUI его не трогает
    "PyQt6.QtWebEngineCore",
    "PyQt6.QtWebEngineWidgets",
    "PyQt6.QtWebEngineQuick",
    "PyQt6.QtWebChannel",
    # Сеть/железо, не нужные прозрачному окну и трею
    "PyQt6.QtBluetooth",
    "PyQt6.QtNfc",
    "PyQt6.QtPositioning",
    "PyQt6.QtLocation",
    "PyQt6.QtSerialPort",
    "PyQt6.QtSensors",
    # Медиа и 3D
    "PyQt6.QtMultimedia",
    "PyQt6.QtMultimediaWidgets",
    "PyQt6.QtSpatialAudio",
    "PyQt6.Qt3DCore",
    "PyQt6.Qt3DRender",
    "PyQt6.Qt3DInput",
    "PyQt6.Qt3DLogic",
    "PyQt6.Qt3DAnimation",
    "PyQt6.Qt3DExtras",
    # Прочее неиспользуемое
    "PyQt6.QtSql",
    "PyQt6.QtTest",
    "PyQt6.QtXml",
    "PyQt6.QtDesigner",
    "PyQt6.QtHelp",
    "PyQt6.QtPdf",
    "PyQt6.QtPdfWidgets",
    "PyQt6.QtQuick",
    "PyQt6.QtQuickWidgets",
    "PyQt6.QtQml",
    "PyQt6.QtRemoteObjects",
)


def _sep() -> str:
    """Разделитель --add-data: Windows «;», иначе «:»."""
    return ";" if sys.platform == "win32" else ":"


def build_commands(repo_root: Path | None = None) -> list[tuple[str, list[str]]]:
    """Возвращает список (метка, argv) для двух сборок."""
    root = (repo_root or REPO_ROOT).resolve()
    entry = str(root / "fly_pet" / "__main__.py")
    icon = str(root / "assets" / "fly_pet.ico")
    sep = _sep()
    hidden = [
        "fly_pet",
        "fly_pet.app",
        "fly_pet.window",
        "fly_pet.locomotion",
        "fly_pet.config",
        "fly_pet.state",
        "fly_pet.needs",
        "fly_pet.bubble",
        "fly_pet.phrases",
        "fly_pet.animation",
        "fly_pet.tray",
        "fly_pet.paths",
        "fly_pet.logging_setup",
    ]
    common = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconsole",
        f"--icon={icon}",
        f"--add-data=assets{sep}assets",
        f"--add-data=config{sep}config",
        "--noconfirm",
        "--clean",
        "--paths",
        str(root),
        "--distpath",
        str(root / "dist"),
        "--workpath",
        str(root / "build"),
        "--specpath",
        str(root / "build"),
    ]
    for name in hidden:
        common.extend(["--hidden-import", name])
    for mod in _EXCLUDE_MODULES:
        common.extend(["--exclude-module", mod])

    onedir = [
        *common,
        "--onedir",
        "--name",
        "fly-pet",
        entry,
    ]
    onefile = [
        *common,
        "--onefile",
        "--name",
        "fly-pet-onefile",
        entry,
    ]
    return [("onedir → dist/fly-pet/", onedir), ("onefile → dist/fly-pet-onefile.exe", onefile)]


def _format_size(path: Path) -> str:
    if path.is_file():
        size = path.stat().st_size
    elif path.is_dir():
        size = sum(p.stat().st_size for p in path.rglob("*") if p.is_file())
    else:
        return "нет"
    if size >= 1024 * 1024:
        return f"{size / (1024 * 1024):.1f} МБ"
    if size >= 1024:
        return f"{size / 1024:.1f} КБ"
    return f"{size} Б"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Сборка fly-pet через PyInstaller")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Только напечатать команды, не запускать PyInstaller",
    )
    args = parser.parse_args(argv)

    root = REPO_ROOT
    commands = build_commands(root)

    if args.dry_run:
        print("Исключённые модули Qt:")
        for mod in _EXCLUDE_MODULES:
            print(f"  - {mod}")
        print()
        for label, cmd in commands:
            print(f"# {label}")
            print(subprocess.list2cmdline(cmd))
            print()
        return 0

    if shutil.which("pyinstaller") is None and not _pyinstaller_importable():
        print(
            "PyInstaller не найден. Установите в venv: pip install pyinstaller",
            file=sys.stderr,
        )
        return 1

    for label, cmd in commands:
        print(f"=== Сборка: {label} ===")
        print(subprocess.list2cmdline(cmd))
        result = subprocess.run(cmd, cwd=str(root), check=False)
        if result.returncode != 0:
            print(f"Ошибка сборки ({label}), код {result.returncode}", file=sys.stderr)
            return int(result.returncode)

    onedir_path = root / "dist" / "fly-pet"
    onefile_path = root / "dist" / "fly-pet-onefile.exe"
    if not onefile_path.is_file():
        # не-Windows: без .exe
        alt = root / "dist" / "fly-pet-onefile"
        if alt.is_file():
            onefile_path = alt

    print()
    print("Готово:")
    print(f"  {onedir_path}  ({_format_size(onedir_path)})")
    print(f"  {onefile_path}  ({_format_size(onefile_path)})")
    return 0


def _pyinstaller_importable() -> bool:
    try:
        import PyInstaller  # noqa: F401
    except ImportError:
        return False
    return True


if __name__ == "__main__":
    raise SystemExit(main())
