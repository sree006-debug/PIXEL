from __future__ import annotations

import os
import re
import sys
import time
from datetime import datetime
from html import escape
from pathlib import Path

# Allow `python gui/app.py` as well as `python -m gui.app`.
_PROJECT_ROOT = str(Path(__file__).resolve().parent.parent)
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from PySide6.QtCore import QEvent, QPointF, QRectF, QSize, Qt, QThread, QTimer, QUrl, Signal
from PySide6.QtGui import (
    QColor,
    QDesktopServices,
    QFont,
    QIcon,
    QImage,
    QKeySequence,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
    QPolygonF,
    QShortcut,
)
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QStackedWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

import config
from agent.vlm import PROVIDERS
from gui import report as task_report
from gui.agent_worker import AgentWorker, short_reason
from gui.screen_preview import ScreenPreview
from gui.voice_worker import VOICE_DURATION, VoiceWorker


# -------------------------------------------------------------
# Palette
# -------------------------------------------------------------

BG = "#ffffff"
SIDEBAR = "#fafafa"
SURFACE = "#f5f5f5"
SURFACE_RAISED = "#ffffff"
VIEWPORT = "#ececec"
BORDER = "#e4e4e4"
BORDER_STRONG = "#d2d2d2"
TEXT = "#0a0a0a"
TEXT_MUTED = "#6b6b6b"
TEXT_FAINT = "#a3a3a3"
ACCENT = "#0a0a0a"
ACCENT_SOFT = "#1f1f1f"
SUCCESS = "#16a34a"
WARNING = "#d97706"
ERROR = "#dc2626"

# Status tones: dot/label colour for each kind of agent state.
TONES = {
    "ready": SUCCESS,
    "info": ACCENT_SOFT,
    "working": ACCENT_SOFT,
    "success": SUCCESS,
    "warning": WARNING,
    "error": ERROR,
    "idle": TEXT_FAINT,
}

# Activity log entry kinds -> timeline dot colour
LOG_KINDS = {
    "info": TEXT_FAINT,
    "success": SUCCESS,
    "step": ACCENT,
    "warning": WARNING,
    "error": ERROR,
    "voice": ACCENT,
}

# Segoe Fluent Icons (Windows 11) / Segoe MDL2 Assets (Windows 10) code points.
ICON_FONTS = ["Segoe Fluent Icons", "Segoe MDL2 Assets"]
ICONS = {
    "home": "",
    "new_task": "",
    "mic": "",
    "history": "",
    "settings": "",
    "book": "",
    "help": "",
    "refresh": "",
    "monitor": "",
    "edit": "",
    "pulse": "",
    "page": "",
    "list": "",
    "play": "",
    "stop": "",
    "arrow": "",
    "globe": "",
    "calculator": "",
    "folder": "",
    "robot": "",
}

# (icon, card label, text placed in the task box for review)
EXAMPLES = (
    ("new_task", "Create a meeting\nto-do list in Notepad",
     "Open Windows Notepad and write a meeting to-do list with three items."),
    ("globe", "Search for latest\nAI news in browser",
     "Open Chrome and search for the latest AI news."),
    ("calculator", "Open Calculator\nand compute 25 × 16",
     "Open Calculator and calculate 25 multiplied by 16."),
    ("folder", "Organize files in a\nnew folder",
     "Open File Explorer and create a new folder on the Desktop named Organized."),
)

STYLESHEET = f"""
* {{
    font-family: "Segoe UI";
    color: {TEXT};
}}

QMainWindow, QWidget#root, QWidget#center {{
    background: {BG};
}}

QToolTip {{
    background: {SURFACE_RAISED};
    border: 1px solid {BORDER_STRONG};
    padding: 4px 8px;
}}

/* ---------- Dialogs (otherwise they follow the Windows dark palette) ---------- */

QMessageBox, QDialog {{
    background: {BG};
}}

QMessageBox QLabel {{
    color: {TEXT};
    background: transparent;
    font-size: 10.5pt;
}}

QMessageBox QPushButton, QDialog QPushButton {{
    color: {TEXT};
    font-weight: 400;
    background: {SURFACE};
    border: 1px solid {BORDER_STRONG};
    min-width: 80px;
    min-height: 30px;
}}

QMessageBox QPushButton:hover, QDialog QPushButton:hover {{
    background: #ececec;
    border-color: #b5b5b5;
}}

QMessageBox QPushButton:focus, QDialog QPushButton:focus {{
    border: 1px solid {ACCENT};
}}

/* ---------- Left sidebar ---------- */

QFrame#sidebar {{
    background: {SIDEBAR};
    border: none;
    border-right: 1px solid {BORDER};
}}

QLabel#brandName {{
    font-size: 21pt;
    font-weight: 700;
    letter-spacing: 2px;
}}

QLabel#brandSubtitle {{
    font-size: 10pt;
    color: {TEXT_MUTED};
}}

QPushButton#navButton {{
    text-align: left;
    font-size: 12pt;
    font-weight: 400;
    color: {TEXT_MUTED};
    background: transparent;
    border: none;
    border-radius: 8px;
    padding: 0 16px;
}}

QPushButton#navButton:hover {{
    background: {SURFACE};
    color: {TEXT};
}}

QPushButton#navButton:checked {{
    color: white;
    background: {ACCENT};
}}

QPushButton#navButton:disabled {{
    color: {TEXT_FAINT};
    background: transparent;
    border: none;
}}

QFrame#footerCard {{
    background: {SURFACE};
    border: 1px solid {BORDER};
    border-radius: 10px;
}}

QLabel#footerText {{
    font-size: 10pt;
    color: {TEXT_MUTED};
}}

/* ---------- Center header ---------- */

QLabel#pageTitle {{
    font-size: 25pt;
    font-weight: 700;
}}

QLabel#pageSubtitle {{
    font-size: 11.5pt;
    color: {TEXT_MUTED};
}}

/* ---------- Cards ---------- */

QFrame#card {{
    background: {SURFACE};
    border: 1px solid {BORDER};
    border-radius: 12px;
}}

QLabel#cardTitle {{
    font-size: 12.5pt;
    font-weight: 600;
}}

QLabel#cardSubtitle {{
    font-size: 10pt;
    color: {TEXT_MUTED};
}}

QLabel#examplesLabel {{
    font-size: 10.5pt;
    color: {TEXT_MUTED};
}}

/* ---------- Vision viewport ---------- */

QFrame#viewport {{
    background: {VIEWPORT};
    border: 1px solid {BORDER_STRONG};
    border-radius: 8px;
}}

QLabel#emptyTitle {{
    font-size: 10pt;
    font-weight: 700;
    letter-spacing: 3px;
    color: {TEXT};
}}

QLabel#emptySubtitle {{
    font-size: 9.5pt;
    color: {TEXT_MUTED};
}}

QLabel#screenPreview {{
    background: transparent;
    border: none;
}}

/* ---------- Task composer ---------- */

QFrame#composer {{
    background: {BG};
    border: 1px solid {ACCENT};
    border-radius: 10px;
}}

QLineEdit#commandInput {{
    background: transparent;
    border: none;
    padding: 0 4px;
    font-size: 12pt;
    selection-background-color: {ACCENT};
}}

QComboBox#providerCombo {{
    background: {SURFACE_RAISED};
    border: 1px solid {BORDER_STRONG};
    border-radius: 8px;
    padding: 0 12px;
    font-size: 11.5pt;
    min-width: 120px;
}}

QComboBox#providerCombo:hover {{
    border-color: #a3a3a3;
}}

QComboBox#providerCombo::drop-down {{
    border: none;
    width: 26px;
}}

QComboBox#providerCombo QAbstractItemView {{
    background: {SURFACE_RAISED};
    border: 1px solid {BORDER_STRONG};
    selection-background-color: {ACCENT};
    selection-color: white;
    outline: none;
}}

QPushButton {{
    font-size: 11.5pt;
    font-weight: 600;
    border-radius: 8px;
    padding: 0 18px;
}}

QPushButton#primaryButton {{
    color: white;
    border: 1px solid {ACCENT};
    background: {ACCENT};
}}

QPushButton#primaryButton:hover {{
    border-color: #2b2b2b;
    background: #2b2b2b;
}}

QPushButton#primaryButton:pressed {{
    background: #000000;
    border-color: #000000;
}}

QPushButton#secondaryButton, QPushButton#iconButton {{
    color: {TEXT};
    font-weight: 400;
    background: {SURFACE_RAISED};
    border: 1px solid {BORDER_STRONG};
}}

QPushButton#iconButton {{
    padding: 0;
}}

QPushButton#secondaryButton:hover, QPushButton#iconButton:hover {{
    background: #f0f0f0;
    border-color: #b5b5b5;
}}

QPushButton#secondaryButton:pressed, QPushButton#iconButton:pressed {{
    background: #e6e6e6;
}}

QPushButton#smallButton {{
    font-size: 10pt;
    font-weight: 400;
    color: {TEXT_MUTED};
    background: {SURFACE_RAISED};
    border: 1px solid {BORDER_STRONG};
    border-radius: 6px;
    padding: 0 10px;
}}

QPushButton#smallButton:hover {{
    color: {TEXT};
    border-color: #b5b5b5;
}}

QPushButton#stopButton {{
    color: {ERROR};
    background: #fef2f2;
    border: 1px solid #f5a3a3;
}}

QPushButton#stopButton:hover {{
    background: #fee2e2;
    border-color: {ERROR};
}}

QPushButton#stopButton:pressed {{
    background: #fecaca;
}}

QPushButton:disabled {{
    color: {TEXT_FAINT};
    background: {SURFACE};
    border: 1px solid {BORDER};
}}

QPushButton#exampleCard {{
    background: {SURFACE};
    border: 1px solid {BORDER};
    border-radius: 10px;
    padding: 0;
}}

QPushButton#exampleCard:hover {{
    background: {SURFACE_RAISED};
    border-color: {BORDER_STRONG};
}}

QLabel#exampleText {{
    font-size: 9.5pt;
    color: {TEXT};
}}

/* ---------- Right sidebar ---------- */

QFrame#rightbar {{
    background: {SIDEBAR};
    border: none;
    border-left: 1px solid {BORDER};
}}

QLabel#statusState {{
    font-size: 17pt;
    font-weight: 600;
}}

QLabel#statusDetail {{
    font-size: 10.5pt;
    color: {TEXT_MUTED};
}}

QFrame#innerBox {{
    background: {BG};
    border: 1px solid {BORDER};
    border-radius: 8px;
}}

QLabel#currentTask {{
    font-size: 10.5pt;
    color: {TEXT_MUTED};
}}

QFrame#divider {{
    background: {BORDER};
    max-height: 1px;
    min-height: 1px;
    border: none;
}}

QLabel#metaKey {{
    font-size: 10.5pt;
    color: {TEXT_MUTED};
}}

QLabel#metaValue {{
    font-size: 10.5pt;
    color: {TEXT};
}}

QLabel#stageName {{
    font-size: 10pt;
}}

QLabel#stageDetail, QLabel#stageTime {{
    font-size: 8.5pt;
    color: {TEXT_MUTED};
}}

QTextEdit#reportText {{
    background: {SURFACE};
    border: 1px solid {BORDER};
    border-radius: 8px;
    padding: 8px;
    font-size: 10pt;
}}

QLabel#reportBadge {{
    font-size: 10pt;
    font-weight: 600;
    border-radius: 6px;
    padding: 3px 10px;
}}

QTextEdit#activityLog {{
    background: transparent;
    border: none;
    font-size: 10.5pt;
}}

QScrollBar:vertical {{
    background: transparent;
    width: 8px;
    margin: 2px;
}}

QScrollBar::handle:vertical {{
    background: {BORDER_STRONG};
    border-radius: 3px;
    min-height: 24px;
}}

QScrollBar::handle:vertical:hover {{
    background: #b5b5b5;
}}

QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical,
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{
    background: none;
    height: 0;
}}
"""


