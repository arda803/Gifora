import json
import math
import os
import queue
import re
import secrets
import shutil
import subprocess
import sys
import threading
import time
import traceback
import webbrowser
from urllib.parse import unquote, urlparse
import ctypes
import ctypes.util
from ctypes import wintypes
import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog, ttk
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, ImageTk

VENDOR_DIR = Path(__file__).resolve().parent / "vendor"
if VENDOR_DIR.is_dir() and str(VENDOR_DIR) not in sys.path:
    sys.path.insert(0, str(VENDOR_DIR))
try:
    from tkinterdnd2 import DND_FILES, TkinterDnD
except ImportError:
    DND_FILES = None
    TkinterDnD = None
from win_tray import WinTrayIcon

APP_NAME = "Gifora"
APP_VERSION = "1.4.0"
LINKEDIN_URL = "https://www.linkedin.com/in/arda-talha-tekinel-882176351/"
GITHUB_URL = "https://github.com/arda803"
BG = "#070b18"
PANEL = "#0e162b"
PANEL2 = "#162443"
TEXT = "#f4f6ff"
MUTED = "#9dadcf"
ACCENT = "#875bff"
ACCENT_HOVER = "#a57dff"
PANEL_HOVER = "#243760"
CYAN = "#55eaff"
BLUE = "#538cff"
KEY = "#ff00ff"
POSITIONS = ["Sol", "Sağ", "Sol üst", "Sol alt", "Sağ üst", "Sağ alt", "Özel"]
CONFIG_DIR = (Path(os.environ.get("APPDATA", Path.home())) if os.name == "nt" else
              Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))) / APP_NAME
CONFIG_FILE = CONFIG_DIR / "ayarlar.json"
PROFILES_FILE = CONFIG_DIR / "duzenler.json"


class WinRect(ctypes.Structure):
    _fields_ = [("left", wintypes.LONG), ("top", wintypes.LONG), ("right", wintypes.LONG), ("bottom", wintypes.LONG)]


class MonitorInfo(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", WinRect), ("rcWork", WinRect), ("dwFlags", wintypes.DWORD)]


class XRectangle(ctypes.Structure):
    _fields_ = [("x", ctypes.c_short), ("y", ctypes.c_short), ("width", ctypes.c_ushort), ("height", ctypes.c_ushort)]


def supported_drop_paths(paths):
    """Return only existing image files the animation decoder can open."""
    accepted = (".gif", ".webp", ".png")
    found, seen = [], set()
    for path in paths:
        try:
            raw = os.fspath(path).strip()
            if raw.casefold().startswith("file:"):
                parsed = urlparse(raw)
                raw = unquote(parsed.path)
                if os.name == "nt" and re.match(r"^/[A-Za-z]:", raw):
                    raw = raw[1:]
                if parsed.netloc and parsed.netloc.casefold() != "localhost":
                    raw = f"//{parsed.netloc}{raw}"
            candidate = Path(raw)
            key = str(candidate.resolve(strict=True)).casefold()
            if candidate.suffix.casefold() in accepted and candidate.is_file() and key not in seen:
                found.append(str(candidate))
                seen.add(key)
        except (OSError, RuntimeError, TypeError, ValueError):
            continue
    return found


def parse_drop_data(tk_app, raw_data):
    """Parse TkDND's Tcl-list payload without breaking paths that contain spaces."""
    return list(tk_app.tk.splitlist(raw_data))


def resize_rgba_frame(frame, size):
    if frame.size == size:
        return frame
    return frame.convert("RGBa").resize(size, Image.Resampling.LANCZOS).convert("RGBA")


def prepare_color_key_frame(frame):
    """Remove resampling-created partial alpha from binary-alpha GIF edges.

    Windows color-key windows blend antialiased pixels with the key color before
    removing the key, which can leave a colored fringe. GIF transparency is
    binary, so snapping only these generated edge pixels back to a binary mask
    preserves the original transparency without affecting APNG/WebP alpha.
    """
    frame = frame.convert("RGBA")
    frame.putalpha(frame.getchannel("A").point(lambda alpha: 255 if alpha >= 128 else 0))
    return frame


def redraw_windows_desktop_region(rect):
    """Force the desktop to repaint the area previously covered by a GIF window."""
    if os.name != "nt" or not rect:
        return
    try:
        x, y, width, height = rect
        bounds = WinRect(x - 3, y - 3, x + width + 3, y + height + 3)
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        user32.GetDesktopWindow.restype = ctypes.c_void_p
        user32.RedrawWindow.argtypes = [ctypes.c_void_p, ctypes.POINTER(WinRect), ctypes.c_void_p, wintypes.UINT]
        user32.RedrawWindow.restype = wintypes.BOOL
        user32.UpdateWindow.argtypes = [ctypes.c_void_p]
        desktop = user32.GetDesktopWindow()
        flags = 0x0001 | 0x0004 | 0x0080 | 0x0100 | 0x0020
        user32.RedrawWindow(desktop, ctypes.byref(bounds), None, flags)
        user32.UpdateWindow(desktop)
        try:
            ctypes.WinDLL("dwmapi").DwmFlush()
        except (AttributeError, OSError):
            pass
    except (AttributeError, OSError, TypeError, ValueError, ctypes.ArgumentError):
        pass


def create_root_window():
    if TkinterDnD:
        try:
            return TkinterDnD.Tk()
        except (tk.TclError, RuntimeError, OSError):
            pass
    return tk.Tk()


def is_wayland_session():
    return bool(os.environ.get("WAYLAND_DISPLAY") or os.environ.get("XDG_SESSION_TYPE", "").casefold() == "wayland")


def open_x11_shape_context():
    if not sys.platform.startswith("linux") or is_wayland_session() or not os.environ.get("DISPLAY"):
        return None
    x11_path, xext_path = ctypes.util.find_library("X11"), ctypes.util.find_library("Xext")
    if not x11_path or not xext_path:
        return None
    try:
        x11, xext = ctypes.CDLL(x11_path), ctypes.CDLL(xext_path)
        x11.XOpenDisplay.argtypes = [ctypes.c_char_p]
        x11.XOpenDisplay.restype = ctypes.c_void_p
        x11.XCloseDisplay.argtypes = [ctypes.c_void_p]
        x11.XFlush.argtypes = [ctypes.c_void_p]
        display = x11.XOpenDisplay(None)
        if not display:
            return None
        major, minor = ctypes.c_int(), ctypes.c_int()
        xext.XShapeQueryVersion.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_int), ctypes.POINTER(ctypes.c_int)]
        xext.XShapeQueryVersion.restype = ctypes.c_int
        if not xext.XShapeQueryVersion(display, ctypes.byref(major), ctypes.byref(minor)) or major.value < 1 or (major.value == 1 and minor.value < 1):
            x11.XCloseDisplay(display)
            return None
        xext.XShapeCombineRectangles.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.POINTER(XRectangle), ctypes.c_int, ctypes.c_int, ctypes.c_int]
        xext.XShapeCombineMask.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_ulong, ctypes.c_int]
        x11.XMoveWindow.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.c_int, ctypes.c_int]
        return {"display": display, "x11": x11, "xext": xext}
    except (AttributeError, OSError, TypeError):
        return None


def set_x11_clickthrough(context, window_id, enabled):
    if not context:
        return False
    display = context["display"]
    if enabled:
        # Empty ShapeInput region makes the overlay pass pointer events through.
        context["xext"].XShapeCombineRectangles(display, int(window_id), 2, 0, 0, None, 0, 0, 0)
    else:
        # Restore the default input region, which follows the visible shape.
        context["xext"].XShapeCombineMask(display, int(window_id), 2, 0, 0, 0, 0)
    context["x11"].XFlush(display)
    return True


def set_x11_window_shape(context, window_id, frame):
    if not context:
        return None
    alpha = frame.getchannel("A")
    raw = alpha.tobytes()
    width, height = frame.size
    active = {}
    rectangles = []
    for y in range(height):
        row = raw[y * width:(y + 1) * width]
        spans, start = [], None
        for x, value in enumerate(row):
            if value and start is None:
                start = x
            elif not value and start is not None:
                spans.append((start, x))
                start = None
        if start is not None:
            spans.append((start, width))
        current = set(spans)
        for span, start_y in list(active.items()):
            if span not in current:
                rectangles.append((span[0], start_y, span[1] - span[0], y - start_y))
                del active[span]
        for span in current:
            active.setdefault(span, y)
    for span, start_y in active.items():
        rectangles.append((span[0], start_y, span[1] - span[0], height - start_y))
    # Keep unusually detailed images responsive; if needed, use their alpha bounds.
    if len(rectangles) > 12000:
        bounds = alpha.getbbox()
        rectangles = [bounds] if bounds else []
        if bounds:
            left, top, right, bottom = bounds
            rectangles = [(left, top, right - left, bottom - top)]
    rect_array = (XRectangle * len(rectangles))(*(XRectangle(x, y, w, h) for x, y, w, h in rectangles)) if rectangles else None
    context["xext"].XShapeCombineRectangles(context["display"], int(window_id), 0, 0, 0, rect_array, len(rectangles), 0, 0)
    context["x11"].XFlush(context["display"])
    return raw


