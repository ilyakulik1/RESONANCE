"""Окно «О программе».

Заполняйте поля класса AboutInfo — они сразу отображаются в диалоге.
Пустые строки и пустые списки не показываются.
"""

from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QLabel,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)


class AboutInfo:
    """Контент окна About — редактируйте здесь."""

    # Заголовок окна и крупное название в диалоге
    window_title = "About KULIK Player"
    app_name = "KULIK Player"
    version = "1.0.0"

    # Короткий слоган под названием (можно оставить "")
    tagline = "Creadted by Ilya Kulik, Curcor AI. In App use UI by Qt6"

    # Основной текст. Несколько абзацев — через пустую строку.
    description = """
Media server for live / broadcast use.
""".strip()

    # Автор / команда (можно оставить "")
    author = ""

    # Копирайт, например: "© 2026 Your Name"
    copyright = ""

    # Сайт или email (можно оставить "")
    contact = ""

    # Дополнительные строки: (заголовок, текст). Пустой список = ничего не показывать.
    # Пример:
    # extras = [
    #     ("License", "MIT"),
    #     ("Credits", "Icons by …"),
    # ]
    extras: list[tuple[str, str]] = []


class AboutDialog(QDialog):
    """Модальное окно About, собранное из AboutInfo."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("aboutDialog")
        self.setWindowTitle(AboutInfo.window_title)
        self.setModal(True)
        self.setMinimumWidth(420)
        self.setMaximumWidth(520)

        root = QVBoxLayout(self)
        root.setContentsMargins(20, 20, 20, 16)
        root.setSpacing(12)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        body = QWidget()
        body_layout = QVBoxLayout(body)
        body_layout.setContentsMargins(0, 0, 4, 0)
        body_layout.setSpacing(10)

        self._add_title(body_layout)
        self._add_version(body_layout)
        self._add_tagline(body_layout)
        self._add_description(body_layout)
        self._add_meta(body_layout)
        self._add_extras(body_layout)
        body_layout.addStretch(1)

        scroll.setWidget(body)
        root.addWidget(scroll, 1)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok)
        buttons.accepted.connect(self.accept)
        root.addWidget(buttons)

    def _add_title(self, layout: QVBoxLayout) -> None:
        name = AboutInfo.app_name.strip()
        if not name:
            return
        label = QLabel(name)
        label.setObjectName("aboutAppName")
        label.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        label.setStyleSheet("font-size: 20px; font-weight: 600;")
        layout.addWidget(label)

    def _add_version(self, layout: QVBoxLayout) -> None:
        version = AboutInfo.version.strip()
        if not version:
            return
        label = QLabel(f"Version {version}")
        label.setObjectName("aboutVersion")
        label.setStyleSheet("color: #9f9f9f;")
        layout.addWidget(label)

    def _add_tagline(self, layout: QVBoxLayout) -> None:
        tagline = AboutInfo.tagline.strip()
        if not tagline:
            return
        label = QLabel(tagline)
        label.setObjectName("aboutTagline")
        label.setWordWrap(True)
        label.setStyleSheet("color: #e0e0e0;")
        layout.addWidget(label)

    def _add_description(self, layout: QVBoxLayout) -> None:
        text = AboutInfo.description.strip()
        if not text:
            return
        for paragraph in text.split("\n\n"):
            paragraph = paragraph.strip()
            if not paragraph:
                continue
            label = QLabel(paragraph.replace("\n", " "))
            label.setObjectName("aboutDescription")
            label.setWordWrap(True)
            layout.addWidget(label)

    def _add_meta(self, layout: QVBoxLayout) -> None:
        rows = (
            ("Author", AboutInfo.author),
            ("Copyright", AboutInfo.copyright),
            ("Contact", AboutInfo.contact),
        )
        for title, value in rows:
            value = (value or "").strip()
            if not value:
                continue
            layout.addWidget(self._meta_row(title, value))

    def _add_extras(self, layout: QVBoxLayout) -> None:
        for title, value in AboutInfo.extras:
            title = (title or "").strip()
            value = (value or "").strip()
            if not title or not value:
                continue
            layout.addWidget(self._meta_row(title, value))

    @staticmethod
    def _meta_row(title: str, value: str) -> QLabel:
        label = QLabel(f"<b>{title}:</b> {value}")
        label.setObjectName("aboutMeta")
        label.setWordWrap(True)
        label.setTextFormat(Qt.TextFormat.RichText)
        label.setOpenExternalLinks(True)
        return label


def show_about_dialog(parent: QWidget | None = None) -> None:
    AboutDialog(parent).exec()