# -------------------------------------------------------------
# Icons and small painted widgets (no external images needed)
# -------------------------------------------------------------


def _icon_font(pixel_size: int) -> QFont:
    font = QFont()
    font.setFamilies(ICON_FONTS)
    font.setPixelSize(pixel_size)
    return font


def glyph_icon(name: str, color: str, size: int = 20) -> QIcon:
    """Render an icon-font glyph to a QIcon (crisp at 2x)."""
    scale = 2
    pixmap = QPixmap(size * scale, size * scale)
    pixmap.fill(Qt.GlobalColor.transparent)
    p = QPainter(pixmap)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setRenderHint(QPainter.RenderHint.TextAntialiasing)
    p.setFont(_icon_font(int(size * scale * 0.82)))
    p.setPen(QColor(color))
    p.drawText(pixmap.rect(), Qt.AlignmentFlag.AlignCenter, ICONS[name])
    p.end()
    pixmap.setDevicePixelRatio(scale)
    return QIcon(pixmap)


def glyph_label(name: str, color: str, size: int = 20) -> QLabel:
    label = QLabel(ICONS[name])
    # The app stylesheet sets Segoe UI on every widget, so the icon font goes here too.
    families = ", ".join(f'"{f}"' for f in ICON_FONTS)
    label.setStyleSheet(f"font-family: {families}; font-size: {size}px; color: {color};"
                        " background: transparent; border: none;")
    label.setFixedSize(size + 6, size + 6)
    label.setAlignment(Qt.AlignmentFlag.AlignCenter)
    return label


class StatusDot(QWidget):
    """A coloured dot with an optional soft pulsing halo."""

    def __init__(self, color: str, size: int = 10, pulse: bool = False) -> None:
        super().__init__()
        self._color = QColor(color)
        self._dot = size
        self._phase = 0.0
        self.setFixedSize(size * 2, size * 2)

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self.set_pulse(pulse)

    def set_color(self, color: str) -> None:
        self._color = QColor(color)
        self.update()

    def set_pulse(self, pulse: bool) -> None:
        if pulse:
            self._timer.start(40)
        else:
            self._timer.stop()
            self._phase = 0.0
            self.update()

    def _tick(self) -> None:
        self._phase = (self._phase + 0.025) % 1.0
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802 (Qt override)
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        center = self.rect().center()
        cx, cy = center.x() + 0.5, center.y() + 0.5

        if self._timer.isActive():
            halo = QColor(self._color)
            halo.setAlphaF(0.35 * (1.0 - self._phase))
            r = self._dot / 2 + self._phase * self._dot / 2
            p.setBrush(halo)
            p.drawEllipse(QRectF(cx - r, cy - r, 2 * r, 2 * r))

        r = self._dot / 2
        p.setBrush(self._color)
        p.drawEllipse(QRectF(cx - r, cy - r, 2 * r, 2 * r))