def get_monitors(fallback_width, fallback_height):
    monitors = []
    if sys.platform.startswith("linux") and not is_wayland_session() and shutil.which("xrandr"):
        try:
            output = subprocess.run(["xrandr", "--query"], capture_output=True, text=True, timeout=2, check=False).stdout
            pattern = re.compile(r"^(\S+)\s+connected(\s+primary)?\s+(\d+)x(\d+)([+-]\d+)([+-]\d+)")
            for line in output.splitlines():
                match = pattern.match(line)
                if match:
                    _, primary, width, height, x, y = match.groups()
                    x, y, width, height = int(x), int(y), int(width), int(height)
                    monitors.append({"x": x, "y": y, "right": x + width, "bottom": y + height, "primary": bool(primary)})
        except (OSError, subprocess.SubprocessError):
            pass
    elif os.name == "nt":
        try:
            user32 = ctypes.windll.user32
            callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, ctypes.c_void_p, ctypes.c_void_p, ctypes.POINTER(WinRect), wintypes.LPARAM)
            user32.GetMonitorInfoW.argtypes = [ctypes.c_void_p, ctypes.POINTER(MonitorInfo)]
            user32.GetMonitorInfoW.restype = wintypes.BOOL
            user32.EnumDisplayMonitors.argtypes = [ctypes.c_void_p, ctypes.c_void_p, callback_type, wintypes.LPARAM]
            user32.EnumDisplayMonitors.restype = wintypes.BOOL

            def collect(handle, _hdc, _rect, _data):
                info = MonitorInfo()
                info.cbSize = ctypes.sizeof(info)
                if user32.GetMonitorInfoW(handle, ctypes.byref(info)):
                    rect = info.rcWork
                    monitors.append({"x": rect.left, "y": rect.top, "right": rect.right, "bottom": rect.bottom, "primary": bool(info.dwFlags & 1)})
                return True
            callback = callback_type(collect)
            user32.EnumDisplayMonitors(None, None, callback, 0)
        except (AttributeError, OSError):
            pass
    monitors.sort(key=lambda monitor: not monitor["primary"])
    return monitors or [{"x": 0, "y": 0, "right": fallback_width, "bottom": fallback_height, "primary": True}]


def foreground_is_fullscreen():
    if sys.platform.startswith("linux"):
        if is_wayland_session() or not os.environ.get("DISPLAY") or not shutil.which("xprop") or not shutil.which("xwininfo"):
            return False
        try:
            active = subprocess.run(["xprop", "-root", "_NET_ACTIVE_WINDOW"], capture_output=True, text=True, timeout=1, check=False).stdout
            match = re.search(r"window id # (0x[0-9a-fA-F]+)", active)
            if not match:
                return False
            window_id = match.group(1)
            owner = subprocess.run(["xprop", "-id", window_id, "_NET_WM_PID"], capture_output=True, text=True, timeout=1, check=False).stdout
            owner_match = re.search(r"=\s*(\d+)", owner)
            if owner_match and int(owner_match.group(1)) == os.getpid():
                return False
            info = subprocess.run(["xwininfo", "-id", window_id], capture_output=True, text=True, timeout=1, check=False).stdout
            values = {key: int(value) for key, value in re.findall(r"(Absolute upper-left X|Absolute upper-left Y|Width|Height):\s*(-?\d+)", info)}
            monitors = get_monitors(0, 0)
            for monitor in monitors:
                if (values.get("Absolute upper-left X", 999999) <= monitor["x"] + 2 and
                    values.get("Absolute upper-left Y", 999999) <= monitor["y"] + 2 and
                    values.get("Width", 0) >= monitor["right"] - monitor["x"] - 2 and
                    values.get("Height", 0) >= monitor["bottom"] - monitor["y"] - 2):
                    return True
        except (OSError, subprocess.SubprocessError, ValueError):
            return False
        return False
    try:
        user32 = ctypes.windll.user32
        user32.GetForegroundWindow.restype = ctypes.c_void_p
        user32.GetWindowThreadProcessId.argtypes = [ctypes.c_void_p, ctypes.POINTER(wintypes.DWORD)]
        user32.GetWindowRect.argtypes = [ctypes.c_void_p, ctypes.POINTER(WinRect)]
        user32.MonitorFromWindow.argtypes = [ctypes.c_void_p, wintypes.DWORD]
        user32.MonitorFromWindow.restype = ctypes.c_void_p
        user32.GetMonitorInfoW.argtypes = [ctypes.c_void_p, ctypes.POINTER(MonitorInfo)]
        hwnd = user32.GetForegroundWindow()
        if not hwnd:
            return False
        process_id = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(process_id))
        if process_id.value == os.getpid():
            return False
        rect = WinRect()
        if not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
            return False
        monitor = user32.MonitorFromWindow(hwnd, 2)
        info = MonitorInfo()
        info.cbSize = ctypes.sizeof(info)
        if not user32.GetMonitorInfoW(monitor, ctypes.byref(info)):
            return False
        bounds = info.rcMonitor
        return (rect.left <= bounds.left + 2 and rect.top <= bounds.top + 2 and
                rect.right >= bounds.right - 2 and rect.bottom >= bounds.bottom - 2)
    except (AttributeError, OSError):
        return False


def asset_path(name):
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).parent))
    return base / name


