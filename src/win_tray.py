"""Minimal native Windows notification-area icon for Gifora."""

import ctypes
import os
import secrets
import tkinter as tk


class WinTrayIcon:
    WM_APP = 0x8000
    WM_CONTEXTMENU = 0x007B
    WM_RBUTTONUP = 0x0205
    WM_LBUTTONDBLCLK = 0x0203
    WM_NIN_SELECT = 0x0400
    WM_NIN_KEYSELECT = 0x0401
    NIM_ADD, NIM_DELETE, NIM_SETVERSION = 0, 2, 4
    NIF_MESSAGE, NIF_ICON, NIF_TIP = 1, 2, 4

    def __init__(self, root, icon_path, version, on_show, on_quit):
        if os.name != "nt":
            raise OSError("The Windows tray icon is available only on Windows.")
        self.root = root
        self.on_show = on_show
        self.on_quit = on_quit
        self._running = True
        self._pending_menu = False
        self._pending_show = False
        self._pending_restore = False
        self._poll_after = None
        self._shell = ctypes.WinDLL("shell32", use_last_error=True)
        self._user32 = ctypes.WinDLL("user32", use_last_error=True)
        self._kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        self._kernel32.GetModuleHandleW.argtypes = [ctypes.c_wchar_p]
        self._kernel32.GetModuleHandleW.restype = ctypes.c_void_p
        self._hinstance = self._kernel32.GetModuleHandleW(None)
        self._wndproc_type = ctypes.WINFUNCTYPE(
            ctypes.c_ssize_t, ctypes.c_void_p, ctypes.c_uint,
            ctypes.c_size_t, ctypes.c_ssize_t,
        )
        self._wndproc_ref = self._wndproc_type(self._wndproc)
        self._class_name = f"GiforaTray_{os.getpid()}_{secrets.token_hex(4)}"
        self._callback_message = 0x8001
        self._user32.RegisterWindowMessageW.argtypes = [ctypes.c_wchar_p]
        self._user32.RegisterWindowMessageW.restype = ctypes.c_uint
        self._taskbar_created = self._user32.RegisterWindowMessageW("TaskbarCreated")

        class WNDCLASSW(ctypes.Structure):
            _fields_ = [
                ("style", ctypes.c_uint), ("lpfnWndProc", self._wndproc_type),
                ("cbClsExtra", ctypes.c_int), ("cbWndExtra", ctypes.c_int),
                ("hInstance", ctypes.c_void_p), ("hIcon", ctypes.c_void_p),
                ("hCursor", ctypes.c_void_p), ("hbrBackground", ctypes.c_void_p),
                ("lpszMenuName", ctypes.c_wchar_p), ("lpszClassName", ctypes.c_wchar_p),
            ]

        class NOTIFYICONDATAW(ctypes.Structure):
            _fields_ = [
                ("cbSize", ctypes.c_uint), ("hWnd", ctypes.c_void_p), ("uID", ctypes.c_uint),
                ("uFlags", ctypes.c_uint), ("uCallbackMessage", ctypes.c_uint),
                ("hIcon", ctypes.c_void_p), ("szTip", ctypes.c_wchar * 128),
                ("dwState", ctypes.c_uint), ("dwStateMask", ctypes.c_uint),
                ("szInfo", ctypes.c_wchar * 256), ("uTimeout", ctypes.c_uint),
                ("uVersion", ctypes.c_uint), ("szInfoTitle", ctypes.c_wchar * 64),
                ("dwInfoFlags", ctypes.c_uint), ("guidItem", ctypes.c_ubyte * 16),
                ("hBalloonIcon", ctypes.c_void_p),
            ]

        class POINT(ctypes.Structure):
            _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]

        class MSG(ctypes.Structure):
            _fields_ = [
                ("hwnd", ctypes.c_void_p), ("message", ctypes.c_uint),
                ("wParam", ctypes.c_size_t), ("lParam", ctypes.c_ssize_t),
                ("time", ctypes.c_uint), ("pt", POINT),
            ]

        self._notify_data_type = NOTIFYICONDATAW
        self._point_type = POINT
        self._msg_type = MSG
        self._user32.RegisterClassW.argtypes = [ctypes.POINTER(WNDCLASSW)]
        self._user32.RegisterClassW.restype = ctypes.c_ushort
        self._user32.CreateWindowExW.argtypes = [
            ctypes.c_uint, ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_uint,
            ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
            ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p,
        ]
        self._user32.CreateWindowExW.restype = ctypes.c_void_p
        self._user32.DefWindowProcW.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_size_t, ctypes.c_ssize_t]
        self._user32.DefWindowProcW.restype = ctypes.c_ssize_t
        self._user32.PeekMessageW.argtypes = [ctypes.POINTER(MSG), ctypes.c_void_p, ctypes.c_uint, ctypes.c_uint, ctypes.c_uint]
        self._user32.TranslateMessage.argtypes = [ctypes.POINTER(MSG)]
        self._user32.DispatchMessageW.argtypes = [ctypes.POINTER(MSG)]
        self._user32.GetCursorPos.argtypes = [ctypes.POINTER(POINT)]
        self._user32.SetForegroundWindow.argtypes = [ctypes.c_void_p]
        self._user32.DestroyWindow.argtypes = [ctypes.c_void_p]
        self._user32.LoadImageW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_uint, ctypes.c_int, ctypes.c_int, ctypes.c_uint]
        self._user32.LoadImageW.restype = ctypes.c_void_p
        self._user32.DestroyIcon.argtypes = [ctypes.c_void_p]
        self._shell.Shell_NotifyIconW.argtypes = [ctypes.c_uint, ctypes.POINTER(NOTIFYICONDATAW)]
        self._shell.Shell_NotifyIconW.restype = ctypes.c_int

        window_class = WNDCLASSW()
        window_class.lpfnWndProc = self._wndproc_ref
        window_class.hInstance = self._hinstance
        window_class.lpszClassName = self._class_name
        if not self._user32.RegisterClassW(ctypes.byref(window_class)):
            raise ctypes.WinError(ctypes.get_last_error())
        self._hwnd = self._user32.CreateWindowExW(
            0, self._class_name, "Gifora tray", 0x80000000,
            -32000, -32000, 1, 1, None, None, self._hinstance, None,
        )
        if not self._hwnd:
            raise ctypes.WinError(ctypes.get_last_error())
        self._hicon = self._user32.LoadImageW(None, str(icon_path), 1, 0, 0, 0x0010 | 0x0040)
        if not self._hicon:
            self._user32.DestroyWindow(self._hwnd)
            self._hwnd = None
            raise ctypes.WinError(ctypes.get_last_error())

        self._notify_data = NOTIFYICONDATAW()
        self._notify_data.cbSize = ctypes.sizeof(NOTIFYICONDATAW)
        self._notify_data.hWnd = self._hwnd
        self._notify_data.uID = 1
        self._notify_data.uFlags = self.NIF_MESSAGE | self.NIF_ICON | self.NIF_TIP
        self._notify_data.uCallbackMessage = self._callback_message
        self._notify_data.hIcon = self._hicon
        self._notify_data.szTip = f"Gifora {version} · GIF masaüstü alanı"
        if not self._shell.Shell_NotifyIconW(self.NIM_ADD, ctypes.byref(self._notify_data)):
            self.stop()
            raise ctypes.WinError(ctypes.get_last_error())
        self._notify_data.uVersion = 4
        self._shell.Shell_NotifyIconW(self.NIM_SETVERSION, ctypes.byref(self._notify_data))
        self._menu = tk.Menu(root, tearoff=False)
        self._menu.add_command(label="Gifora'yı göster", command=self.on_show)
        self._menu.add_separator()
        self._menu.add_command(label="Uygulamadan çık", command=self.on_quit)
        self._poll_after = root.after(40, self._poll_messages)

    def _wndproc(self, hwnd, message, wparam, lparam):
        if message == self._callback_message:
            event = lparam & 0xFFFF
            if event in (self.WM_CONTEXTMENU, self.WM_RBUTTONUP):
                self._pending_menu = True
            elif event in (self.WM_LBUTTONDBLCLK, self.WM_NIN_SELECT, self.WM_NIN_KEYSELECT):
                self._pending_show = True
            return 0
        if self._taskbar_created and message == self._taskbar_created:
            self._pending_restore = True
            return 0
        return self._user32.DefWindowProcW(hwnd, message, wparam, lparam)

    def _poll_messages(self):
        if not self._running:
            return
        msg = self._msg_type()
        while self._user32.PeekMessageW(ctypes.byref(msg), self._hwnd, 0, 0, 1):
            self._user32.TranslateMessage(ctypes.byref(msg))
            self._user32.DispatchMessageW(ctypes.byref(msg))
        if self._pending_restore:
            self._pending_restore = False
            self._shell.Shell_NotifyIconW(self.NIM_ADD, ctypes.byref(self._notify_data))
            self._notify_data.uVersion = 4
            self._shell.Shell_NotifyIconW(self.NIM_SETVERSION, ctypes.byref(self._notify_data))
        if self._pending_show:
            self._pending_show = False
            self.on_show()
        if self._pending_menu:
            self._pending_menu = False
            point = self._point_type()
            if self._user32.GetCursorPos(ctypes.byref(point)):
                self._user32.SetForegroundWindow(self._hwnd)
                self._menu.tk_popup(point.x, point.y)
                self.root.after_idle(self._menu.grab_release)
        self._poll_after = self.root.after(40, self._poll_messages)

    def stop(self):
        if not getattr(self, "_running", False):
            return
        self._running = False
        if self._poll_after:
            try:
                self.root.after_cancel(self._poll_after)
            except tk.TclError:
                pass
            self._poll_after = None
        if getattr(self, "_hwnd", None):
            if getattr(self, "_notify_data", None):
                self._shell.Shell_NotifyIconW(self.NIM_DELETE, ctypes.byref(self._notify_data))
            self._user32.DestroyWindow(self._hwnd)
            self._hwnd = None
        if getattr(self, "_hicon", None):
            self._user32.DestroyIcon(self._hicon)
            self._hicon = None