class LogoMark(QWidget):
    """PIXEL logo: an isometric blue cube."""

    def __init__(self, size: int = 54) -> None:
        super().__init__()
        self.setFixedSize(size, size)

    def paintEvent(self, event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        s = self.width()
        c = s / 2
        r = s * 0.46
        dx = r * 0.866  # cos(30°)

        top = QPointF(c, c - r)
        upper_right = QPointF(c + dx, c - r / 2)
        lower_right = QPointF(c + dx, c + r / 2)
        bottom = QPointF(c, c + r)
        lower_left = QPointF(c - dx, c + r / 2)
        upper_left = QPointF(c - dx, c - r / 2)
        middle = QPointF(c, c)

        p.setPen(QPen(QColor(SIDEBAR), 1.5))
        for points, color in (
            ((top, upper_right, middle, upper_left), "#737373"),
            ((upper_left, middle, bottom, lower_left), "#262626"),
            ((middle, upper_right, lower_right, bottom), "#000000"),
        ):
            path = QPainterPath()
            path.addPolygon(QPolygonF(list(points)))
            path.closeSubpath()
            p.fillPath(path, QColor(color))
            p.drawPath(path)


class VisionReticle(QWidget):
    """Viewfinder icon used in the vision panel's empty state."""

    def __init__(self, size: int = 76) -> None:
        super().__init__()
        self.setFixedSize(size, size)

    def paintEvent(self, event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        s = self.width()
        c = s / 2

        p.setPen(QPen(QColor(BORDER), 1))
        p.setBrush(QColor(SURFACE))
        p.drawRoundedRect(QRectF(0.5, 0.5, s - 1, s - 1), 18, 18)

        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(QPen(QColor(TEXT_MUTED), 1.6))
        w, h = s * 0.5, s * 0.34
        p.drawRoundedRect(QRectF(c - w / 2, c - h / 2 - 4, w, h), 3, 3)
        p.drawLine(int(c - 7), int(c + h / 2 + 4), int(c + 7), int(c + h / 2 + 4))
        p.drawLine(int(c), int(c + h / 2 - 4), int(c), int(c + h / 2 + 4))

        p.setPen(QPen(QColor(ACCENT), 1.6))
        p.drawEllipse(QRectF(c - 6, c - 10, 12, 12))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(ACCENT_SOFT))
        p.drawEllipse(QRectF(c - 2, c - 6, 4, 4))


class VisionEmptyState(QWidget):
    """Placeholder shown until a real screenshot is displayed."""

    def __init__(self) -> None:
        super().__init__()

        layout = QVBoxLayout(self)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.setSpacing(6)

        icon = VisionReticle()
        title = QLabel("LIVE DESKTOP VIEW")
        title.setObjectName("emptyTitle")
        self.subtitle = QLabel("Waiting for screen capture")
        self.subtitle.setObjectName("emptySubtitle")

        layout.addWidget(icon, alignment=Qt.AlignmentFlag.AlignHCenter)
        layout.addSpacing(12)
        layout.addWidget(title, alignment=Qt.AlignmentFlag.AlignHCenter)
        layout.addWidget(self.subtitle, alignment=Qt.AlignmentFlag.AlignHCenter)


# -------------------------------------------------------------
# Live execution timeline
# -------------------------------------------------------------

# (key, name). The last row is the task outcome.
STAGES = (
    ("observing", "Observing"),
    ("planning", "Planning"),
    ("validating", "Validating"),
    ("executing", "Executing"),
    ("verifying", "Verifying"),
    ("outcome", "Outcome"),
)


class StageIcon(QWidget):
    """Timeline marker plus connector line; its look follows the stage state."""

    def __init__(self, first: bool, last: bool) -> None:
        super().__init__()
        self.state = "pending"
        self._first, self._last = first, last
        self.setFixedWidth(20)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Expanding)

    def set_state(self, state: str) -> None:
        self.state = state
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        c, cy, r = self.width() / 2, 11.0, 6.5
        p.setPen(QPen(QColor(BORDER_STRONG), 1.5))
        if not self._first:
            p.drawLine(QPointF(c, 0), QPointF(c, cy - r - 2))
        if not self._last:
            p.drawLine(QPointF(c, cy + r + 2), QPointF(c, self.height()))

        circle = QRectF(c - r, cy - r, 2 * r, 2 * r)
        if self.state == "pending":
            p.setPen(QPen(QColor(TEXT_FAINT), 1.5))
            p.setBrush(QColor(BG))
            p.drawEllipse(circle)
            return
        fill = {"active": TEXT, "done": SUCCESS, "failed": ERROR, "stopped": TEXT_MUTED}
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(fill[self.state]))
        p.drawEllipse(circle)
        pen = QPen(QColor("#ffffff"), 1.6)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        p.setPen(pen)
        if self.state == "active":
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor("#ffffff"))
            p.drawEllipse(QRectF(c - 2.2, cy - 2.2, 4.4, 4.4))
        elif self.state == "done":
            p.drawPolyline(QPolygonF([QPointF(c - 3, cy), QPointF(c - 0.8, cy + 2.4),
                                      QPointF(c + 3.2, cy - 2.4)]))
        elif self.state == "failed":
            p.drawLine(QPointF(c - 2.5, cy - 2.5), QPointF(c + 2.5, cy + 2.5))
            p.drawLine(QPointF(c + 2.5, cy - 2.5), QPointF(c - 2.5, cy + 2.5))
        else:  # stopped
            p.fillRect(QRectF(c - 2.3, cy - 2.3, 4.6, 4.6), QColor("#ffffff"))


class ExecutionTimeline(QWidget):
    """Stages of the current step cycle, driven only by real agent events.

    Each Planning event starts a new cycle: the later stages go back to pending,
    so a retry or a repeated action never shows stages from an earlier cycle as
    if they belonged to this one. Times are when PIXEL received the event.
    """

    def __init__(self) -> None:
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self.rows: dict[str, tuple[StageIcon, QLabel, QLabel, QLabel]] = {}
        self.active: str | None = None
        for i, (key, name) in enumerate(STAGES):
            row = QHBoxLayout()
            row.setContentsMargins(0, 0, 0, 0)
            row.setSpacing(10)
            icon = StageIcon(first=i == 0, last=i == len(STAGES) - 1)
            texts = QVBoxLayout()
            texts.setContentsMargins(0, 1, 0, 3)
            texts.setSpacing(0)
            name_label = QLabel(name)
            name_label.setObjectName("stageName")
            detail = QLabel()
            detail.setObjectName("stageDetail")
            texts.addWidget(name_label)
            texts.addWidget(detail)
            stamp = QLabel()
            stamp.setObjectName("stageTime")
            row.addWidget(icon)
            row.addLayout(texts, stretch=1)
            row.addWidget(stamp, alignment=Qt.AlignmentFlag.AlignTop)
            layout.addLayout(row)
            self.rows[key] = (icon, name_label, detail, stamp)
        self.reset()

    def state(self, key: str) -> str:
        return self.rows[key][0].state

    def detail(self, key: str) -> str:
        return self.rows[key][2].toolTip()

    def time(self, key: str) -> str:
        return self.rows[key][3].text()

    def reset(self) -> None:
        self.active = None
        for key in self.rows:
            self._set(key, "pending", "", "")

    def activate(self, key: str, detail: str) -> None:
        """key is now running; the stage that was running before it has finished."""
        if self.active and self.active != key and self.state(self.active) == "active":
            self._set(self.active, "done")
        if key == "planning":                       # a new decide -> act -> verify cycle
            for later in ("validating", "executing", "verifying"):
                self._set(later, "pending", "", "")
        self.active = key
        self._set(key, "active", detail, datetime.now().strftime("%H:%M:%S"))

    def finish_stage(self, key: str, state: str, detail: str) -> None:
        """Mark key done/failed/stopped, e.g. with a verification verdict."""
        self._set(key, state, detail, datetime.now().strftime("%H:%M:%S"))
        if self.active == key:
            self.active = None

    def end_active(self, state: str, detail: str | None = None) -> None:
        """Close whatever stage is still running with the given state."""
        if self.active and self.state(self.active) == "active":
            self._set(self.active, state, detail)
        self.active = None

    def _set(self, key: str, state: str, detail: str | None = None,
             stamp: str | None = None) -> None:
        icon, name, detail_label, stamp_label = self.rows[key]
        icon.set_state(state)
        color = {"pending": TEXT_FAINT, "failed": ERROR}.get(state, TEXT)
        weight = 600 if state == "active" else 400
        name.setStyleSheet(f"color: {color}; font-weight: {weight};")
        if detail is not None:
            detail_label.setText(_clip(detail, 40))
            detail_label.setToolTip(detail)
            detail_label.setVisible(bool(detail))
        if stamp is not None:
            stamp_label.setText(stamp)