class GifOverlay:
    def __init__(self, app, item_id, path, position, width, opacity=100, clickthrough=False, monitor=0, locked=False):
        self.app = app
        self.item_id = item_id
        self.path = path
        self.position = position
        self.width = width
        self.visible = True
        self.playing = True
        self.opacity = max(20, min(100, int(opacity)))
        self.clickthrough = bool(clickthrough)
        self.monitor = min(max(0, int(monitor)), len(app.monitors) - 1)
        self.locked = bool(locked)
        self.frame_queue = queue.Queue(maxsize=2)
        self.load_stop = None
        self.decode_threads = []
        self.load_generation = 0
        self.image_ref = None
        self.current_delay = 80
        self.next_frame_at = 0.0
        self.after_id = None
        self.drag_start = None
        self.manual_xy = None
        self.transparent_key = f"#{secrets.token_hex(3)}"
        self.x11_context = open_x11_shape_context() if sys.platform.startswith("linux") else None
        self.last_shape_alpha = None
        self.win = tk.Toplevel(app.root)
        # Wait for the first frame so the window manager sees final dimensions
        # and the restored position before this overlay is shown.
        self.win.withdraw()
        self.win.overrideredirect(True)
        self.win.attributes("-topmost", True)
        self.win.configure(bg=self.transparent_key)
        if os.name == "nt":
            try:
                self.win.wm_attributes("-transparentcolor", self.transparent_key)
            except tk.TclError:
                pass
        self.label = tk.Label(self.win, bg=self.transparent_key, bd=0, highlightthickness=0, cursor="fleur")
        self.label.pack()
        self.label.bind("<ButtonPress-1>", self._start_drag)
        self.label.bind("<B1-Motion>", self._drag)
        self.label.bind("<ButtonRelease-1>", self._end_drag)
        self.label.bind("<Button-3>", lambda _e: self.app.select_item(self.item_id))
        self.load(path, width)
        self.reposition()
        self.set_opacity(self.opacity)
        self.set_clickthrough(self.clickthrough)

    def load(self, path, width):
        if self.after_id:
            self.win.after_cancel(self.after_id)
            self.after_id = None
        if self.load_stop:
            self.load_stop.set()
        self.load_generation += 1
        generation = self.load_generation
        stop = threading.Event()
        self.load_stop = stop
        self.frame_queue = queue.Queue(maxsize=2)
        self.path = path
        self.width = max(48, int(width))
        self._loading = True
        self.decode_threads = [thread for thread in self.decode_threads if thread.is_alive()]
        decoder = threading.Thread(
            target=GifOverlay._decode_frames,
            args=(path, self.width, dict(self.app.monitors[self.monitor]), generation, stop, self.frame_queue),
            daemon=True,
        )
        self.decode_threads.append(decoder)
        decoder.start()
        self._poll_frames(generation)

    @staticmethod
    def _decode_frames(path, width, bounds, generation, stop, frames):
        try:
            with Image.open(path) as gif:
                source_w, source_h = gif.size
                max_w = max(48, bounds["right"] - bounds["x"] - 24)
                max_h = max(48, bounds["bottom"] - bounds["y"] - 48)
                scale = min(width / source_w, max_w / source_w, max_h / source_h)
                size = (max(1, int(source_w * scale)), max(1, int(source_h * scale)))
                frame_count = max(1, getattr(gif, "n_frames", 1))
                frame_no = 0
                while not stop.is_set():
                    gif.seek(frame_no)
                    frame = gif.convert("RGBA")
                    # Premultiplied-alpha resizing avoids colored fringes around
                    # transparent pixels on Windows color-key windows.
                    frame = resize_rgba_frame(frame, size)
                    if os.name == "nt" and gif.format == "GIF":
                        frame = prepare_color_key_frame(frame)
                    delay = max(20, int(gif.info.get("duration", 80)))
                    while not stop.is_set():
                        try:
                            frames.put((generation, frame, delay), timeout=0.1)
                            break
                        except queue.Full:
                            continue
                    frame_no = (frame_no + 1) % frame_count
        except Exception as exc:
            if not stop.is_set():
                try:
                    frames.put((generation, None, str(exc)), timeout=0.2)
                except queue.Full:
                    pass

    def _poll_frames(self, generation):
        if generation != self.load_generation or not self.win.winfo_exists():
            return
        try:
            frame_generation, frame, delay = self.frame_queue.get_nowait()
        except queue.Empty:
            self.after_id = self.win.after(10, lambda: self._poll_frames(generation))
            return
        if frame_generation != generation:
            self.after_id = self.win.after(1, lambda: self._poll_frames(generation))
            return
        if frame is None:
            self._loading = False
            self.app.on_overlay_error(self.item_id, delay)
            return
        self._loading = False
        self._show_frame(frame, delay)
        self.app.on_overlay_ready(self.item_id)
        if self.playing:
            self._schedule_advance(generation, delay)

    def _schedule_advance(self, generation, delay):
        """Schedule frame deadlines with a monotonic clock to reduce playback drift."""
        self.next_frame_at = time.monotonic() + delay / 1000
        self.after_id = self.win.after(max(1, delay), lambda: self._advance(generation))

    def _show_frame(self, frame, delay):
        if self.image_ref is None or (self.image_ref.width(), self.image_ref.height()) != frame.size:
            self.image_ref = ImageTk.PhotoImage(frame, master=self.win)
            self.label.configure(image=self.image_ref)
            self.win.geometry(f"{self.image_ref.width()}x{self.image_ref.height()}")
            self.reposition()
        else:
            self.image_ref.paste(frame)
        if self.x11_context and self.win.winfo_exists():
            try:
                if self.last_shape_alpha != frame.getchannel("A").tobytes():
                    self.last_shape_alpha = set_x11_window_shape(self.x11_context, self.win.winfo_id(), frame)
            except (tk.TclError, OSError):
                pass
        self.current_delay = delay

    def _advance(self, generation):
        if generation != self.load_generation or not self.win.winfo_exists() or not self.playing:
            return
        try:
            frame_generation, frame, delay = self.frame_queue.get_nowait()
        except queue.Empty:
            remaining = self.next_frame_at - time.monotonic()
            self.after_id = self.win.after(max(2, min(8, int(remaining * 1000))), lambda: self._advance(generation))
            return
        if frame_generation != generation:
            self.after_id = self.win.after(1, lambda: self._advance(generation))
            return
        if frame is None:
            self.app.on_overlay_error(self.item_id, delay)
            return
        self._show_frame(frame, delay)
        self._schedule_advance(generation, delay)

    def _start_drag(self, event):
        if self.locked or self.clickthrough:
            return
        self.drag_start = (event.x_root, event.y_root, self.win.winfo_x(), self.win.winfo_y())

    def _drag(self, event):
        if not self.drag_start:
            return
        sx, sy, wx, wy = self.drag_start
        nx, ny = wx + event.x_root - sx, wy + event.y_root - sy
        self.app.move_overlay(self, nx, ny)

    def _end_drag(self, _event):
        if self.drag_start:
            self.monitor, x, y = self.app.snap_overlay(self, self.win.winfo_x(), self.win.winfo_y())
            self.manual_xy = (x, y)
            self.position = "Özel"
            self.app.move_overlay(self, x, y)
            self.drag_start = None
            self.app.update_position_label(self.item_id)
            self.app.save_config()

    def reposition(self):
        if not self.win.winfo_exists():
            return
        if self.manual_xy:
            x, y = self.manual_xy
        else:
            w = self.win.winfo_reqwidth()
            h = self.win.winfo_reqheight()
            margin = 24
            bounds = self.app.monitors[self.monitor]
            left, top = bounds["x"], bounds["y"]
            sw, sh = bounds["right"] - left, bounds["bottom"] - top
            positions = {
                "Sol": (left + margin, top + max(margin, (sh - h) // 2)),
                "Sağ": (left + max(margin, sw - w - margin), top + max(margin, (sh - h) // 2)),
                "Sol üst": (left + margin, top + margin),
                "Sol alt": (left + margin, top + max(margin, sh - h - margin)),
                "Sağ üst": (left + max(margin, sw - w - margin), top + margin),
                "Sağ alt": (left + max(margin, sw - w - margin), top + max(margin, sh - h - margin)),
                "Özel": (left + margin, top + max(margin, (sh - h) // 2)),
            }
            x, y = positions.get(self.position, positions["Sol"])
        self.app.move_overlay(self, x, y)

    def set_position(self, position):
        self.position = position
        self.manual_xy = None
        self.reposition()

    def set_monitor(self, monitor):
        self.monitor = min(max(0, int(monitor)), len(self.app.monitors) - 1)
        self.manual_xy = None
        self.load(self.path, self.width)
        self.reposition()

    def set_locked(self, locked):
        self.locked = bool(locked)

    def set_width(self, width):
        self.load(self.path, width)
        self.reposition()

    def set_playing(self, playing):
        self.playing = playing
        if self.after_id:
            self.win.after_cancel(self.after_id)
            self.after_id = None
        if self.image_ref is None:
            # Even paused GIFs need their first frame decoded so the saved
            # visible state can be restored on screen.
            self._poll_frames(self.load_generation)
        elif playing and not self._loading:
            self._schedule_advance(self.load_generation, self.current_delay)

    def set_visible(self, visible):
        self.visible = visible
        if visible:
            if self.image_ref is not None:
                self.reposition()
                self.win.deiconify()
                self.win.attributes("-topmost", True)
        else:
            self.win.withdraw()

    def set_opacity(self, opacity):
        self.opacity = max(20, min(100, int(opacity)))
        try:
            self.win.attributes("-alpha", self.opacity / 100)
        except tk.TclError:
            pass

    def set_clickthrough(self, enabled):
        enabled = bool(enabled)
        if sys.platform.startswith("linux"):
            if not self.x11_context:
                self.clickthrough = False
                return not enabled
            try:
                result = set_x11_clickthrough(self.x11_context, self.win.winfo_id(), enabled)
            except (tk.TclError, OSError):
                result = False
            self.clickthrough = enabled if result else False
            return result
        if os.name != "nt":
            self.clickthrough = False
            return not enabled
        try:
            user32 = ctypes.WinDLL("user32", use_last_error=True)
            get_style = getattr(user32, "GetWindowLongPtrW")
            set_style = getattr(user32, "SetWindowLongPtrW")
            get_style.argtypes = [ctypes.c_void_p, ctypes.c_int]
            get_style.restype = ctypes.c_ssize_t
            set_style.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_ssize_t]
            set_style.restype = ctypes.c_ssize_t
            user32.GetAncestor.argtypes = [ctypes.c_void_p, wintypes.UINT]
            user32.GetAncestor.restype = ctypes.c_void_p
            user32.SetWindowPos.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.UINT]
            user32.SetWindowPos.restype = wintypes.BOOL

            # Tk can return the client HWND; update the actual overlay window.
            hwnd = user32.GetAncestor(self.win.winfo_id(), 2) or self.win.winfo_id()
            ex_style = get_style(hwnd, -20)
            transparent = 0x00000020
            layered = 0x00080000
            if enabled:
                ex_style |= transparent | layered
            else:
                ex_style &= ~transparent
                # Keep WS_EX_LAYERED: Tk uses it for opacity and color-key transparency.
            ctypes.set_last_error(0)
            set_style(hwnd, -20, ex_style)
            if ctypes.get_last_error():
                raise ctypes.WinError(ctypes.get_last_error())
            flags = 0x0001 | 0x0002 | 0x0004 | 0x0010 | 0x0020
            if not user32.SetWindowPos(hwnd, None, 0, 0, 0, 0, flags):
                raise ctypes.WinError(ctypes.get_last_error())
        except (AttributeError, OSError, ValueError, TypeError, ctypes.ArgumentError, tk.TclError):
            self.clickthrough = False
            return False
        self.clickthrough = enabled
        return True

    def close(self):
        if self.load_stop:
            self.load_stop.set()
        if self.after_id:
            try:
                self.win.after_cancel(self.after_id)
            except tk.TclError:
                pass
        self.win.destroy()
        for decoder in self.decode_threads:
            decoder.join(timeout=0.35)
        redraw_windows_desktop_region(getattr(self, "last_screen_rect", None))
        if self.x11_context:
            try:
                self.x11_context["x11"].XCloseDisplay(self.x11_context["display"])
            except (AttributeError, OSError):
                pass
            self.x11_context = None


class GiforaApp:
    def __init__(self, root):
        self.root = root
        self.root.title(f"Gifora {APP_VERSION} | Masaüstü GIF")
        self.root.geometry("610x980")
        self.root.minsize(540, 740)
        self.root.configure(bg=BG)
        self.root.resizable(True, True)
        self.screen_w, self.screen_h = root.winfo_screenwidth(), root.winfo_screenheight()
        self.monitors = get_monitors(self.screen_w, self.screen_h)
        self.monitor_names = [f"Ekran {index + 1} · {m['right'] - m['x']}×{m['bottom'] - m['y']}" for index, m in enumerate(self.monitors)]
        self.items = []
        self.overlays = {}
        self.selected_id = None
        self.width_after = None
        self.opacity_after = None
        self._updating_controls = False
        self.performance_after = None
        self.profiles = {}
        self.performance_paused = False
        self.file_drop_enabled = bool(TkinterDnD and isinstance(root, TkinterDnD.Tk))
        self.tray_icon = None
        self._tray_hidden = False
        self._button_animations = {}
        self._style()
        self._build_ui()
        if os.name == "nt":
            try:
                self.tray_icon = WinTrayIcon(root, asset_path("gifora.ico"), APP_VERSION, self.show_main_window, self.quit)
            except (AttributeError, OSError, RuntimeError, tk.TclError) as exc:
                self._log_tray_error(exc)
        if self.file_drop_enabled:
            self.root.after_idle(self._register_file_drop)
        else:
            self.drop_hint.config(text="Dosya bırakma kullanılamıyor; Görsel ekle düğmesini kullan.")
        self._load_config()
        self._load_profiles()
        self.performance_after = self.root.after(1200, self._poll_performance)
        if self.tray_icon:
            self.root.protocol("WM_DELETE_WINDOW", self.hide_to_tray)
            self.root.bind("<Unmap>", self._on_root_unmap, add="+")
        elif os.name == "nt":
            self.root.protocol("WM_DELETE_WINDOW", self.quit)
        else:
            self.root.protocol("WM_DELETE_WINDOW", self.minimize_window)
        self.root.bind("<Escape>", lambda _e: self.quit())

    def _style(self):
        style = ttk.Style()
        style.theme_use("clam")
        style.configure("Treeview", background="#0b1429", fieldbackground="#0b1429", foreground=TEXT, rowheight=41, borderwidth=0, font=("Segoe UI", 9))
        style.configure("Treeview.Heading", background="#18284a", foreground="#b8c8eb", relief="flat", font=("Segoe UI", 8, "bold"), padding=(7, 10))
        style.map("Treeview", background=[("selected", "#373276")], foreground=[("selected", TEXT)])
        style.configure("TCombobox", fieldbackground=PANEL2, background=PANEL2, foreground=TEXT, arrowcolor=TEXT, padding=9)
        style.map("TCombobox", fieldbackground=[("readonly", PANEL2)], foreground=[("readonly", TEXT)])
        style.configure("TEntry", fieldbackground="#101d38", foreground=TEXT, insertcolor=CYAN, bordercolor="#2b4577", padding=9)
        style.configure("Horizontal.TScale", background=PANEL, troughcolor="#243960")
        style.configure("Vertical.TScrollbar", background="#1b2d51", troughcolor=BG, borderwidth=0, arrowcolor=CYAN)

    def _build_ui(self):
        header = tk.Frame(self.root, bg=BG)
        header.pack(fill="x", padx=18, pady=(16, 10))
        self.hero_canvas = tk.Canvas(header, height=166, bg="#0b1430", highlightthickness=1, highlightbackground="#334f91", bd=0)
        self.hero_canvas.pack(fill="x")
        self.hero_canvas.bind("<Configure>", self._draw_hero)
        links = tk.Frame(header, bg=BG)
        links.pack(fill="x", pady=(8, 0))
        tk.Label(links, text="✦  ANIMATED DESKTOP COMPANIONS", fg=CYAN, bg=BG, font=("Segoe UI", 8, "bold")).pack(side="left")
        for label, url in (("LinkedIn", LINKEDIN_URL), ("GitHub", GITHUB_URL)):
            link = tk.Label(links, text=label, fg="#b7c8ff", bg=BG, font=("Segoe UI", 8, "underline"), cursor="hand2")
            link.pack(side="right", padx=(10, 0))
            link.bind("<Button-1>", lambda _event, target=url: webbrowser.open_new_tab(target))

        stats = tk.Frame(header, bg=BG)
        stats.pack(fill="x", pady=(10, 0))
        self.total_stat = self._metric_card(stats, "KOLEKSİYON")
        self.visible_stat = self._metric_card(stats, "GÖRÜNÜR")
        self.playing_stat = self._metric_card(stats, "OYNATIYOR")

        content = tk.Frame(self.root, bg=BG)
        content.pack(fill="both", expand=True, padx=18, pady=(0, 12))
        self.panel_canvas = tk.Canvas(content, bg=BG, highlightthickness=0, bd=0)
        scrollbar = ttk.Scrollbar(content, orient="vertical", command=self.panel_canvas.yview)
        self.panel_canvas.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side="right", fill="y")
        self.panel_canvas.pack(side="left", fill="both", expand=True)
        card = tk.Frame(self.panel_canvas, bg=PANEL, padx=20, pady=20, highlightthickness=1, highlightbackground="#243861")
        card_window = self.panel_canvas.create_window((0, 0), window=card, anchor="nw")
        card.bind("<Configure>", lambda _event: self.panel_canvas.configure(scrollregion=self.panel_canvas.bbox("all")))
        self.panel_canvas.bind("<Configure>", lambda event: self.panel_canvas.itemconfigure(card_window, width=event.width))
        self.panel_canvas.bind_all("<MouseWheel>", self._scroll_panel)
        self._section_label(card, "01  /  KÜTÜPHANE", "GIF koleksiyonun")
        tk.Label(card, text="Animasyonlarını ekle, ara ve masaüstünde tek tek düzenle.", fg=MUTED, bg=PANEL, font=("Segoe UI", 9)).pack(anchor="w", pady=(3, 13))
        search_row = tk.Frame(card, bg=PANEL)
        search_row.pack(fill="x", pady=(0, 4))
        tk.Label(search_row, text="Ara", fg=MUTED, bg=PANEL, font=("Segoe UI", 9)).pack(side="left", padx=(0, 8))
        self.search_var = tk.StringVar()
        self.search_entry = ttk.Entry(search_row, textvariable=self.search_var)
        self.search_entry.pack(side="left", fill="x", expand=True)
        self.search_var.trace_add("write", lambda *_args: self._render_items())
        self.drop_hint = tk.Label(card, text="✧   DOSYALARINI BURAYA SÜRÜKLE VE BIRAK   ✧\nGIF  ·  WEBP  ·  APNG", fg="#d5f8ff", bg="#111e3b", font=("Segoe UI", 9, "bold"), anchor="center", padx=10, pady=15, cursor="plus", relief="flat", highlightthickness=1, highlightbackground="#3863a7")
        self.drop_hint.pack(fill="x", pady=(7, 12))
        self.collection_summary = tk.Label(card, text="Liste durumu", fg=MUTED, bg=PANEL, font=("Segoe UI", 8), anchor="w")
        self.collection_summary.pack(fill="x", pady=(0, 5))
        tree_list_frame = tk.Frame(card, bg=PANEL)
        tree_list_frame.pack(fill="x")
        self.tree = ttk.Treeview(tree_list_frame, columns=("name", "position", "size", "status", "visible"), show="headings", selectmode="browse", height=6)
        tree_scrollbar = ttk.Scrollbar(tree_list_frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=tree_scrollbar.set)
        self.tree.heading("name", text="DOSYA")
        self.tree.heading("position", text="KONUM")
        self.tree.heading("size", text="GENİŞLİK")
        self.tree.heading("status", text="DURUM")
        self.tree.heading("visible", text="GÖSTER")
        self.tree.column("name", width=128, minwidth=98, anchor="w")
        self.tree.column("position", width=58, minwidth=54, anchor="center")
        self.tree.column("size", width=53, minwidth=48, anchor="center")
        self.tree.column("status", width=72, minwidth=66, anchor="center")
        self.tree.column("visible", width=54, minwidth=50, anchor="center")
        self.tree.pack(side="left", fill="both", expand=True)
        tree_scrollbar.pack(side="right", fill="y")
        self.tree.bind("<<TreeviewSelect>>", self._on_select)
        self.tree.bind("<Button-1>", self._on_tree_click, add="+")
        for wheel_event in ("<MouseWheel>", "<Button-4>", "<Button-5>"):
            self.tree.bind(wheel_event, self._scroll_tree, add="+")

        row = tk.Frame(card, bg=PANEL)
        row.pack(fill="x", pady=(11, 15))
        self._button(row, "+  Görsel ekle", self.add_gif, primary=True).pack(side="left", fill="x", expand=True, padx=(0, 5))
        self._button(row, "Kaldır", self.remove_gif).pack(side="left", padx=(5, 0))
        self._section_label(card, "TOPLU İŞLEMLER", "Tüm GIF’ler")
        tk.Label(card, text="Görünürlük ve oynatma", fg=MUTED, bg=PANEL, font=("Segoe UI", 8)).pack(anchor="w", pady=(2, 6))
        bulk_visibility = tk.Frame(card, bg=PANEL)
        bulk_visibility.pack(fill="x", pady=(0, 10))
        self._button(bulk_visibility, "▶  Hepsini aç", lambda: self.set_all_visible(True)).pack(side="left", fill="x", expand=True, padx=(0, 4))
        self._button(bulk_visibility, "■  Hepsini kapat", lambda: self.set_all_visible(False)).pack(side="left", fill="x", expand=True, padx=(4, 0))
        bulk_playback = tk.Frame(card, bg=PANEL)
        bulk_playback.pack(fill="x", pady=(0, 12))
        self._button(bulk_playback, "Tümünü oynat", lambda: self.set_all_playing(True)).pack(side="left", fill="x", expand=True, padx=(0, 4))
        self._button(bulk_playback, "Tümünü duraklat", lambda: self.set_all_playing(False)).pack(side="left", fill="x", expand=True, padx=(4, 0))
        self._divider(card)

        self._section_label(card, "02  /  MASAÜSTÜ", "Yerleşim ve görünüm")
        tk.Label(card, text="Konum", fg=TEXT, bg=PANEL, font=("Segoe UI", 10, "bold")).pack(anchor="w", pady=(14, 5))
        self.position_var = tk.StringVar(value="Sol")
        self.position_box = ttk.Combobox(card, textvariable=self.position_var, values=POSITIONS, state="readonly")
        self.position_box.pack(fill="x")
        self.position_box.bind("<<ComboboxSelected>>", self._apply_position)

        tk.Label(card, text="Ekran", fg=TEXT, bg=PANEL, font=("Segoe UI", 10, "bold")).pack(anchor="w", pady=(11, 5))
        self.monitor_var = tk.StringVar(value=self.monitor_names[0])
        self.monitor_box = ttk.Combobox(card, textvariable=self.monitor_var, values=self.monitor_names, state="readonly")
        self.monitor_box.pack(fill="x")
        self.monitor_box.bind("<<ComboboxSelected>>", self._apply_monitor)
        for wheel_event in ("<MouseWheel>", "<Button-4>", "<Button-5>"):
            self.monitor_box.bind(wheel_event, self._ignore_combo_wheel, add="+")

        size_header = tk.Frame(card, bg=PANEL)
        size_header.pack(fill="x", pady=(14, 0))
        tk.Label(size_header, text="Genişlik", fg=TEXT, bg=PANEL, font=("Segoe UI", 10, "bold")).pack(side="left")
        self.size_value = tk.Label(size_header, text="220 px", fg="#c5beff", bg=PANEL, font=("Segoe UI", 9, "bold"))
        self.size_value.pack(side="right")
        self.size_var = tk.IntVar(value=220)
        self.size_scale = ttk.Scale(card, from_=64, to=520, variable=self.size_var, command=self._apply_width)
        self.size_scale.pack(fill="x", pady=(3, 0))
        tk.Label(card, text="Genişlik oranlı değişir; GIF’in görüntüsü ezilmez.", fg=MUTED, bg=PANEL, font=("Segoe UI", 8)).pack(anchor="w", pady=(1, 13))
        self._divider(card)

        opacity_header = tk.Frame(card, bg=PANEL)
        opacity_header.pack(fill="x", pady=(14, 0))
        tk.Label(opacity_header, text="Saydamlık", fg=TEXT, bg=PANEL, font=("Segoe UI", 10, "bold")).pack(side="left")
        self.opacity_value = tk.Label(opacity_header, text="100%", fg="#c5beff", bg=PANEL, font=("Segoe UI", 9, "bold"))
        self.opacity_value.pack(side="right")
        self.opacity_var = tk.IntVar(value=100)
        self.opacity_scale = ttk.Scale(card, from_=20, to=100, variable=self.opacity_var, command=self._apply_opacity)
        self.opacity_scale.pack(fill="x", pady=(3, 5))
        self.clickthrough_var = tk.BooleanVar(value=False)
        self.clickthrough_check = tk.Checkbutton(card, text="Tıklamaları alttaki pencereye geçir", variable=self.clickthrough_var, command=self._apply_clickthrough, bg=PANEL, fg=MUTED, activebackground=PANEL, activeforeground=TEXT, selectcolor=PANEL2, font=("Segoe UI", 9), anchor="w")
        if sys.platform.startswith("linux") and (is_wayland_session() or not self.monitors):
            self.clickthrough_check.config(state="disabled", text="Tıklama geçişi X11 oturumu gerektirir")
        self.clickthrough_check.pack(fill="x", pady=(0, 10))
        self.locked_var = tk.BooleanVar(value=False)
        self.locked_check = tk.Checkbutton(card, text="Konumu kilitle", variable=self.locked_var, command=self._apply_locked, bg=PANEL, fg=MUTED, activebackground=PANEL, activeforeground=TEXT, selectcolor=PANEL2, font=("Segoe UI", 9), anchor="w")
        self.locked_check.pack(fill="x", pady=(0, 7))
        self._divider(card)

        self.performance_var = tk.BooleanVar(value=False)
        self.performance_check = tk.Checkbutton(card, text="Tam ekran uygulamada oynatmayı duraklat", variable=self.performance_var, command=self._apply_performance_rule, bg=PANEL, fg=MUTED, activebackground=PANEL, activeforeground=TEXT, selectcolor=PANEL2, font=("Segoe UI", 9), anchor="w")
        if sys.platform.startswith("linux") and is_wayland_session():
            self.performance_check.config(state="disabled", text="Tam ekran algılama X11 oturumu gerektirir")
        self.performance_check.pack(fill="x", pady=(10, 7))
        self._divider(card)

        self._section_label(card, "03  /  OYNATMA", "Seçili animasyon")
        actions = tk.Frame(card, bg=PANEL)
        actions.pack(fill="x", pady=(14, 0))
        self._button(actions, "▶  Oynat", lambda: self.toggle_play(True), primary=True).pack(side="left", fill="x", expand=True, padx=(0, 5))
        self._button(actions, "Ⅱ  Duraklat", lambda: self.toggle_play(False)).pack(side="left", fill="x", expand=True, padx=5)
        self.visibility_button = self._button(actions, "◉  Gizle", self.toggle_visible)
        self.visibility_button.pack(side="left", fill="x", expand=True, padx=(5, 0))
        self.visibility_button.config(state="disabled")

        self._section_label(card, "04  /  PROFİLLER", "Düzen profilleri")
        profile_row = tk.Frame(card, bg=PANEL)
        profile_row.pack(fill="x")
        self.profile_var = tk.StringVar()
        self.profile_box = ttk.Combobox(profile_row, textvariable=self.profile_var, state="readonly", width=16)
        self.profile_box.pack(side="left", fill="x", expand=True, padx=(0, 4))
        self._button(profile_row, "Kaydet", self.save_profile).pack(side="left", padx=3)
        self._button(profile_row, "Yükle", self.load_profile).pack(side="left", padx=3)
        self._button(profile_row, "Sil", self.delete_profile).pack(side="left", padx=(3, 0))

        footer = tk.Frame(self.root, bg=BG)
        footer.pack(fill="x", padx=22, pady=(0, 16))
        actions = tk.Frame(footer, bg=BG)
        actions.pack(fill="x")
        tk.Button(actions, text="Tepsiye gizle" if os.name == "nt" else "Simge durumuna küçült", command=self.hide_to_tray if os.name == "nt" else self.minimize_window, bg=BG, fg=MUTED, activebackground=BG, activeforeground=TEXT, relief="flat", font=("Segoe UI", 9), cursor="hand2").pack(side="left")
        tk.Button(actions, text="Tam kapat", command=self.quit, bg=BG, fg=MUTED, activebackground=BG, activeforeground=TEXT, relief="flat", font=("Segoe UI", 9), cursor="hand2").pack(side="right")
        tk.Label(footer, text="İpucu: Görseli sürükle; kenarlara yaklaşınca hizalanır.", fg=MUTED, bg=BG, font=("Segoe UI", 9)).pack(anchor="w", pady=(3, 0))

    def _divider(self, parent):
        tk.Frame(parent, bg="#263b64", height=1).pack(fill="x", pady=(4, 0))

    def _section_label(self, parent, eyebrow, title):
        section = tk.Frame(parent, bg=PANEL)
        section.pack(fill="x", anchor="w", pady=(13, 0))
        accent = tk.Frame(section, bg=CYAN, width=3, height=30)
        accent.pack(side="left", padx=(0, 9))
        labels = tk.Frame(section, bg=PANEL)
        labels.pack(side="left", fill="x", expand=True)
        tk.Label(labels, text=eyebrow, fg=CYAN, bg=PANEL, font=("Segoe UI", 7, "bold")).pack(anchor="w")
        tk.Label(labels, text=title, fg=TEXT, bg=PANEL, font=("Segoe UI", 12, "bold")).pack(anchor="w", pady=(1, 0))

    def _draw_hero(self, event=None):
        canvas = getattr(self, "hero_canvas", None)
        if not canvas or not canvas.winfo_exists():
            return
        width = max(1, canvas.winfo_width())
        height = max(1, canvas.winfo_height())
        if width < 100:
            return
        canvas.delete("all")
        stops = ((0.0, (13, 22, 62)), (0.46, (10, 21, 48)), (1.0, (24, 14, 61)))
        stripe = 3
        for x in range(0, width, stripe):
            t = x / max(1, width - 1)
            for index in range(len(stops) - 1):
                if stops[index][0] <= t <= stops[index + 1][0]:
                    left, right = stops[index], stops[index + 1]
                    ratio = (t - left[0]) / (right[0] - left[0])
                    rgb = tuple(round(a + (b - a) * ratio) for a, b in zip(left[1], right[1]))
                    break
            color = "#" + "".join(f"{component:02x}" for component in rgb)
            canvas.create_rectangle(x, 0, x + stripe + 1, height, fill=color, outline="")

        cx = width / 2
        # Glowing orbit lines, drawn as separate curves so the title stays clear.
        for offset, color in ((0, "#3b58a3"), (1, "#4e39a8")):
            points = []
            for step in range(41):
                t = step / 40
                px = cx - 145 + t * 290
                py = 87 + math.sin(t * math.tau + offset * 0.6) * (23 + offset * 3)
                points.extend((px, py))
            canvas.create_line(*points, fill=color, width=2 if offset == 0 else 1, smooth=True)
        for index in range(46):
            x = (index * 83 + 29) % width
            y = (index * 47 + 19) % max(1, height)
            radius = 1 if index % 5 else 2
            tint = CYAN if index % 3 else "#aa8aff"
            canvas.create_oval(x - radius, y - radius, x + radius, y + radius, fill=tint, outline="")

        self._draw_preview_window(canvas, 16, 36, "cat")
        self._draw_preview_window(canvas, width - 120, 36, "star")
        canvas.create_text(cx + 1, 75, text="GIFORA", fill="#05091a", font=("Segoe UI", 34, "bold"))
        canvas.create_text(cx, 72, text="GIFORA", fill=TEXT, font=("Segoe UI", 34, "bold"))
        canvas.create_text(cx, 113, text="ANIMATED DESKTOP COMPANIONS", fill=CYAN, font=("Segoe UI", 9, "bold"),
                           spacing=2)
        canvas.create_rectangle(width - 84, 11, width - 14, 35, fill="#2b2461", outline="#755dff", width=1)
        canvas.create_text(width - 49, 23, text=f"v{APP_VERSION}", fill="#e8e1ff", font=("Segoe UI", 8, "bold"))
        canvas.create_text(18, height - 14, text="✦  YOUR DESKTOP, ALIVE", anchor="w", fill="#9daee5", font=("Segoe UI", 7, "bold"))

    @staticmethod
    def _draw_preview_window(canvas, x, y, character):
        width, height = 104, 70
        canvas.create_rectangle(x + 2, y + 3, x + width + 2, y + height + 3, fill="#060b20", outline="")
        canvas.create_rectangle(x, y, x + width, y + height, fill="#101d42", outline="#6b75ff", width=2)
        canvas.create_rectangle(x + 2, y + 2, x + width - 2, y + 16, fill="#202957", outline="")
        for dot, color in enumerate(("#ff8ee8", "#71e9ff", "#9c9fff")):
            canvas.create_oval(x + 8 + dot * 10, y + 6, x + 14 + dot * 10, y + 12, fill=color, outline="")
        if character == "cat":
            # Small pixel-art companion echoing the mascots in the promo art.
            for dx, dy, dw, dh, color in (
                (39, 28, 26, 18, "#e2d9ff"), (35, 23, 12, 12, "#f3eaff"),
                (57, 23, 12, 12, "#f3eaff"), (42, 34, 3, 3, "#603cba"),
                (57, 34, 3, 3, "#603cba"), (48, 40, 7, 3, "#ff9de8"),
                (30, 44, 14, 6, "#c9bdff"), (62, 44, 14, 6, "#c9bdff"),
            ):
                canvas.create_rectangle(x + dx, y + dy, x + dx + dw, y + dy + dh, fill=color, outline="")
            canvas.create_text(x + 21, y + 30, text="✦", fill="#78efff", font=("Segoe UI", 10, "bold"))
            canvas.create_text(x + 83, y + 45, text="♥", fill="#ff86e5", font=("Segoe UI", 9, "bold"))
        else:
            canvas.create_rectangle(x + 42, y + 25, x + 62, y + 48, fill="#b9c9ff", outline="#e3e8ff")
            canvas.create_oval(x + 45, y + 19, x + 59, y + 33, fill="#e6eaff", outline="")
            canvas.create_rectangle(x + 45, y + 46, x + 59, y + 52, fill="#ffbd75", outline="")
            canvas.create_text(x + 26, y + 28, text="✦", fill="#ff84ea", font=("Segoe UI", 10, "bold"))
            canvas.create_text(x + 79, y + 47, text="✧", fill="#66eaff", font=("Segoe UI", 10, "bold"))

    @staticmethod
    def _metric_card(parent, caption):
        palette = {"KOLEKSİYON": CYAN, "GÖRÜNÜR": "#be91ff", "OYNATIYOR": "#73a4ff"}
        accent = palette.get(caption, CYAN)
        card = tk.Frame(parent, bg="#101d39", padx=12, pady=7, highlightthickness=1, highlightbackground="#283c68")
        card.pack(side="left", fill="x", expand=True, padx=(0, 7))
        tk.Frame(card, bg=accent, height=2).pack(fill="x", pady=(0, 6))
        tk.Label(card, text=f"✦  {caption}", fg=accent, bg="#101d39", font=("Segoe UI", 7, "bold")).pack(anchor="w")
        value = tk.Label(card, text="0", fg=TEXT, bg="#101d39", font=("Segoe UI", 18, "bold"))
        value.pack(anchor="w", pady=(1, 0))
        return value

    def show_main_window(self):
        try:
            self._tray_hidden = False
            self.root.deiconify()
            self.root.state("normal")
            self.root.lift()
            self.root.focus_force()
        except tk.TclError:
            pass

    def hide_to_tray(self):
        if self.tray_icon:
            self.save_config()
            self._tray_hidden = True
            self.root.withdraw()
        else:
            self.minimize_window()

    def _on_root_unmap(self, _event=None):
        """Convert the title-bar minimize action into a true notification-area hide."""
        if not self.tray_icon or self._tray_hidden:
            return
        try:
            if self.root.state() == "iconic":
                self.root.after_idle(self._withdraw_iconified_window)
        except tk.TclError:
            pass

    def _withdraw_iconified_window(self):
        if not self.tray_icon or self._tray_hidden:
            return
        try:
            if self.root.state() == "iconic":
                self.save_config()
                self._tray_hidden = True
                self.root.withdraw()
        except tk.TclError:
            pass

    def minimize_window(self):
        try:
            self.root.iconify()
        except tk.TclError:
            pass

    @staticmethod
    def _log_tray_error(exc):
        try:
            CONFIG_DIR.mkdir(parents=True, exist_ok=True)
            with (CONFIG_DIR / "hatalar.log").open("a", encoding="utf-8") as error_log:
                error_log.write(f"\n--- Bildirim alanı simgesi kurulamadı ---\n{type(exc).__name__}: {exc}\n")
        except OSError:
            pass

    def _scroll_panel(self, event):
        if event.delta:
            self.panel_canvas.yview_scroll(int(-event.delta / 120), "units")

    def _scroll_tree(self, event):
        if getattr(event, "num", None) == 4:
            amount = -1
        elif getattr(event, "num", None) == 5:
            amount = 1
        elif event.delta:
            amount = int(-event.delta / 120)
            if amount == 0:
                amount = -1 if event.delta > 0 else 1
        else:
            return "break"
        self.tree.yview_scroll(amount, "units")
        return "break"

    @staticmethod
    def _ignore_combo_wheel(_event):
        return "break"

    def _register_file_drop(self):
        if not self.file_drop_enabled:
            return
        try:
            for widget in (self.root, self.tree, self.drop_hint):
                widget.drop_target_register(DND_FILES)
                widget.dnd_bind("<<Drop>>", self._on_file_drop)
        except (tk.TclError, AttributeError):
            self.file_drop_enabled = False
            self.drop_hint.config(text="Dosya bırakma kullanılamıyor; Görsel ekle düğmesini kullan.")

    def _on_file_drop(self, event):
        try:
            paths = parse_drop_data(self.root, event.data)
            self.add_dropped_files(paths)
            return getattr(event, "action", "copy") or "copy"
        except Exception as exc:
            self._log_drop_error(exc)
            messagebox.showerror("Dosyalar eklenemedi", f"Bırakılan dosyalar okunamadı.\n{type(exc).__name__}: {exc}", parent=self.root)
            return "refuse_drop"

    @staticmethod
    def _log_drop_error(exc):
        try:
            CONFIG_DIR.mkdir(parents=True, exist_ok=True)
            with (CONFIG_DIR / "hatalar.log").open("a", encoding="utf-8") as error_log:
                error_log.write("\n--- Sürükle bırak hatası ---\n")
                traceback.print_exception(type(exc), exc, exc.__traceback__, file=error_log)
        except OSError:
            pass

    def add_dropped_files(self, paths):
        paths = list(paths)
        valid = supported_drop_paths(paths)
        if not valid:
            received = ", ".join(Path(str(path)).name for path in paths[:3]) or "Dosya yolu alınamadı"
            detail = "Desteklenen türler: GIF, WebP ve PNG/APNG."
            if paths:
                detail += f"\nAlınan: {received}"
            self.drop_hint.config(text="Dosya eklenemedi · GIF, WebP veya PNG bırakın")
            messagebox.showinfo("Dosya eklenmedi", detail, parent=self.root)
            return
        for index, path in enumerate(valid, 1):
            self.drop_hint.config(text=f"Yükleniyor… {index}/{len(valid)} · {Path(path).name}")
            self.root.update_idletasks()
            self.add_gif(path)
        self.drop_hint.config(text=f"{len(valid)} dosya alındı · GIF’ler hazırlanıyor")
        ignored = len(paths) - len(valid)
        if ignored:
            messagebox.showinfo("Bazı dosyalar atlandı", f"{len(valid)} görsel eklendi; desteklenmeyen veya bulunamayan {ignored} dosya atlandı.", parent=self.root)

    def _button(self, parent, text, command, primary=False):
        base = ACCENT if primary else PANEL2
        hover = ACCENT_HOVER if primary else PANEL_HOVER
        button = tk.Button(parent, text=text, command=command, bg=base, fg=TEXT, activebackground=hover, activeforeground=TEXT, relief="flat", bd=0, padx=12, pady=10, font=("Segoe UI", 9, "bold"), cursor="hand2", highlightthickness=0)
        button.bind("<Enter>", lambda _event, widget=button, target=hover: self._animate_button(widget, target), add="+")
        button.bind("<Leave>", lambda _event, widget=button, target=base: self._animate_button(widget, target), add="+")
        return button

    def _animate_button(self, button, target, step=0):
        if not button.winfo_exists():
            return
        key = str(button)
        previous = self._button_animations.pop(key, None)
        if previous:
            try:
                self.root.after_cancel(previous)
            except tk.TclError:
                pass
        try:
            current = button.cget("bg")
            source_rgb = tuple(int(current[i:i + 2], 16) for i in (1, 3, 5))
            target_rgb = tuple(int(target[i:i + 2], 16) for i in (1, 3, 5))
            t = min(1.0, (step + 1) / 5)
            eased = 1 - (1 - t) ** 2
            color = "#" + "".join(f"{round(a + (b - a) * eased):02x}" for a, b in zip(source_rgb, target_rgb))
            button.configure(bg=color)
        except (tk.TclError, ValueError):
            self._button_animations.pop(key, None)
            return
        if step < 4:
            self._button_animations[key] = self.root.after(16, lambda: self._animate_button(button, target, step + 1))

    def _render_items(self):
        if not hasattr(self, "tree") or not self.tree.winfo_exists():
            return
        query = self.search_var.get().strip().casefold()
        shown = []
        for item in self.items:
            filename = Path(item["path"]).name
            if query and query not in filename.casefold() and query not in item["position"].casefold():
                continue
            shown.append(item)
        for child in self.tree.get_children():
            self.tree.delete(child)
        for item in shown:
            self.tree.insert("", "end", iid=item["id"], values=self._tree_values(item))
        if self.selected_id and any(item["id"] == self.selected_id for item in shown):
            self.tree.selection_set(self.selected_id)
        elif self.selected_id:
            self.selected_id = None
            self.tree.selection_set(())
            self.visibility_button.config(state="disabled")
        else:
            self.visibility_button.config(state="disabled")
        visible_count = sum(1 for item in self.items if item["visible"])
        playing_count = sum(1 for item in self.items if item["playing"])
        self.total_stat.config(text=str(len(self.items)))
        self.visible_stat.config(text=str(visible_count))
        self.playing_stat.config(text=str(playing_count))
        self.collection_summary.config(
            text=f"{len(shown)} sonuç gösteriliyor · toplam {len(self.items)} görsel"
            if self.items else "Henüz görsel eklenmedi · dosyaları bırak veya Görsel ekle’yi seç"
        )

    def add_gif(self, path=None, saved=None):
        if not path:
            path = filedialog.askopenfilename(title="Animasyonlu görsel seç", filetypes=[("Animasyonlu görseller", "*.gif *.webp *.png"), ("GIF", "*.gif"), ("WebP", "*.webp"), ("PNG / APNG", "*.png")])
        if not path:
            return
        try:
            with Image.open(path) as im:
                if im.format not in ("GIF", "WEBP", "PNG"):
                    raise ValueError("Bu dosya türü desteklenmiyor. GIF, WebP veya PNG seç.")
                im.seek(0)
                if im.width == 0 or im.height == 0:
                    raise ValueError("GIF dosyasının boyutları okunamadı.")
        except Exception as exc:
            messagebox.showerror("GIF açılamadı", str(exc), parent=self.root)
            return
        data = saved or {"path": str(path), "position": "Sol", "width": 220, "opacity": 100, "clickthrough": False, "monitor": 0, "locked": False, "playing": True, "visible": True, "xy": None}
        item_id = f"gif{len(self.items)+1}_{id(data)}"
        item = {"id": item_id, "path": str(path), "position": data.get("position", "Sol"), "width": int(data.get("width", 220)), "opacity": int(data.get("opacity", 100)), "clickthrough": bool(data.get("clickthrough", False)), "monitor": min(max(0, int(data.get("monitor", 0))), len(self.monitors) - 1), "locked": bool(data.get("locked", False)), "playing": bool(data.get("playing", True)), "visible": bool(data.get("visible", True)), "xy": data.get("xy")}
        item["status"] = "Yükleniyor…"
        self.items.append(item)
        overlay = GifOverlay(self, item_id, item["path"], item["position"], item["width"], item["opacity"], item["clickthrough"], item["monitor"], item["locked"])
        item["clickthrough"] = overlay.clickthrough
        self.overlays[item_id] = overlay
        if item["xy"]:
            overlay.manual_xy = tuple(item["xy"])
            overlay.reposition()
        overlay.set_playing(item["playing"] and not self.performance_paused)
        overlay.set_visible(item["visible"])
        self.search_var.set("")
        self._render_items()
        self.select_item(item_id)
        self.save_config()

    def select_item(self, item_id):
        if item_id not in self.overlays:
            return
        if self.tree.selection() != (item_id,):
            self.tree.selection_set(item_id)
        self.selected_id = item_id
        item = self._item()
        self._updating_controls = True
        try:
            self.position_var.set(item["position"])
            self.size_var.set(item["width"])
            self.size_value.config(text=f'{item["width"]} px')
            self.opacity_var.set(item["opacity"])
            self.opacity_value.config(text=f'{item["opacity"]}%')
            self.clickthrough_var.set(item["clickthrough"])
            self.monitor_var.set(self.monitor_names[item["monitor"]])
            self.locked_var.set(item["locked"])
            self.visibility_button.config(text="◉  Gizle" if item["visible"] else "◉  Göster")
            self.visibility_button.config(state="normal")
        finally:
            self._updating_controls = False

    @staticmethod
    def _tree_values(item):
        return (Path(item["path"]).name, item["position"], f'{item["width"]} px', item.get("status", "Hazır"), "Açık" if item["visible"] else "Kapalı")

    def _item(self):
        return next((it for it in self.items if it["id"] == self.selected_id), None)

    def _on_select(self, _event=None):
        selection = self.tree.selection()
        if selection:
            self.select_item(selection[0])

    def _on_tree_click(self, event):
        if self.tree.identify_region(event.x, event.y) != "cell" or self.tree.identify_column(event.x) != "#5":
            return
        item_id = self.tree.identify_row(event.y)
        if not item_id or item_id not in self.overlays:
            return
        if self.tree.selection() != (item_id,):
            self.tree.selection_set(item_id)
        self.select_item(item_id)
        self.toggle_visible()
        return "break"

    def _apply_position(self, _event=None):
        item = self._item()
        if item:
            item["position"] = self.position_var.get()
            self.overlays[item["id"]].set_position(item["position"])
            self.update_position_label(item["id"])
            self.save_config()

    def _apply_monitor(self, _event=None):
        item = self._item()
        if item:
            item["monitor"] = max(0, self.monitor_names.index(self.monitor_var.get()))
            self.overlays[item["id"]].set_monitor(item["monitor"])
            item["xy"] = None
            self.save_config()

    def _apply_width(self, value):
        if self._updating_controls:
            return
        width = int(float(value))
        self.size_value.config(text=f"{width} px")
        item = self._item()
        if item:
            item["width"] = width
            if self.width_after:
                self.root.after_cancel(self.width_after)
            self.width_after = self.root.after(180, lambda item_id=item["id"], applied_width=width: self._commit_width(item_id, applied_width))

    def _commit_width(self, item_id, width):
        self.width_after = None
        item = next((it for it in self.items if it["id"] == item_id), None)
        if item and item_id in self.overlays:
            item["width"] = width
            self.overlays[item_id].set_width(width)
            self.update_position_label(item_id)
            self.save_config()

    def _apply_opacity(self, value):
        if self._updating_controls:
            return
        opacity = int(float(value))
        self.opacity_value.config(text=f"{opacity}%")
        item = self._item()
        if item:
            item["opacity"] = opacity
            self.overlays[item["id"]].set_opacity(opacity)
            if self.opacity_after:
                self.root.after_cancel(self.opacity_after)
            self.opacity_after = self.root.after(180, self._commit_opacity)

    def _commit_opacity(self):
        self.opacity_after = None
        self.save_config()

    def _apply_clickthrough(self):
        item = self._item()
        if item:
            overlay = self.overlays[item["id"]]
            requested = self.clickthrough_var.get()
            if not overlay.set_clickthrough(requested):
                self.clickthrough_var.set(overlay.clickthrough)
                messagebox.showerror("Ayar uygulanamadı", "Tıklama geçirme bu pencerede etkinleştirilemedi. Önceki ayar geri yüklendi.", parent=self.root)
                return
            item["clickthrough"] = overlay.clickthrough
            self.save_config()

    def _apply_locked(self):
        item = self._item()
        if item:
            item["locked"] = self.locked_var.get()
            self.overlays[item["id"]].set_locked(item["locked"])
            self.save_config()

    def move_overlay(self, overlay, x, y):
        try:
            width = max(1, overlay.win.winfo_width(), overlay.win.winfo_reqwidth())
            height = max(1, overlay.win.winfo_height(), overlay.win.winfo_reqheight())
            overlay.last_screen_rect = (int(x), int(y), width, height)
        except tk.TclError:
            pass
        if os.name != "nt":
            try:
                overlay.win.geometry(f"+{int(x)}+{int(y)}")
            except tk.TclError:
                pass
            return
        try:
            user32 = ctypes.windll.user32
            user32.GetAncestor.argtypes = [ctypes.c_void_p, wintypes.UINT]
            user32.GetAncestor.restype = ctypes.c_void_p
            user32.SetWindowPos.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.UINT]
            user32.SetWindowPos.restype = wintypes.BOOL
            hwnd = user32.GetAncestor(overlay.win.winfo_id(), 2) or overlay.win.winfo_id()
            # Move only. Resizing on every drag event can reset Tk's requested
            # geometry and make the animated overlay jump back or stop tracking.
            flags = 0x0001 | 0x0004 | 0x0010  # NOSIZE | NOZORDER | NOACTIVATE
            if not user32.SetWindowPos(hwnd, None, int(x), int(y), 0, 0, flags):
                raise ctypes.WinError(ctypes.get_last_error())
        except (AttributeError, OSError, tk.TclError):
            try:
                overlay.win.geometry(f"+{int(x)}+{int(y)}")
            except tk.TclError:
                pass

    def monitor_for_point(self, x, y):
        for index, monitor in enumerate(self.monitors):
            if monitor["x"] <= x < monitor["right"] and monitor["y"] <= y < monitor["bottom"]:
                return index
        def distance(monitor):
            dx = max(monitor["x"] - x, 0, x - monitor["right"])
            dy = max(monitor["y"] - y, 0, y - monitor["bottom"])
            return dx * dx + dy * dy
        return min(range(len(self.monitors)), key=lambda index: distance(self.monitors[index]))

    def snap_overlay(self, overlay, x, y):
        width, height = overlay.win.winfo_width(), overlay.win.winfo_height()
        center_x, center_y = x + width // 2, y + height // 2
        index = self.monitor_for_point(center_x, center_y)
        monitor = self.monitors[index]
        left, top, right, bottom = monitor["x"], monitor["y"], monitor["right"], monitor["bottom"]
        edge = 18
        if abs(x - left) <= edge:
            x = left
        if abs(y - top) <= edge:
            y = top
        if abs((x + width) - right) <= edge:
            x = right - width
        if abs((y + height) - bottom) <= edge:
            y = bottom - height
        x = min(max(x, left), max(left, right - width))
        y = min(max(y, top), max(top, bottom - height))
        return index, x, y

    def _apply_performance_rule(self):
        self.performance_paused = False
        if self.performance_after:
            self.root.after_cancel(self.performance_after)
            self.performance_after = None
        self._poll_performance()
        self.save_config()

    def _poll_performance(self):
        self.performance_after = None
        should_pause = self.performance_var.get() and foreground_is_fullscreen()
        if should_pause != self.performance_paused:
            self.performance_paused = should_pause
            for item in self.items:
                overlay = self.overlays.get(item["id"])
                if overlay:
                    overlay.set_playing(item["playing"] and not should_pause)
        try:
            if self.root.winfo_exists():
                self.performance_after = self.root.after(1200, self._poll_performance)
        except tk.TclError:
            pass

    def profile_items(self):
        result = []
        for item in self.items:
            overlay = self.overlays.get(item["id"])
            if overlay:
                item["xy"] = list(overlay.manual_xy) if overlay.manual_xy else None
                item["monitor"] = overlay.monitor
                item["position"] = overlay.position
            result.append({key: item.get(key) for key in ("path", "position", "width", "opacity", "clickthrough", "monitor", "locked", "playing", "visible", "xy")})
        return result

    def save_profile(self):
        if not self.items:
            messagebox.showinfo("Düzen profili", "Kaydetmek için önce en az bir görsel ekle.", parent=self.root)
            return
        name = simpledialog.askstring("Düzen profilini kaydet", "Bu masaüstü düzenine bir ad ver:", parent=self.root)
        if not name or not name.strip():
            return
        name = name.strip()
        if name in self.profiles and not messagebox.askyesno("Profil güncellensin mi?", f"‘{name}’ profili var. Üzerine kaydedilsin mi?", parent=self.root):
            return
        self.profiles[name] = {"items": self.profile_items(), "pause_fullscreen": self.performance_var.get()}
        self._write_profiles()
        self._refresh_profiles(name)

    def load_profile(self):
        name = self.profile_var.get()
        profile = self.profiles.get(name)
        if not profile:
            return
        for overlay in list(self.overlays.values()):
            overlay.close()
        for child in self.tree.get_children():
            self.tree.delete(child)
        self.items.clear()
        self.overlays.clear()
        self.selected_id = None
        self.performance_var.set(bool(profile.get("pause_fullscreen", False)))
        self.performance_paused = False
        if self.performance_after:
            self.root.after_cancel(self.performance_after)
            self.performance_after = None
        for item in profile.get("items", []):
            if Path(item.get("path", "")).is_file():
                self.add_gif(item["path"], saved=item)
        self._poll_performance()
        self.save_config()

    def delete_profile(self):
        name = self.profile_var.get()
        if name not in self.profiles:
            return
        if not messagebox.askyesno("Profili sil", f"‘{name}’ düzen profili silinsin mi?", parent=self.root):
            return
        del self.profiles[name]
        self._write_profiles()
        self._refresh_profiles()

    def _load_profiles(self):
        try:
            data = json.loads(PROFILES_FILE.read_text(encoding="utf-8"))
            self.profiles = data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            self.profiles = {}
        self._refresh_profiles()

    def _write_profiles(self):
        try:
            CONFIG_DIR.mkdir(parents=True, exist_ok=True)
            PROFILES_FILE.write_text(json.dumps(self.profiles, ensure_ascii=False, indent=2), encoding="utf-8")
        except OSError:
            messagebox.showerror("Kaydedilemedi", "Düzen profili diske yazılamadı.", parent=self.root)

    def _refresh_profiles(self, selected=None):
        names = sorted(self.profiles, key=str.casefold)
        self.profile_box.configure(values=names)
        if selected in names:
            self.profile_var.set(selected)
        elif self.profile_var.get() not in names:
            self.profile_var.set(names[0] if names else "")

    def update_position_label(self, item_id):
        item = next((it for it in self.items if it["id"] == item_id), None)
        if item and self.tree.exists(item_id):
            overlay = self.overlays[item_id]
            item["xy"] = list(overlay.manual_xy) if overlay.manual_xy else None
            item["position"] = overlay.position
            item["monitor"] = overlay.monitor
            if item_id == self.selected_id:
                self.monitor_var.set(self.monitor_names[overlay.monitor])
                self.position_var.set(overlay.position)
            self._render_items()

    def toggle_play(self, playing):
        item = self._item()
        if not item:
            return
        item["playing"] = playing
        self.overlays[item["id"]].set_playing(playing and not self.performance_paused)
        self._render_items()
        self.save_config()

    def toggle_visible(self):
        item = self._item()
        if not item:
            return
        item["visible"] = not item["visible"]
        self.overlays[item["id"]].set_visible(item["visible"])
        if self.tree.exists(item["id"]):
            self.tree.item(item["id"], values=self._tree_values(item))
        self.visibility_button.config(text="◉  Gizle" if item["visible"] else "◉  Göster")
        self._render_items()
        self.save_config()

    def set_all_visible(self, visible):
        changed = False
        for item in self.items:
            visible = bool(visible)
            if item["visible"] != visible:
                changed = True
                item["visible"] = visible
                self.overlays[item["id"]].set_visible(visible)
        if self.selected_id:
            selected = self._item()
            if selected:
                self.visibility_button.config(text="◉  Gizle" if selected["visible"] else "◉  Göster")
        self._render_items()
        if changed:
            self.save_config()

    def set_all_playing(self, playing):
        changed = False
        for item in self.items:
            playing = bool(playing)
            if item["playing"] != playing:
                changed = True
                item["playing"] = playing
                self.overlays[item["id"]].set_playing(playing and not self.performance_paused)
        self._render_items()
        if changed:
            self.save_config()

    def remove_gif(self):
        item = self._item()
        if not item:
            return
        self.overlays.pop(item["id"]).close()
        if self.tree.exists(item["id"]):
            self.tree.delete(item["id"])
        self.items.remove(item)
        self.selected_id = None
        if self.items:
            self.select_item(self.items[-1]["id"])
        self._render_items()
        self.save_config()

    def on_overlay_ready(self, item_id):
        item = next((it for it in self.items if it["id"] == item_id), None)
        if not item:
            return
        item["status"] = "Hazır"
        overlay = self.overlays.get(item_id)
        if overlay:
            overlay.set_visible(item["visible"])
        if self.tree.exists(item_id):
            self.tree.item(item_id, values=self._tree_values(item))
        if hasattr(self, "drop_hint") and not any(it.get("status") == "Yükleniyor…" for it in self.items):
            self.drop_hint.config(text="Dosyaları bu listeye sürükleyip bırak")

    def on_overlay_error(self, item_id, error):
        if item_id not in self.overlays:
            return
        item = next((it for it in self.items if it["id"] == item_id), None)
        name = Path(item["path"]).name if item else "GIF"
        self.overlays.pop(item_id).close()
        self.items = [it for it in self.items if it["id"] != item_id]
        if self.tree.exists(item_id):
            self.tree.delete(item_id)
        self.selected_id = None
        if self.items:
            self.select_item(self.items[-1]["id"])
        self._render_items()
        if hasattr(self, "drop_hint") and not any(it.get("status") == "Yükleniyor…" for it in self.items):
            self.drop_hint.config(text="Bir görsel yüklenemedi · Dosya bozuk veya desteklenmiyor")
        self.save_config()
        messagebox.showerror("GIF açılamadı", f"{name}\n\n{error}", parent=self.root)

    def _load_config(self):
        try:
            data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
            self.performance_var.set(bool(data.get("pause_fullscreen", False)))
            for item in data.get("items", []):
                if Path(item.get("path", "")).is_file():
                    self.add_gif(item["path"], saved=item)
        except (OSError, ValueError, tk.TclError):
            pass

    def save_config(self):
        try:
            CONFIG_DIR.mkdir(parents=True, exist_ok=True)
            rows = []
            for item in self.items:
                overlay = self.overlays.get(item["id"])
                if overlay:
                    item["xy"] = list(overlay.manual_xy) if overlay.manual_xy else None
                    item["monitor"] = overlay.monitor
                    item["position"] = overlay.position
                rows.append({key: item.get(key) for key in ("path", "position", "width", "opacity", "clickthrough", "monitor", "locked", "playing", "visible", "xy")})
            CONFIG_FILE.write_text(json.dumps({"items": rows, "pause_fullscreen": self.performance_var.get()}, ensure_ascii=False, indent=2), encoding="utf-8")
        except OSError:
            pass

    def quit(self):
        self.save_config()
        if self.tray_icon:
            self.tray_icon.stop()
            self.tray_icon = None
        for callback_id in (self.width_after, self.opacity_after, self.performance_after):
            if callback_id:
                try:
                    self.root.after_cancel(callback_id)
                except tk.TclError:
                    pass
        self.width_after = None
        self.opacity_after = None
        self.performance_after = None
        for overlay in list(self.overlays.values()):
            overlay.close()
        self.root.destroy()


def main():
    root = create_root_window()
    if os.name != "nt":
        GiforaApp(root)
        root.mainloop()
        return
    try:
        icon = Image.new("RGBA", (64, 64), (139, 124, 255, 255))
        d = ImageDraw.Draw(icon)
        d.rounded_rectangle((3, 3, 61, 61), radius=15, fill=(139, 124, 255, 255))
        d.text((16, 7), "G", fill="white", font=ImageFont.truetype("segoeui.ttf", 44))
        icon.save(asset_path("gifora.ico"), format="ICO", sizes=[(16,16),(32,32),(48,48),(64,64)])
        root.iconbitmap(asset_path("gifora.ico"))
    except Exception:
        pass
    GiforaApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
