"""Screen helpers: DPI awareness, screen size, screenshots, Desktop path.

Windows display scaling (125% on this laptop) makes non-DPI-aware programs see
a smaller "logical" screen (1536x864) than the real one (1920x1080). If the
screenshot and the mouse use different coordinate systems, every click misses.
Calling enable_dpi_awareness() once at startup makes both use physical pixels.
"""

import ctypes
import os
import winreg

_dpi_mode = None  # remembers which DPI call succeeded, for the --check report


def enable_dpi_awareness():
    """Make this process DPI-aware. Must run before any screenshot or mouse call."""
    global _dpi_mode
    if _dpi_mode is not None:
        return _dpi_mode
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)  # per-monitor aware
        _dpi_mode = "per-monitor"
    except (AttributeError, OSError):
        ctypes.windll.user32.SetProcessDPIAware()       # older fallback
        _dpi_mode = "system"
    return _dpi_mode


def get_scale_percent():
    """Windows display scaling of the primary monitor, e.g. 125."""
    dpi = ctypes.windll.user32.GetDpiForSystem()
    return round(dpi * 100 / 96)


def get_screen_size():
    """(width, height) of the primary screen in physical pixels.

    After enable_dpi_awareness() this matches the units pyautogui clicks in.
    """
    user32 = ctypes.windll.user32
    return user32.GetSystemMetrics(0), user32.GetSystemMetrics(1)


# --- Coordinate conversion ----------------------------------------------------
# The VLM may see a resized screenshot, or answer on a 0-1000 grid. These
# helpers turn its coordinates back into real screen pixels.

def scale_point(x, y, from_size, to_size):
    """Map (x, y) from an image of from_size to the same spot on to_size.

    Example: (400, 300) on a 960x540 image -> (800, 600) on a 1920x1080 screen.
    The result is rounded and clamped to stay inside to_size.
    """
    fw, fh = from_size
    tw, th = to_size
    sx = round(x * tw / fw)
    sy = round(y * th / fh)
    return min(max(sx, 0), tw - 1), min(max(sy, 0), th - 1)


def normalized_to_pixels(nx, ny, screen_size, scale=1000):
    """Convert coordinates on a 0..scale grid to screen pixels."""
    return scale_point(nx, ny, (scale, scale), screen_size)


def resize_for_model(image, max_width):
    """Shrink a screenshot (keeping aspect ratio) before sending it to a VLM."""
    if image.width <= max_width:
        return image
    height = round(image.height * max_width / image.width)
    return image.resize((max_width, height))


def take_screenshot():
    """Capture the primary screen as a PIL Image."""
    from PIL import ImageGrab
    return ImageGrab.grab()


# FOLDERID_Desktop from the Windows SDK (KnownFolders.h).
_FOLDERID_DESKTOP = "{B4BFCC3A-DB2C-424C-B029-7FE99A87C641}"


class _GUID(ctypes.Structure):
    _fields_ = [("Data1", ctypes.c_uint32), ("Data2", ctypes.c_uint16),
                ("Data3", ctypes.c_uint16), ("Data4", ctypes.c_ubyte * 8)]


def _known_folder_desktop():
    """Desktop path from the shell API SHGetKnownFolderPath (raises OSError on failure)."""
    from ctypes import wintypes
    guid = _GUID()
    ctypes.oledll.ole32.CLSIDFromString(_FOLDERID_DESKTOP, ctypes.byref(guid))
    buf = wintypes.LPWSTR()
    try:
        # oledll raises OSError if the HRESULT is a failure code.
        ctypes.oledll.shell32.SHGetKnownFolderPath(ctypes.byref(guid), 0, None, ctypes.byref(buf))
        return buf.value
    finally:
        ctypes.windll.ole32.CoTaskMemFree(buf)


def _registry_desktop():
    """Desktop path from the per-user shell folders registry key."""
    key_path = r"Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders"
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path) as key:
        value, _ = winreg.QueryValueEx(key, "Desktop")
    return os.path.expandvars(value)


def get_desktop_path():
    """Real Desktop folder (may be redirected to OneDrive).

    Asks the shell's known-folder API first, then the registry, and only
    falls back to %USERPROFILE%\\Desktop if both fail.
    """
    for lookup in (_known_folder_desktop, _registry_desktop):
        try:
            path = lookup()
        except OSError:
            continue
        if path:
            return path
    return os.path.join(os.path.expanduser("~"), "Desktop")
