"""Acknowledgement popup shown once when PIXEL starts.

A frameless, window-modal QDialog that covers the main window: it paints a dim
layer and holds a centered white card. It is opened with open() (not exec()),
so the GUI event loop, the live preview and the worker threads keep running
underneath; it only blocks clicks on the main window until it is dismissed.
"""

from __future__ import annotations

from PySide6.QtCore import QEvent, QPointF, QRectF, QSize, Qt
from PySide6.QtGui import QColor, QPainter, QPen, QPolygonF
from PySide6.QtWidgets import (
    QDialog,
    QFrame,
    QGraphicsDropShadowEffect,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from gui.app import (
    BG,
    BORDER,
    BORDER_STRONG,
    TEXT,
    TEXT_FAINT,
    TEXT_MUTED,
    LogoMark,
)

CARD_MAX_WIDTH = 560

STYLESHEET = f"""
QDialog#ackOverlay {{
    background: transparent;
}}

QFrame#ackCard {{
    background: transparent;
    border: none;
}}

QLabel {{
    background: transparent;
    color: {TEXT};
}}

QLabel#ackBrand {{
    font-size: 20pt;
    font-weight: 700;
    letter-spacing: 2px;
}}

QLabel#ackBrandSubtitle {{
    font-size: 10.5pt;
    font-style: italic;
    color: {TEXT_MUTED};
}}

QLabel#ackHeading {{
    font-family: "Georgia";
    font-size: 21pt;
    font-weight: 700;
}}

QLabel#ackBody {{
    font-size: 11pt;
    color: #262626;
}}

QLabel#ackName {{
    font-family: "Georgia";
    font-size: 16pt;
    font-weight: 700;
}}

QLabel#ackItalic {{
    font-size: 11pt;
    font-style: italic;
    color: {TEXT_MUTED};
}}

QLabel#ackAuthor {{
    font-family: "Georgia";
    font-size: 13pt;
    font-weight: 700;
}}

QLabel#ackAuthorRole {{
    font-size: 10pt;
    color: {TEXT_MUTED};
}}

QPushButton#ackClose {{
    font-size: 13pt;
    font-weight: 400;
    color: {TEXT_MUTED};
    background: transparent;
    border: 1px solid transparent;
    border-radius: 16px;
    padding: 0;
}}

QPushButton#ackClose:hover {{
    color: {TEXT};
    background: #f0f0f0;
    border-color: {BORDER};
}}
"""


class AccentDivider(QWidget):
    """Thin gray line with a small diamond in the middle."""

    def __init__(self) -> None:
        super().__init__()
        self.setFixedHeight(12)

    def paintEvent(self, event) -> None:  # noqa: N802 (Qt override)
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, cy = self.width(), self.height() / 2
        c, d = w / 2, 4.0
        p.setPen(QPen(QColor(BORDER_STRONG), 1))
        p.drawLine(QPointF(w * 0.12, cy), QPointF(c - d - 6, cy))
        p.drawLine(QPointF(c + d + 6, cy), QPointF(w * 0.88, cy))
        p.setPen(QPen(QColor(TEXT_FAINT), 1.2))
        p.setBrush(QColor(BG))
        p.drawPolygon(QPolygonF([QPointF(c, cy - d), QPointF(c + d, cy),
                                 QPointF(c, cy + d), QPointF(c - d, cy)]))


class AcknowledgementCard(QFrame):
    """White rounded card with a hairline border and corner bracket accents."""

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("ackCard")

    def paintEvent(self, event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        p.setPen(QPen(QColor(BORDER), 1))
        p.setBrush(QColor(BG))
        p.drawRoundedRect(rect, 16, 16)

        # Corner brackets, inset from the rounded corners.
        pen = QPen(QColor(BORDER_STRONG), 1.2)
        pen.setCapStyle(Qt.PenCapStyle.SquareCap)
        p.setPen(pen)
        m, length = 16.0, 18.0
        w, h = self.width(), self.height()
        for x, y, dx, dy in ((m, m, 1, 1), (w - m, m, -1, 1),
                             (m, h - m, 1, -1), (w - m, h - m, -1, -1)):
            p.drawLine(QPointF(x, y), QPointF(x + dx * length, y))
            p.drawLine(QPointF(x, y), QPointF(x, y + dy * length))


def _label(text: str, name: str, wrap: bool = False) -> QLabel:
    label = QLabel(text)
    label.setObjectName(name)
    label.setAlignment(Qt.AlignmentFlag.AlignHCenter)
    label.setWordWrap(wrap)
    return label


class AcknowledgementDialog(QDialog):
    """Dimmed overlay over the main window with the acknowledgement card."""

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setObjectName("ackOverlay")
        self.setWindowTitle("Acknowledgement")
        self.setWindowFlags(Qt.WindowType.Dialog | Qt.WindowType.FramelessWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setWindowModality(Qt.WindowModality.WindowModal)
        self.setStyleSheet(STYLESHEET)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(24, 24, 24, 24)
        outer.addStretch()
        row = QHBoxLayout()
        row.addStretch()
        self.card = AcknowledgementCard()
        row.addWidget(self.card)
        row.addStretch()
        outer.addLayout(row)
        outer.addStretch()

        shadow = QGraphicsDropShadowEffect(self.card)
        shadow.setBlurRadius(48)
        shadow.setOffset(0, 14)
        shadow.setColor(QColor(0, 0, 0, 70))
        self.card.setGraphicsEffect(shadow)

        self._build_card()
        parent.installEventFilter(self)     # follow the main window if it moves/resizes

    def _build_card(self) -> None:
        layout = QVBoxLayout(self.card)
        layout.setContentsMargins(44, 18, 18, 30)
        layout.setSpacing(0)

        close_row = QHBoxLayout()
        close_row.addStretch()
        self.close_button = QPushButton("✕")
        self.close_button.setObjectName("ackClose")
        self.close_button.setAccessibleName("Close acknowledgement")
        self.close_button.setToolTip("Close")
        self.close_button.setFixedSize(QSize(32, 32))
        self.close_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.close_button.clicked.connect(self.reject)
        close_row.addWidget(self.close_button)
        layout.addLayout(close_row)

        content = QVBoxLayout()
        content.setContentsMargins(0, 0, 26, 0)    # balances the close button column
        content.setSpacing(0)
        layout.addLayout(content)

        brand = QHBoxLayout()
        brand.setSpacing(12)
        brand.addStretch()
        brand.addWidget(LogoMark(44), alignment=Qt.AlignmentFlag.AlignVCenter)
        names = QVBoxLayout()
        names.setSpacing(0)
        brand_name = QLabel("PIXEL")
        brand_name.setObjectName("ackBrand")
        brand_subtitle = QLabel("Vision-Language Computer Agent")
        brand_subtitle.setObjectName("ackBrandSubtitle")
        names.addWidget(brand_name)
        names.addWidget(brand_subtitle)
        brand.addLayout(names)
        brand.addStretch()
        content.addLayout(brand)

        content.addSpacing(18)
        content.addWidget(AccentDivider())
        content.addSpacing(16)
        content.addWidget(_label("Acknowledgement", "ackHeading"))
        content.addSpacing(16)
        content.addWidget(_label("I would like to express my sincere gratitude to", "ackBody",
                                 wrap=True))
        content.addSpacing(12)
        content.addWidget(_label("Dr. Rakeshkumar Sidharthan", "ackName", wrap=True))
        content.addSpacing(8)
        for line in ("Department of Mechanical Engineering", "Amrita School of Engineering",
                     "Coimbatore"):
            content.addWidget(_label(line, "ackItalic", wrap=True))
        content.addSpacing(14)
        content.addWidget(_label(
            "for his valuable guidance, continuous support, and encouragement throughout "
            "this project for the Generative AI course.", "ackBody", wrap=True))
        content.addSpacing(18)
        content.addWidget(AccentDivider())
        content.addSpacing(14)
        content.addWidget(_label("Sreekeerthi M.", "ackAuthor"))
        content.addSpacing(2)
        content.addWidget(_label("Final-Year Automation and Robotics Engineering Student",
                                 "ackAuthorRole", wrap=True))

    # ---------------------------------------------------------
    # Overlay geometry and painting
    # ---------------------------------------------------------

    def _fit_to_parent(self) -> None:
        parent = self.parentWidget()
        if parent is None:
            return
        self.setGeometry(parent.geometry())      # the main window's client area
        width = max(360, min(CARD_MAX_WIDTH, parent.width() - 64))
        self.card.setFixedWidth(width)

    def showEvent(self, event) -> None:  # noqa: N802
        self._fit_to_parent()
        super().showEvent(event)
        self.close_button.setFocus()

    def eventFilter(self, watched, event) -> bool:  # noqa: N802
        if watched is self.parentWidget() and self.isVisible() and \
                event.type() in (QEvent.Type.Move, QEvent.Type.Resize):
            self._fit_to_parent()
        return super().eventFilter(watched, event)

    def paintEvent(self, event) -> None:  # noqa: N802
        p = QPainter(self)
        p.fillRect(self.rect(), QColor(10, 10, 10, 115))     # dims the main window
