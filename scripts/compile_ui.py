#!/usr/bin/env python3
"""Compile Qt Designer .ui files and resource bundles for PyQt6."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
UI_DIR = ROOT / "app" / "ui"
DESIGNER_DIR = UI_DIR / "designer"
GENERATED_DIR = UI_DIR / "generated"
RESOURCES_QRC = UI_DIR / "resources" / "resources.qrc"


def run(cmd: list[str]) -> None:
    print("$", " ".join(cmd))
    subprocess.run(cmd, check=True)


def _uic_cmd() -> list[str]:
    for candidate in (
        ["pyuic6"],
        [sys.executable, "-m", "PyQt6.uic.pyuic"],
    ):
        try:
            subprocess.run([*candidate, "--version"], capture_output=True, check=True)
            return candidate
        except (FileNotFoundError, subprocess.CalledProcessError):
            continue
    raise RuntimeError("pyuic6 not found. Install PyQt6 in the active environment.")


def _rcc_cmd() -> list[str]:
    for candidate in (
        ["pyrcc6"],
        [sys.executable, "-m", "PyQt6.uic.pyrcc"],
    ):
        try:
            subprocess.run([*candidate, "--version"], capture_output=True, check=True)
            return candidate
        except (FileNotFoundError, subprocess.CalledProcessError):
            continue
    raise RuntimeError("pyrcc6 not found. Install PyQt6 in the active environment.")


def compile_ui_files() -> None:
    GENERATED_DIR.mkdir(parents=True, exist_ok=True)
    uic = _uic_cmd()
    for ui_file in sorted(DESIGNER_DIR.glob("*.ui")):
        output = GENERATED_DIR / f"ui_{ui_file.stem}.py"
        run([*uic, str(ui_file), "-o", str(output)])


def compile_resources() -> None:
    GENERATED_DIR.mkdir(parents=True, exist_ok=True)
    output = GENERATED_DIR / "resources_rc.py"
    try:
        rcc = _rcc_cmd()
    except RuntimeError as exc:
        print(f"Skipping resources compile: {exc}")
        return
    run([*rcc, str(RESOURCES_QRC), "-o", str(output)])


def main() -> None:
    compile_ui_files()
    compile_resources()
    print("UI compile complete.")


if __name__ == "__main__":
    main()
