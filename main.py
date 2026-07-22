#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import sys

from PyQt6.QtWidgets import QApplication

from app.player import AudioPlayer
from app.ui.styles import apply_application_theme


def main():
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    apply_application_theme(app)

    player = AudioPlayer()
    player.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()