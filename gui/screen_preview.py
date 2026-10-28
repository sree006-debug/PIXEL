"""Live desktop preview for the Vision Monitor panel.

A QTimer asks for a frame every INTERVAL_MS. The actual grab (agent.screen's
take_screenshot, ~35-45 ms at 1920x1080) and the PIL -> QImage conversion run
on one worker thread so the GUI never stutters. Results come back through Qt
signals, which Qt delivers on the GUI thread.

If a grab is still running when the timer fires, that tick is skipped, so
captures never pile up.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

from PySide6.QtCore import QObject, QTimer, Signal
from PySide6.QtGui import QImage

from agent.screen import take_screenshot

INTERVAL_MS = 400  # ~2.5 frames per second


def _pil_to_qimage(image) -> QImage:
    image = image.convert("RGB")
    w, h = image.size
    # .copy() so the QImage owns its pixels once the bytes object is freed.
    return QImage(image.tobytes(), w, h, 3 * w, QImage.Format.Format_RGB888).copy()


class ScreenPreview(QObject):
    """Periodically captures the screen; emits frame_ready or capture_failed."""

    frame_ready = Signal(QImage)
    capture_failed = Signal(str)

    def __init__(self, parent: QObject | None = None, interval_ms: int = INTERVAL_MS) -> None:
        super().__init__(parent)
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="screen-preview")
        self._busy = False
        self._paused = False
        self._timer = QTimer(self)
        self._timer.setInterval(interval_ms)
        self._timer.timeout.connect(self._request_frame)

    def start(self) -> None:
        self._timer.start()
        self._request_frame()  # first frame right away

    def stop(self) -> None:
        """Stop capturing and wait for any in-flight grab (call on window close)."""
        self._timer.stop()
        self._executor.shutdown(wait=True, cancel_futures=True)

    def set_paused(self, paused: bool) -> None:
        """Skip captures while paused (e.g. while the window is minimized)."""
        self._paused = paused

    def refresh(self) -> None:
        """Capture a frame now (Refresh button); skipped if a grab is already running."""
        self._request_frame()

    def _request_frame(self) -> None:
        if self._busy or self._paused:
            return
        self._busy = True
        try:
            self._executor.submit(self._capture)
        except RuntimeError:  # executor already shut down
            self._busy = False

    def _capture(self) -> None:
        """Runs on the worker thread."""
        try:
            frame = _pil_to_qimage(take_screenshot())
        except Exception as exc:  # any capture failure is reported, never raised
            self.capture_failed.emit(f"{type(exc).__name__}: {exc}")
        else:
            self.frame_ready.emit(frame)
        finally:
            self._busy = False