class ReportDialog(QDialog):
    """One run's completion report. Non-modal; every run gets its own dialog."""

    def __init__(self, report: dict, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.report = report
        self.text = task_report.report_text(report)
        started = datetime.fromtimestamp(report["started"]).strftime("%H:%M:%S")
        self.setWindowTitle(f"PIXEL Task Report — {started}")
        self.resize(580, 540)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 16)
        layout.setSpacing(12)

        header = QHBoxLayout()
        header.setSpacing(8)
        title = QLabel("Task Report")
        title.setObjectName("cardTitle")
        result = task_report.outcome(report)
        verification = task_report.verification_status(report)
        self.outcome_badge = self._badge(
            result, {"Completed": SUCCESS, "Failed": ERROR}.get(result, TEXT_MUTED))
        self.verification_badge = self._badge(
            verification, {"Verified": SUCCESS, "Failed": ERROR}.get(verification, TEXT_MUTED))
        header.addWidget(title)
        header.addStretch()
        header.addWidget(self.outcome_badge)
        header.addWidget(self.verification_badge)
        layout.addLayout(header)

        self.body = QTextEdit()
        self.body.setObjectName("reportText")
        self.body.setReadOnly(True)
        self.body.setPlainText(self.text)
        layout.addWidget(self.body, stretch=1)

        buttons = QHBoxLayout()
        buttons.addStretch()
        self.copy_button = _button("Copy Report", "secondaryButton")
        self.copy_button.setFixedHeight(36)
        self.copy_button.clicked.connect(self.copy)
        close = _button("Close", "primaryButton")
        close.setFixedHeight(36)
        close.clicked.connect(self.close)
        buttons.addWidget(self.copy_button)
        buttons.addWidget(close)
        layout.addLayout(buttons)

    @staticmethod
    def _badge(text: str, color: str) -> QLabel:
        badge = QLabel(text)
        badge.setObjectName("reportBadge")
        c = QColor(color)
        rgb = f"{c.red()},{c.green()},{c.blue()}"
        badge.setStyleSheet(f"color: {color}; background: rgba({rgb},22);"
                            f" border: 1px solid rgba({rgb},80);")
        return badge

    def copy(self) -> None:
        """Copy exactly the text shown in the dialog."""
        QApplication.clipboard().setText(self.text)
        self.copy_button.setText("Copied")


# -------------------------------------------------------------
# Main window
# -------------------------------------------------------------


def _card() -> QFrame:
    card = QFrame()
    card.setObjectName("card")
    return card


def _card_header(icon: str, title: str, subtitle: str = "") -> QHBoxLayout:
    """Blue icon + title (+ optional muted subtitle), as in every panel header."""
    row = QHBoxLayout()
    row.setSpacing(12)
    row.addWidget(glyph_label(icon, ACCENT_SOFT, 22), alignment=Qt.AlignmentFlag.AlignTop)
    texts = QVBoxLayout()
    texts.setSpacing(2)
    title_label = QLabel(title)
    title_label.setObjectName("cardTitle")
    texts.addWidget(title_label)
    if subtitle:
        sub = QLabel(subtitle)
        sub.setObjectName("cardSubtitle")
        texts.addWidget(sub)
    row.addLayout(texts)
    row.addStretch()
    return row


def _divider() -> QFrame:
    line = QFrame()
    line.setObjectName("divider")
    return line


def _button(text: str, name: str, icon: str | None = None,
            icon_color: str = TEXT, icon_size: int = 16) -> QPushButton:
    button = QPushButton(text)
    button.setObjectName(name)
    button.setCursor(Qt.CursorShape.PointingHandCursor)
    if icon:
        button.setIcon(glyph_icon(icon, icon_color, icon_size))
        button.setIconSize(QSize(icon_size, icon_size))
    return button


class ExampleCard(QPushButton):
    """Clickable example task: icon, two-line label, arrow."""

    def __init__(self, icon: str, label: str, task: str) -> None:
        super().__init__()
        self.setObjectName("exampleCard")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setMinimumHeight(56)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.task = task
        self.setToolTip(task)
        self.setAccessibleName(label.replace("\n", " "))

        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 6, 8, 6)
        layout.setSpacing(7)
        text = QLabel(label)
        text.setObjectName("exampleText")
        arrow = glyph_label("arrow", TEXT_MUTED, 12)
        icon_label = glyph_label(icon, ACCENT_SOFT, 22)
        for widget in (icon_label, text, arrow):
            widget.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        layout.addWidget(icon_label)
        layout.addWidget(text, stretch=1)
        layout.addWidget(arrow)


class PixelWindow(QMainWindow):
    """Main PIXEL user interface."""

    voice_requested = Signal()
    agent_requested = Signal(str)

    def __init__(self) -> None:
        super().__init__()

        self.setWindowTitle("PIXEL — Vision-Language Computer Agent")
        # Open at the reference size, but never bigger than the screen we're on.
        available = QApplication.primaryScreen().availableGeometry()
        self.resize(
            min(1640, available.width() - 40),
            min(920, available.height() - 40),
        )
        self.setMinimumWidth(1320)  # minimum height comes from the layout
        self.setStyleSheet(STYLESHEET)

        self._screenshot: QPixmap | None = None
        self._capture_ok: bool | None = None  # None until the first result
        self._agent_running = False
        self._voice_busy = False
        self._stop_requested = False
        self._close_after_task = False
        self._task_started = 0.0
        self._retry_note = ""
        self.reports: list[ReportDialog] = []
        self.acknowledgement = None     # shown once per launch, see show_acknowledgement()

        self._build_ui()

        self.set_status("Ready", "Waiting for your instruction...", "ready")
        self.log("System ready", "info")

        self.screen_preview_source = ScreenPreview(self)
        self.screen_preview_source.frame_ready.connect(self._on_frame)
        self.screen_preview_source.capture_failed.connect(self._on_capture_failed)
        self.screen_preview_source.start()

        self._start_voice_worker()
        self._start_agent_worker()

    # ---------------------------------------------------------
    # Layout
    # ---------------------------------------------------------

    def _build_ui(self) -> None:
        central = QWidget()
        central.setObjectName("root")
        self.setCentralWidget(central)

        main_layout = QHBoxLayout(central)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        main_layout.addWidget(self._build_sidebar())
        main_layout.addWidget(self._build_center(), stretch=1)
        main_layout.addWidget(self._build_rightbar())

        self.run_button.clicked.connect(self._run_task)
        self.voice_button.clicked.connect(self._start_voice_capture)
        self.stop_button.clicked.connect(self._stop_task)
        self.command_input.returnPressed.connect(self._run_task)
        # Esc = Stop, so the running task can be stopped from the keyboard too.
        self.stop_shortcut = QShortcut(QKeySequence(Qt.Key.Key_Escape), self)
        self.stop_shortcut.activated.connect(self._stop_task)

    def _build_sidebar(self) -> QFrame:
        bar = QFrame()
        bar.setObjectName("sidebar")
        bar.setFixedWidth(250)
        layout = QVBoxLayout(bar)
        layout.setContentsMargins(20, 34, 18, 20)
        layout.setSpacing(6)

        brand = QHBoxLayout()
        brand.setSpacing(14)
        brand.addWidget(LogoMark(), alignment=Qt.AlignmentFlag.AlignVCenter)
        names = QVBoxLayout()
        names.setSpacing(0)
        name = QLabel("PIXEL")
        name.setObjectName("brandName")
        subtitle = QLabel("Vision-Language\nComputer Agent")
        subtitle.setObjectName("brandSubtitle")
        names.addWidget(name)
        names.addWidget(subtitle)
        brand.addLayout(names)
        brand.addStretch()
        layout.addLayout(brand)
        layout.addSpacing(34)

        self.nav_buttons: dict[str, QPushButton] = {}
        for key, text, action in (
            ("home", "Home", self._nav_home),
            ("new_task", "New Task", self._nav_new_task),
            ("mic", "Voice Input", self._start_voice_capture),
            ("history", "Task History", self._nav_history),
            ("settings", "Settings", self._nav_settings),
        ):
            button = QPushButton(f"   {text}")
            button.setObjectName("navButton")
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.setMinimumHeight(52)
            button.setIconSize(QSize(20, 20))
            button.clicked.connect(action)
            self.nav_buttons[key] = button
            layout.addWidget(button)
        # Home is the only page; the other entries are actions.
        home = self.nav_buttons["home"]
        home.setCheckable(True)
        home.setChecked(True)
        self._refresh_nav_icons()
        layout.addStretch()

        footer = QFrame()
        footer.setObjectName("footerCard")
        footer_layout = QHBoxLayout(footer)
        footer_layout.setContentsMargins(14, 14, 12, 14)
        footer_layout.setSpacing(10)
        footer_layout.addWidget(glyph_label("robot", ACCENT_SOFT, 28))
        footer_text = QLabel("Your AI agent\nfor real desktop tasks.")
        footer_text.setObjectName("footerText")
        footer_layout.addWidget(footer_text, stretch=1)
        layout.addWidget(footer)
        return bar

    def _refresh_nav_icons(self) -> None:
        for key, button in self.nav_buttons.items():
            if not button.isEnabled():
                color = TEXT_FAINT
            elif button.isChecked():
                color = "#ffffff"
            else:
                color = TEXT_MUTED
            button.setIcon(glyph_icon(key, color, 20))

    def _build_center(self) -> QWidget:
        center = QWidget()
        center.setObjectName("center")
        layout = QVBoxLayout(center)
        layout.setContentsMargins(24, 30, 20, 12)
        layout.setSpacing(12)

        header = QHBoxLayout()
        header.setSpacing(10)
        title = QLabel(f'Tell <span style="color:{ACCENT_SOFT};">PIXEL</span> what to do')
        title.setObjectName("pageTitle")
        title.setTextFormat(Qt.TextFormat.RichText)
        subtitle = QLabel("Describe your task in natural language or use voice. "
                          "PIXEL will observe, plan, execute and verify.")
        subtitle.setObjectName("pageSubtitle")
        subtitle.setWordWrap(True)
        header.addWidget(title)
        header.addStretch()

        self.docs_button = _button("  Documentation", "secondaryButton", "book", TEXT_MUTED)
        self.help_button = _button("  Help", "secondaryButton", "help", TEXT_MUTED)
        for button in (self.docs_button, self.help_button):
            button.setFixedHeight(38)
            header.addWidget(button, alignment=Qt.AlignmentFlag.AlignTop)
        self.docs_button.clicked.connect(self._open_docs)
        self.help_button.clicked.connect(self._show_help)
        layout.addLayout(header)
        layout.addWidget(subtitle)
        layout.addSpacing(4)

        layout.addWidget(self._build_vision_card(), stretch=1)
        layout.addWidget(self._build_command_card())

        examples_label = QLabel("Try these examples")
        examples_label.setObjectName("examplesLabel")
        layout.addWidget(examples_label)
        self.examples_grid = QGridLayout()
        self.examples_grid.setSpacing(10)
        self.example_cards: list[ExampleCard] = []
        for icon, label, task in EXAMPLES:
            card = ExampleCard(icon, label, task)
            card.clicked.connect(lambda _=False, t=task: self._use_example(t))
            self.example_cards.append(card)
        self._example_columns = 0
        self.center = center
        center.installEventFilter(self)
        self._reflow_examples()
        layout.addLayout(self.examples_grid)
        return center

    def _reflow_examples(self) -> None:
        """One row of four cards when their text fits (leaving height for the preview),
        otherwise 2 x 2."""
        margins = self.center.layout().contentsMargins()
        available = self.center.width() - margins.left() - margins.right()
        count = len(self.example_cards)             # grid columns get equal widths
        needed = (count * max(card.layout().minimumSize().width() for card in self.example_cards)
                  + self.examples_grid.spacing() * (count - 1))
        columns = 4 if available >= needed else 2
        if columns == self._example_columns:
            return
        self._example_columns = columns
        for card in self.example_cards:
            self.examples_grid.removeWidget(card)
        for i, card in enumerate(self.example_cards):
            self.examples_grid.addWidget(card, i // columns, i % columns)

    def _build_vision_card(self) -> QFrame:
        card = _card()
        layout = QVBoxLayout(card)
        layout.setContentsMargins(16, 14, 16, 16)
        layout.setSpacing(12)

        top = _card_header("monitor", "Live Desktop View",
                           "PIXEL observes your screen to understand and complete tasks.")
        self.refresh_button = _button("  Refresh", "secondaryButton", "refresh", TEXT)
        self.refresh_button.setFixedHeight(40)
        self.refresh_button.setToolTip("Capture a new frame of the screen now")
        self.refresh_button.clicked.connect(self._refresh_preview)
        top.addWidget(self.refresh_button, alignment=Qt.AlignmentFlag.AlignTop)
        layout.addLayout(top)

        viewport = QFrame()
        viewport.setObjectName("viewport")
        viewport_layout = QVBoxLayout(viewport)
        viewport_layout.setContentsMargins(1, 1, 1, 1)

        # Page 0: empty state. Page 1: the real screenshot.
        self.screen_stack = QStackedWidget()
        self.screen_preview = QLabel()
        self.screen_preview.setObjectName("screenPreview")
        self.screen_preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.screen_preview.setSizePolicy(
            QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Ignored
        )
        self.empty_state = VisionEmptyState()
        self.screen_stack.addWidget(self.empty_state)
        self.screen_stack.addWidget(self.screen_preview)

        viewport_layout.addWidget(self.screen_stack)
        viewport.setMinimumHeight(230)
        layout.addWidget(viewport, stretch=1)
        return card

    def _build_command_card(self) -> QFrame:
        card = _card()
        layout = QVBoxLayout(card)
        layout.setContentsMargins(16, 10, 16, 12)
        layout.setSpacing(8)
        layout.addLayout(_card_header("edit", "Give PIXEL a Task"))

        composer = QFrame()
        composer.setObjectName("composer")
        composer_layout = QVBoxLayout(composer)
        composer_layout.setContentsMargins(12, 4, 12, 8)
        composer_layout.setSpacing(2)

        self.command_input = QLineEdit()
        self.command_input.setObjectName("commandInput")
        self.command_input.setPlaceholderText(
            "E.g. Open Notepad, create a file with my notes, and save it on the Desktop.")
        self.command_input.setMinimumHeight(36)
        composer_layout.addWidget(self.command_input)

        controls = QHBoxLayout()
        controls.setSpacing(10)

        self.provider_combo = QComboBox()
        self.provider_combo.setObjectName("providerCombo")
        self.provider_combo.setFixedHeight(38)
        self.provider_combo.setCursor(Qt.CursorShape.PointingHandCursor)
        self.provider_combo.setToolTip("Vision-language model used for the next task")
        for provider in PROVIDERS:
            self.provider_combo.addItem(f"✦  {provider.capitalize()}", provider)
        configured = os.environ.get("VLM_PROVIDER", "").strip().lower()
        index = self.provider_combo.findData(configured)
        if index >= 0:
            self.provider_combo.setCurrentIndex(index)

        self.voice_button = _button("", "iconButton", "mic", TEXT, 20)
        self.voice_button.setFixedSize(QSize(42, 38))
        self.voice_button.setAccessibleName("Speak a command")
        self.voice_button.setToolTip(f"Speak a command ({VOICE_DURATION} seconds)")

        self.run_button = _button("  Run Task", "primaryButton", "play", "#ffffff", 16)
        self.run_button.setMinimumSize(QSize(132, 38))
        self.run_button.setToolTip("Run the task (Enter)")

        self.stop_button = _button("  Stop", "stopButton", "stop", ERROR, 14)
        self.stop_button.setMinimumSize(QSize(100, 38))
        self.stop_button.setAccessibleName("Stop task")
        self.stop_button.setToolTip(
            "Stop the running task (Esc). No new actions are sent after Stop; "
            "a model request or input action already in progress finishes first.")
        self.stop_button.setEnabled(False)

        controls.addWidget(self.provider_combo)
        controls.addStretch()
        controls.addWidget(self.voice_button)
        controls.addWidget(self.run_button)
        controls.addWidget(self.stop_button)
        composer_layout.addLayout(controls)
        layout.addWidget(composer)
        return card

    def _build_rightbar(self) -> QFrame:
        bar = QFrame()
        bar.setObjectName("rightbar")
        bar.setFixedWidth(350)
        layout = QVBoxLayout(bar)
        layout.setContentsMargins(20, 30, 20, 20)
        layout.setSpacing(12)

        layout.addWidget(self._build_status_card())
        layout.addWidget(self._build_current_task_card())
        layout.addWidget(self._build_activity_card(), stretch=1)
        return bar

    def _build_status_card(self) -> QFrame:
        card = _card()
        layout = QVBoxLayout(card)
        layout.setContentsMargins(14, 16, 14, 14)
        layout.setSpacing(14)
        layout.addLayout(_card_header("pulse", "Task Status"))

        self.status_box = QFrame()
        self.status_box.setObjectName("statusBox")
        box_layout = QVBoxLayout(self.status_box)
        box_layout.setContentsMargins(12, 12, 14, 14)
        box_layout.setSpacing(4)

        state_row = QHBoxLayout()
        state_row.setSpacing(6)
        self.status_dot = StatusDot(SUCCESS, size=12)
        self.status_value = QLabel()
        self.status_value.setObjectName("statusState")
        state_row.addWidget(self.status_dot)
        state_row.addWidget(self.status_value)
        state_row.addStretch()

        self.status_detail = QLabel()
        self.status_detail.setObjectName("statusDetail")
        self.status_detail.setWordWrap(True)

        box_layout.addLayout(state_row)
        box_layout.addWidget(self.status_detail)
        layout.addWidget(self.status_box)
        return card

    def _build_current_task_card(self) -> QFrame:
        card = _card()
        layout = QVBoxLayout(card)
        layout.setContentsMargins(14, 16, 14, 14)
        layout.setSpacing(12)
        top = _card_header("page", "Current Task")
        self.report_button = _button("Last report", "smallButton")
        self.report_button.setFixedHeight(32)
        self.report_button.setToolTip("Show the completion report of the last task")
        self.report_button.setEnabled(False)
        self.report_button.clicked.connect(self._show_last_report)
        top.addWidget(self.report_button, alignment=Qt.AlignmentFlag.AlignVCenter)
        layout.addLayout(top)

        box = QFrame()
        box.setObjectName("innerBox")
        box_layout = QVBoxLayout(box)
        box_layout.setContentsMargins(16, 12, 16, 12)
        self.current_task = QLabel("No task running")
        self.current_task.setObjectName("currentTask")
        self.current_task.setWordWrap(True)
        box_layout.addWidget(self.current_task)
        layout.addWidget(box)

        self.timeline = ExecutionTimeline()
        layout.addWidget(self.timeline)
        layout.addWidget(_divider())

        grid = QGridLayout()
        grid.setContentsMargins(2, 0, 2, 0)
        grid.setHorizontalSpacing(8)
        self.task_step = QLabel("-")
        self.task_elapsed = QLabel("-")
        for col, (key, value) in enumerate((("Step", self.task_step),
                                            ("Elapsed", self.task_elapsed))):
            key_label = QLabel(key)
            key_label.setObjectName("metaKey")
            value.setObjectName("metaValue")
            grid.addWidget(key_label, 0, col * 2)
            grid.addWidget(value, 0, col * 2 + 1)
        grid.setColumnStretch(1, 1)
        layout.addLayout(grid)

        self._elapsed_timer = QTimer(self)
        self._elapsed_timer.setInterval(1000)
        self._elapsed_timer.timeout.connect(self._update_elapsed)
        return card

    def _build_activity_card(self) -> QFrame:
        card = _card()
        layout = QVBoxLayout(card)
        layout.setContentsMargins(14, 16, 10, 10)
        layout.setSpacing(10)

        top = _card_header("list", "Recent Activity")
        clear = _button("Clear", "smallButton")
        clear.setFixedHeight(32)
        clear.setToolTip("Clear the activity list")
        clear.clicked.connect(lambda: self.activity_log.clear())
        top.addWidget(clear, alignment=Qt.AlignmentFlag.AlignVCenter)
        layout.addLayout(top)

        self.activity_log = QTextEdit()
        self.activity_log.setObjectName("activityLog")
        self.activity_log.setReadOnly(True)
        self.activity_log.setFrameShape(QFrame.Shape.NoFrame)
        self.activity_log.document().setDocumentMargin(2)
        layout.addWidget(self.activity_log)
        return card

    # ---------------------------------------------------------
    # Display helpers
    # ---------------------------------------------------------

    def set_status(self, state: str, detail: str, tone: str = "info") -> None:
        """Update the Task Status panel, e.g. set_status("Running", "...", "working")."""
        color = QColor(TONES.get(tone, ACCENT_SOFT))
        self.status_value.setText(state)
        self.status_value.setStyleSheet(f"color: {color.name()};")
        self.status_detail.setText(detail)
        self.status_dot.set_color(color.name())
        self.status_dot.set_pulse(tone == "working")
        r, g, b = color.red(), color.green(), color.blue()
        self.status_box.setStyleSheet(
            f"QFrame#statusBox {{ background: rgba({r},{g},{b},20);"
            f" border: 1px solid rgba({r},{g},{b},70); border-radius: 8px; }}")

    def log(self, message: str, kind: str = "step") -> None:
        """Append one timeline entry to the activity log."""
        color = LOG_KINDS.get(kind, LOG_KINDS["step"])
        time_text = datetime.now().strftime("%H:%M:%S")
        self.activity_log.append(
            f'<table cellspacing="0" cellpadding="4"><tr>'
            f'<td width="18" style="color:{color}; font-size:9pt;">●</td>'
            f'<td width="70" style="color:{TEXT_MUTED};">{time_text}</td>'
            f'<td style="color:{TEXT};">{escape(message)}</td>'
            f"</tr></table>"
        )
        bar = self.activity_log.verticalScrollBar()
        bar.setValue(bar.maximum())

    def show_screenshot(self, pixmap: QPixmap) -> None:
        """Replace the empty state with a screenshot."""
        self._screenshot = pixmap
        if self.screen_stack.currentIndex() != 1:
            self.screen_stack.setCurrentIndex(1)
        self._fit_screenshot()

    def show_capture_unavailable(self) -> None:
        """Fall back to the empty state with a friendly message."""
        self._screenshot = None
        self.screen_preview.clear()
        self.empty_state.subtitle.setText("Screen capture unavailable — press Refresh to retry")
        self.screen_stack.setCurrentIndex(0)

    def _on_frame(self, image: QImage) -> None:
        if self._capture_ok is False:
            self.log("Screen capture restored", "success")
        elif self._capture_ok is None:
            self.log("Live screen preview started", "success")
        self._capture_ok = True
        self.show_screenshot(QPixmap.fromImage(image))

    def _on_capture_failed(self, error: str) -> None:
        # Log only when the state changes, not on every failed tick.
        if self._capture_ok is not False:
            self.log(f"Screen capture failed: {error}", "error")
            self.show_capture_unavailable()
        self._capture_ok = False

    def _refresh_preview(self) -> None:
        self.screen_preview_source.refresh()

    def _fit_screenshot(self) -> None:
        if self._screenshot is None:
            return
        # Scale in physical pixels so the image stays sharp on high-DPI screens.
        ratio = self.devicePixelRatioF()
        scaled = self._screenshot.scaled(
            self.screen_stack.size() * ratio,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        scaled.setDevicePixelRatio(ratio)
        self.screen_preview.setPixmap(scaled)

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._fit_screenshot()

    def eventFilter(self, watched, event) -> bool:  # noqa: N802
        if watched is self.center and event.type() == QEvent.Type.Resize:
            self._reflow_examples()
        return super().eventFilter(watched, event)

    def changeEvent(self, event) -> None:  # noqa: N802
        super().changeEvent(event)
        # No point capturing while nobody can see the preview.
        if hasattr(self, "screen_preview_source"):
            self.screen_preview_source.set_paused(self.isMinimized())

    def closeEvent(self, event) -> None:  # noqa: N802
        if self._agent_running:
            # Never kill the agent mid-action: ask it to stop (no new actions are
            # sent) and close once the worker reports finished.
            event.ignore()
            if not self._close_after_task:
                self._close_after_task = True
                self._stop_requested = True
                self.stop_button.setEnabled(False)
                self.agent_worker.request_stop()
                self.set_status("Stopping", "Finishing the current step, then closing...", "warning")
                self.log("Close requested: stopping after the current step", "warning")
            return
        if self._voice_busy:
            self.voice_worker.cancel()          # skip transcription so closing is quick
        self._elapsed_timer.stop()
        self.screen_preview_source.stop()
        # Lets an in-flight recording/transcription finish, then stops the thread.
        self.voice_thread.quit()
        self.voice_thread.wait()
        self.agent_thread.quit()
        self.agent_thread.wait()
        super().closeEvent(event)

    # ---------------------------------------------------------
    # Navigation, header buttons and examples
    # ---------------------------------------------------------

    def _nav_home(self) -> None:
        self.nav_buttons["home"].setChecked(True)   # Home is the only page
        self.command_input.setFocus()

    def _nav_new_task(self) -> None:
        if self.command_input.isReadOnly():
            return
        self.command_input.clear()
        self.command_input.setFocus()

    def _nav_history(self) -> None:
        config.RUNS_DIR.mkdir(exist_ok=True)
        self._open_path(config.RUNS_DIR, "task history folder")

    def _nav_settings(self) -> None:
        env_file = config.PROJECT_DIR / ".env"
        self._open_path(env_file if env_file.exists() else config.PROJECT_DIR, "settings")

    def _open_docs(self) -> None:
        self._open_path(config.PROJECT_DIR / "README.md", "documentation")

    def _open_path(self, path: Path, what: str) -> None:
        if not QDesktopServices.openUrl(QUrl.fromLocalFile(str(path))):
            self.log(f"Could not open {what}: {path}", "warning")

    def _show_help(self) -> None:
        QMessageBox.information(
            self,
            "PIXEL Help",
            "Type a task (or press the microphone to speak one), review it, then "
            "press Run Task or Enter.\n\n"
            "Stop or Esc: no new actions are sent; a step already in progress finishes "
            "first.\n"
            "Emergency stop: move the mouse into a screen corner.\n\n"
            "Task History opens the per-run logs and screenshots. Settings opens .env "
            "(VLM provider, model and API key).",
        )

    def show_acknowledgement(self) -> None:
        """Show the acknowledgement popup, once per application launch.

        open() is non-blocking: it is window-modal over PIXEL, but the event loop,
        live preview and worker threads keep running while it is visible.
        """
        if self.acknowledgement is not None:
            return
        from gui.acknowledgement import AcknowledgementDialog   # imports this module
        self.acknowledgement = AcknowledgementDialog(self)
        self.acknowledgement.open()

    def _use_example(self, task: str) -> None:
        """Example cards only fill the task box; the user reviews and runs it."""
        if self.command_input.isReadOnly():
            return
        self.command_input.setText(task)
        self.command_input.setFocus()

    def _update_elapsed(self) -> None:
        seconds = int(time.monotonic() - self._task_started)
        self.task_elapsed.setText(f"{seconds // 60}:{seconds % 60:02d}")

    # ---------------------------------------------------------
    # Agent: Run Task -> AgentWorker -> Agent.run()
    # ---------------------------------------------------------

    def _start_agent_worker(self) -> None:
        self.agent_thread = QThread(self)
        self.agent_worker = AgentWorker()
        self.agent_worker.moveToThread(self.agent_thread)
        self.agent_thread.finished.connect(self.agent_worker.deleteLater)

        # Queued across threads: run_task() runs on agent_thread, handlers on the GUI thread.
        self.agent_requested.connect(self.agent_worker.run_task)
        self.agent_worker.event.connect(self._on_agent_event)
        self.agent_worker.succeeded.connect(self._on_agent_succeeded)
        self.agent_worker.failed.connect(self._on_agent_failed)
        self.agent_worker.report.connect(self._on_agent_report)
        self.agent_worker.finished.connect(self._on_agent_finished)

        self.agent_thread.start()

    def _run_task(self) -> None:
        # Enter while voice input or another task is busy
        if self._agent_running or not self.run_button.isEnabled():
            return

        command = self.command_input.text().strip()

        if not command:
            self.set_status("Waiting", "Enter a command first.", "warning")
            self.log("No command entered", "warning")
            return

        self._voice_reset_timer.stop()
        self._agent_running = True
        self._stop_requested = False
        self._set_busy(True)
        self.set_status("Running", "Executing task...", "working")
        self.current_task.setText(command)
        self.current_task.setStyleSheet(f"color: {TEXT};")
        self.timeline.reset()
        self._retry_note = ""
        self.task_step.setText("-")
        self._task_started = time.monotonic()
        self.task_elapsed.setText("0:00")
        self._elapsed_timer.start()
        self.log(f"Task started: {command}", "step")
        self.agent_worker.set_provider(self.provider_combo.currentData())
        self.agent_worker.prepare_task()        # before queueing, so an early Stop is kept
        self.agent_requested.emit(command)

    def _stop_task(self) -> None:
        """Stop button / Esc: cooperative stop of the running task or voice capture.

        The agent sends no new action after this. A model request or input action
        that is already running cannot be interrupted safely; it finishes first and
        the status stays Stopping until the worker reports that it has ended.
        """
        if self._stop_requested or not self.stop_button.isEnabled():
            return
        if self._agent_running:
            self._stop_requested = True
            self.stop_button.setEnabled(False)
            self.agent_worker.request_stop()
            self.set_status("Stopping", "No new actions will be sent; finishing the current step...",
                            "warning")
            self.log("Stop requested: no new actions will be sent", "warning")
            self.timeline.finish_stage("outcome", "active", "Stop requested; finishing the current step")
        elif self._voice_busy:
            self._stop_requested = True
            self.stop_button.setEnabled(False)
            self.voice_worker.cancel()
            self.set_status("Stopping", "Discarding the voice input...", "warning")
            self.log("Voice input cancelled", "warning")

    def _on_agent_event(self, kind: str, message: str) -> None:
        """Agent progress -> compact status/activity. Busy-work events only touch the status."""
        if kind == "DECIDING":
            self.set_status("Running", message, "working")
            step = re.match(r"Step (\d+)", message)
            if step:
                self.task_step.setText(f"{step.group(1)} of max {config.MAX_STEPS}")
            detail = f"Step {step.group(1)}: asking the VLM" if step else message
            if self._retry_note:                    # this cycle follows a failed one
                detail += f" (after {self._retry_note})"
                self._retry_note = ""
            self.timeline.activate("planning", detail)
        elif kind == "OBSERVING":
            self.set_status("Running", "Observing screen...", "working")
            self.timeline.activate("observing", "Capturing the desktop")
        elif kind == "VALIDATING":
            self.timeline.activate("validating", f"Safety checks: {message}")
        elif kind == "VERIFYING":
            self.set_status("Verifying", _clip(message, 120), "working")
            self.timeline.activate("verifying", message)
        elif kind == "ACTION":
            self.set_status("Running", f"Executing: {_clip(message, 100)}", "working")
            self.timeline.activate("executing", message)
            self.log(f"Action: {_clip(message, 90)}", "step")
        elif kind == "VERIFICATION_PASS":
            self.timeline.finish_stage("verifying", "done", f"Passed: {message}")
            self.log("Verification: PASS", "success")
        elif kind == "VERIFICATION_FAIL":
            self.timeline.finish_stage("verifying", "failed", f"Not confirmed: {message}")
            self.log(f"Verification: FAIL - {_clip(message, 90)}", "warning")
        elif kind == "RETRYING":
            # The stage that was running when the step failed (e.g. Validating for a
            # rejected action, Planning for no usable decision) is marked failed.
            self.timeline.end_active("failed", message)
            self._retry_note = message
            self.log(f"Retrying: {message}", "warning")
        elif kind == "CONFIRMATION_NEEDED":
            self.timeline.finish_stage("verifying", "done", "Confirmation dialog detected")
            self.log("Confirmation dialog detected", "warning")
        # TASK_* end events are handled by the succeeded/failed signals.

    def _on_agent_succeeded(self, reason: str) -> None:
        self.set_status("Success", "Task verified complete.", "success")
        self.log("Task completed", "success")

    def _on_agent_failed(self, reason: str) -> None:
        if reason == "stopped by user":
            self.set_status("Stopped", "Stopped by user. No further actions were sent.", "warning")
            self.log("Task stopped by user", "warning")
            return
        self.set_status("Failed", short_reason(reason), "error")
        self.log(f"Task failed: {_clip(reason, 120)}", "error")

    def _on_agent_report(self, report: dict) -> None:
        """End of a run: close the timeline with the real outcome and show its report."""
        result = task_report.outcome(report)
        reason = report.get("reason") or ""
        if result == "Completed":
            self.timeline.end_active("done")
            self.timeline.finish_stage("outcome", "done", "Completed and verified")
        elif result == "Stopped":
            self.timeline.end_active("stopped", "Cancelled")
            self.timeline.finish_stage("outcome", "stopped", f"Stopped: {reason}")
        else:
            self.timeline.end_active("failed")
            self.timeline.finish_stage("outcome", "failed", short_reason(reason))

        dialog = ReportDialog(report, self)
        self.reports.append(dialog)     # one dialog per run; earlier reports are kept
        self.report_button.setEnabled(True)
        self.log(f"Task report ready: {result} ({task_report.verification_status(report)})",
                 "info")
        if not self._close_after_task:
            dialog.show()

    def _show_last_report(self) -> None:
        if self.reports:
            dialog = self.reports[-1]
            dialog.show()
            dialog.raise_()
            dialog.activateWindow()

    def _on_agent_finished(self) -> None:
        self._agent_running = False
        self._stop_requested = False
        self._elapsed_timer.stop()
        self._set_busy(False)
        # Keep the last task visible (muted) with its final progress values.
        self.current_task.setText(f"Last task: {self.current_task.text()}")
        self.current_task.setStyleSheet("")
        if self._close_after_task:
            self.close()
            return
        self.command_input.setFocus()

    # ---------------------------------------------------------
    # Voice input: Speak -> record -> Whisper -> command input
    # ---------------------------------------------------------

    def _start_voice_worker(self) -> None:
        self.voice_thread = QThread(self)
        self.voice_worker = VoiceWorker()
        self.voice_worker.moveToThread(self.voice_thread)
        self.voice_thread.finished.connect(self.voice_worker.deleteLater)

        # Cross-thread connections are queued: capture() runs on voice_thread,
        # the handlers below run on the GUI thread.
        self.voice_requested.connect(self.voice_worker.capture)
        self.voice_worker.transcribing.connect(self._on_voice_transcribing)
        self.voice_worker.transcribed.connect(self._on_voice_transcribed)
        self.voice_worker.recording_failed.connect(self._on_voice_recording_failed)
        self.voice_worker.transcription_failed.connect(self._on_voice_transcription_failed)
        self.voice_worker.cancelled.connect(self._on_voice_cancelled)
        self.voice_worker.finished.connect(self._on_voice_finished)

        # After an error, show ERROR briefly and then fall back to READY.
        self._voice_reset_timer = QTimer(self)
        self._voice_reset_timer.setSingleShot(True)
        self._voice_reset_timer.timeout.connect(
            lambda: self.set_status("Ready", "Waiting for your instruction...", "ready")
        )

        self.voice_thread.start()

    def _set_busy(self, busy: bool) -> None:
        """Lock Speak, Run Task, the provider and the command box while voice or the
        agent is busy; Stop is available exactly while something is running."""
        self.voice_button.setEnabled(not busy)
        self.run_button.setEnabled(not busy)
        self.stop_button.setEnabled(busy)
        self.command_input.setReadOnly(busy)
        self.provider_combo.setEnabled(not busy)
        self.nav_buttons["mic"].setEnabled(not busy)
        self.nav_buttons["new_task"].setEnabled(not busy)
        for card in self.example_cards:
            card.setEnabled(not busy)
        self._refresh_nav_icons()

    def _start_voice_capture(self) -> None:
        if not self.voice_button.isEnabled():
            return
        self._voice_reset_timer.stop()
        self._voice_busy = True
        self._stop_requested = False
        self.voice_worker.prepare()
        self._set_busy(True)
        self.set_status("Listening", f"Speak now... ({VOICE_DURATION} seconds)", "working")
        self.log("Listening for voice command...", "voice")
        self.voice_requested.emit()

    def _on_voice_transcribing(self) -> None:
        self.set_status("Transcribing", "Converting speech to text...", "working")
        self.log("Transcribing voice command...", "step")

    def _on_voice_transcribed(self, text: str) -> None:
        if not text:
            self.set_status("Ready", "No speech detected. Please try again.", "ready")
            self.log("No speech detected", "warning")
            return
        self.command_input.setText(text)
        self.set_status("Ready", "Voice command captured", "ready")
        self.log(f"Voice command: {text}", "voice")

    def _on_voice_recording_failed(self, error: str) -> None:
        self.set_status("Error", "Microphone recording failed.", "error")
        self.log(f"Voice recording failed: {error}", "error")
        self._voice_reset_timer.start(3000)

    def _on_voice_transcription_failed(self, error: str) -> None:
        self.set_status("Error", "Voice transcription failed.", "error")
        self.log(f"Voice transcription failed: {error}", "error")
        self._voice_reset_timer.start(3000)

    def _on_voice_cancelled(self) -> None:
        self.set_status("Stopped", "Voice input cancelled.", "warning")
        self._voice_reset_timer.start(3000)

    def _on_voice_finished(self) -> None:
        self._voice_busy = False
        self._stop_requested = False
        self._set_busy(False)
        self.command_input.setFocus()


def _clip(text: str, limit: int) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 3] + "..."


def main() -> None:
    app = QApplication(sys.argv)
    app.setFont(QFont("Segoe UI", 10))

    window = PixelWindow()
    window.show()
    window.show_acknowledgement()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
