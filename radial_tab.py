"""A small Windows radial Alt+Tab switcher."""

from __future__ import annotations

import argparse
import ctypes
from ctypes import wintypes
import math
import os
import sys
import threading

from PIL import Image, ImageFilter
from PySide6.QtCore import QObject, QPoint, QPointF, QRect, QRectF, Qt, QTimer, Signal
from PySide6.QtCore import QEasingCurve, QPropertyAnimation
from PySide6.QtGui import (
    QColor, QCursor, QFont, QFontMetrics, QGuiApplication, QIcon, QImage,
    QLinearGradient, QPainter, QPainterPath, QPen, QPixmap, QRadialGradient,
)
from PySide6.QtWidgets import QApplication, QMenu, QSystemTrayIcon, QWidget


if sys.platform != "win32":
    raise SystemExit("Radial Alt+Tab requires Windows.")

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
dwmapi = ctypes.WinDLL("dwmapi", use_last_error=True)

HWND = wintypes.HWND
LPARAM = wintypes.LPARAM
WNDPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, HWND, LPARAM)
HOOKPROC = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, ctypes.c_int, wintypes.WPARAM, LPARAM)


class KBDLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [
        ("vkCode", wintypes.DWORD), ("scanCode", wintypes.DWORD),
        ("flags", wintypes.DWORD), ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.c_size_t),
    ]


class MSG(ctypes.Structure):
    _fields_ = [
        ("hwnd", HWND), ("message", wintypes.UINT),
        ("wParam", wintypes.WPARAM), ("lParam", LPARAM),
        ("time", wintypes.DWORD), ("pt", wintypes.POINT),
        ("lPrivate", wintypes.DWORD),
    ]


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", wintypes.WORD), ("wScan", wintypes.WORD),
        ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.c_size_t),
    ]


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", wintypes.LONG), ("dy", wintypes.LONG),
        ("mouseData", wintypes.DWORD), ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.c_size_t),
    ]


class INPUTDATA(ctypes.Union):
    _fields_ = [("ki", KEYBDINPUT), ("mi", MOUSEINPUT)]


class INPUT(ctypes.Structure):
    _anonymous_ = ("data",)
    _fields_ = [("type", wintypes.DWORD), ("data", INPUTDATA)]


class DWM_THUMBNAIL_PROPERTIES(ctypes.Structure):
    _fields_ = [
        ("dwFlags", wintypes.DWORD), ("rcDestination", wintypes.RECT),
        ("rcSource", wintypes.RECT), ("opacity", ctypes.c_ubyte),
        ("fVisible", wintypes.BOOL), ("fSourceClientAreaOnly", wintypes.BOOL),
    ]


user32.EnumWindows.argtypes = [WNDPROC, LPARAM]
user32.GetWindowTextLengthW.argtypes = [HWND]
user32.GetWindowTextW.argtypes = [HWND, wintypes.LPWSTR, ctypes.c_int]
user32.GetWindowThreadProcessId.argtypes = [HWND, ctypes.POINTER(wintypes.DWORD)]
user32.GetWindowRect.argtypes = [HWND, ctypes.POINTER(wintypes.RECT)]
user32.GetWindowLongW.argtypes = [HWND, ctypes.c_int]
user32.GetClassLongPtrW.argtypes = [HWND, ctypes.c_int]
user32.GetClassLongPtrW.restype = ctypes.c_void_p
user32.SendMessageTimeoutW.argtypes = [HWND, wintypes.UINT, wintypes.WPARAM, LPARAM, wintypes.UINT, wintypes.UINT, ctypes.POINTER(ctypes.c_size_t)]
user32.GetForegroundWindow.restype = HWND
user32.SetForegroundWindow.argtypes = [HWND]
user32.SetForegroundWindow.restype = wintypes.BOOL
user32.ShowWindow.argtypes = [HWND, ctypes.c_int]
user32.IsIconic.argtypes = [HWND]
user32.IsWindow.argtypes = [HWND]
user32.SetWindowsHookExW.argtypes = [ctypes.c_int, HOOKPROC, wintypes.HINSTANCE, wintypes.DWORD]
user32.SetWindowsHookExW.restype = ctypes.c_void_p
user32.CallNextHookEx.argtypes = [ctypes.c_void_p, ctypes.c_int, wintypes.WPARAM, LPARAM]
user32.CallNextHookEx.restype = ctypes.c_ssize_t
user32.UnhookWindowsHookEx.argtypes = [ctypes.c_void_p]
user32.GetMessageW.argtypes = [ctypes.POINTER(MSG), HWND, wintypes.UINT, wintypes.UINT]
user32.GetMessageW.restype = ctypes.c_int
user32.TranslateMessage.argtypes = [ctypes.POINTER(MSG)]
user32.DispatchMessageW.argtypes = [ctypes.POINTER(MSG)]
user32.PostThreadMessageW.argtypes = [wintypes.DWORD, wintypes.UINT, wintypes.WPARAM, LPARAM]
user32.GetAsyncKeyState.argtypes = [ctypes.c_int]
user32.SendInput.argtypes = [wintypes.UINT, ctypes.POINTER(INPUT), ctypes.c_int]
user32.SendInput.restype = wintypes.UINT
kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
kernel32.GetModuleHandleW.restype = wintypes.HMODULE
kernel32.GetCurrentThreadId.restype = wintypes.DWORD
kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
kernel32.CreateMutexW.restype = wintypes.HANDLE
kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
dwmapi.DwmGetWindowAttribute.argtypes = [HWND, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD]
dwmapi.DwmRegisterThumbnail.argtypes = [HWND, HWND, ctypes.POINTER(ctypes.c_void_p)]
dwmapi.DwmUnregisterThumbnail.argtypes = [ctypes.c_void_p]
dwmapi.DwmUpdateThumbnailProperties.argtypes = [ctypes.c_void_p, ctypes.POINTER(DWM_THUMBNAIL_PROPERTIES)]

VK_TAB, VK_ESCAPE, VK_MENU, VK_OEM_3 = 0x09, 0x1B, 0x12, 0xC0
VK_LEFT, VK_UP, VK_RIGHT, VK_DOWN = 0x25, 0x26, 0x27, 0x28
VK_LMENU, VK_RMENU = 0xA4, 0xA5
WM_KEYDOWN, WM_KEYUP, WM_SYSKEYDOWN, WM_SYSKEYUP = 0x100, 0x101, 0x104, 0x105
WM_QUIT, WH_KEYBOARD_LL = 0x12, 13
WS_EX_TOOLWINDOW, WS_EX_APPWINDOW, WS_EX_NOACTIVATE = 0x80, 0x40000, 0x08000000
DWMWA_CLOAKED = 14
ERROR_ALREADY_EXISTS = 183


def activate_window(hwnd: int) -> bool:
    if not hwnd or not user32.IsWindow(hwnd):
        return False
    if user32.IsIconic(hwnd):
        user32.ShowWindow(hwnd, 9)
    if user32.GetForegroundWindow() == hwnd or user32.SetForegroundWindow(hwnd):
        return True
    # A background tray process can be denied foreground access; Alt unlocks it.
    keys = (INPUT * 2)()
    for key, flags in zip(keys, (0, 2)):
        key.type = 1
        key.ki = KEYBDINPUT(VK_MENU, 0, flags, 0, 0)
    return user32.SendInput(2, keys, ctypes.sizeof(INPUT)) == 2 and bool(user32.SetForegroundWindow(hwnd))


def window_title(hwnd: int) -> str:
    length = user32.GetWindowTextLengthW(hwnd)
    if not length:
        return ""
    buffer = ctypes.create_unicode_buffer(length + 1)
    user32.GetWindowTextW(hwnd, buffer, length + 1)
    return buffer.value.strip()


def windows(exclude: int = 0) -> list[tuple[int, str]]:
    found: list[tuple[int, str]] = []

    @WNDPROC
    def visit(hwnd: int, _unused: int) -> bool:
        if hwnd == exclude or not user32.IsWindowVisible(hwnd):
            return True
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if pid.value == os.getpid():
            return True
        style = user32.GetWindowLongW(hwnd, -20)
        if style & WS_EX_NOACTIVATE or style & WS_EX_TOOLWINDOW and not style & WS_EX_APPWINDOW:
            return True
        cloaked = wintypes.DWORD()
        if dwmapi.DwmGetWindowAttribute(hwnd, DWMWA_CLOAKED, ctypes.byref(cloaked), ctypes.sizeof(cloaked)) == 0 and cloaked.value:
            return True
        title = window_title(hwnd)
        rect = wintypes.RECT()
        rect_ok = user32.GetWindowRect(hwnd, ctypes.byref(rect))
        if title and (user32.IsIconic(hwnd) or rect_ok and rect.right - rect.left >= 80 and rect.bottom - rect.top >= 50):
            found.append((int(hwnd), title))
        return True

    user32.EnumWindows(visit, 0)
    return found


def pil_image_to_qimage(image: Image.Image) -> QImage:
    image = image.convert("RGBA")
    data = image.tobytes("raw", "RGBA")
    return QImage(data, image.width, image.height, image.width * 4, QImage.Format.Format_RGBA8888).copy()


def window_icon(hwnd: int) -> QPixmap | None:
    result = ctypes.c_size_t()
    for kind in (1, 2, 0):
        if user32.SendMessageTimeoutW(hwnd, 0x7F, kind, 0, 2, 100, ctypes.byref(result)) and result.value:
            break
    handle = result.value or user32.GetClassLongPtrW(hwnd, -14) or user32.GetClassLongPtrW(hwnd, -34)
    if handle:
        image = QImage.fromHICON(int(handle))
        if not image.isNull():
            return QPixmap.fromImage(image)
    return None


def demo_icon(title: str) -> QPixmap:
    colors = {
        "Chrome": ("#e6a23a", "●"), "VS Code": ("#2389d9", "⌁"),
        "微信": ("#56c889", "●"), "视频播放器": ("#e38e4e", "▶"),
        "文件资源管理器": ("#e5b950", "■"), "Photoshop": ("#255ca5", "Ps"),
        "终端": ("#404b68", ">_"), "设置": ("#8292b2", "⚙"),
    }
    color, glyph = colors[title]
    pixmap = QPixmap(28, 28)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor(color))
    painter.drawRoundedRect(2, 2, 24, 24, 7, 7)
    painter.setPen(QColor("#f9fbff"))
    painter.setFont(QFont("Segoe UI", 10 if len(glyph) > 1 else 13, QFont.Weight.Bold))
    painter.drawText(pixmap.rect(), Qt.AlignmentFlag.AlignCenter, glyph)
    painter.end()
    return pixmap


def demo_preview(title: str) -> QPixmap:
    """Small recognizable mock windows for the hook-free demo."""
    pixmap = QPixmap(320, 190)
    pixmap.fill(QColor("#101a29"))
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)

    def box(x: int, y: int, w: int, h: int, color: str, radius: int = 0) -> None:
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(color))
        painter.drawRoundedRect(x, y, w, h, radius, radius)

    light = title in ("Chrome", "微信", "文件资源管理器", "设置")
    box(0, 0, 320, 190, "#e8edf3" if light else "#182535")
    box(0, 0, 320, 25, "#f6f8fb" if light else "#26354a")
    for x, color in ((12, "#ed7b82"), (23, "#e5bd66"), (34, "#6dc8a2")):
        box(x, 10, 5, 5, color, 3)
    box(48, 10, 93, 5, "#c4ceda" if light else "#65778d", 2)

    if title in ("Chrome", "Photoshop", "视频播放器"):
        if title == "Chrome":
            box(11, 32, 298, 15, "#d7e1ec", 5)
            box(27, 37, 170, 5, "#f8fbff", 2)
            scene = QRect(12, 55, 296, 124)
        elif title == "Photoshop":
            box(0, 25, 30, 165, "#26374d")
            box(281, 25, 39, 165, "#26374d")
            scene = QRect(38, 37, 235, 137)
        else:
            scene = QRect(0, 25, 320, 165)
        sky = QLinearGradient(scene.topLeft(), scene.bottomLeft())
        sky.setColorAt(0, QColor("#607fa4"))
        sky.setColorAt(0.6, QColor("#c39a91"))
        sky.setColorAt(1, QColor("#303e5a"))
        painter.fillRect(scene, sky)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor("#344764"))
        ridge = QPainterPath()
        ridge.moveTo(scene.left(), scene.bottom() - 33)
        for fraction, height in ((0.13, 0.59), (0.27, 0.22), (0.39, 0.52), (0.57, 0.12), (0.73, 0.53), (0.86, 0.3), (1, 0.58)):
            ridge.lineTo(scene.left() + scene.width() * fraction, scene.top() + scene.height() * height)
        ridge.lineTo(scene.right(), scene.bottom())
        ridge.lineTo(scene.left(), scene.bottom())
        painter.drawPath(ridge)
        box(scene.left(), scene.bottom() - 23, scene.width(), 23, "#243a55")
        if title == "视频播放器":
            box(13, 165, 294, 3, "#8c9bb0", 2)
            box(13, 165, 118, 3, "#8cc9fb", 2)
            painter.setBrush(QColor(248, 250, 255, 220))
            path = QPainterPath()
            path.moveTo(148, 85)
            path.lineTo(148, 119)
            path.lineTo(180, 102)
            path.closeSubpath()
            painter.drawPath(path)
    elif title == "VS Code":
        box(0, 25, 58, 165, "#1c2a3b")
        for row in range(9):
            box(11 + row % 3 * 3, 40 + row * 16, 38 - row % 3 * 3, 4, "#556c85", 2)
        for row in range(13):
            y = 37 + row * 11
            box(75, y, 15, 3, "#51677e", 1)
            box(96, y, 38 + row % 4 * 13, 4, ("#75aace", "#c99fc2", "#d5ba8c")[row % 3], 1)
            box(170 + row % 4 * 13, y, 34, 4, "#8095a8", 1)
    elif title == "微信":
        box(0, 25, 112, 165, "#eef2f3")
        box(112, 25, 208, 165, "#f8fafb")
        for row in range(4):
            y = 39 + row * 34
            box(10, y, 21, 21, ("#68c69c", "#8eb2d5", "#e4ad78", "#9ba8c3")[row], 6)
            box(41, y + 3, 55, 4, "#8c9daa", 2)
            box(41, y + 12, 43, 3, "#cad3d8", 2)
        for x, y, w, color in ((144, 58, 116, "#e4e9eb"), (172, 95, 118, "#a8e6c4"), (141, 133, 96, "#e4e9eb")):
            box(x, y, w, 23, color, 6)
    elif title == "文件资源管理器":
        box(0, 25, 72, 165, "#e1e9f0")
        box(84, 36, 221, 15, "#f8fbfe", 5)
        for row in range(7):
            box(13, 45 + row * 19, 39 + row % 2 * 10, 4, "#aebdc9", 2)
        for index in range(8):
            x, y = 92 + index % 4 * 54, 68 + index // 4 * 52
            box(x, y + 4, 32, 22, "#f0bd58", 3)
            box(x + 2, y, 14, 7, "#f5cf78", 2)
            box(x, y + 31, 38, 3, "#b1bfcb", 1)
    elif title == "终端":
        for row in range(11):
            y = 39 + row * 13
            box(14, y, 10, 3, "#55c693", 1)
            box(31, y, 43 + row % 4 * 22, 3, ("#d0d8e4", "#8bc7e5", "#b7a6d7")[row % 3], 1)
            if row % 3 != 2:
                box(150, y, 28 + row % 3 * 15, 3, "#6a8198", 1)
    else:  # 设置
        box(0, 25, 84, 165, "#dce5ed")
        for row in range(7):
            box(12, 43 + row * 19, 48 + row % 3 * 5, 4, "#a7b8c8", 2)
        for row in range(4):
            y = 47 + row * 33
            box(104, y, 142, 5, "#889bab", 2)
            box(104, y + 11, 108, 3, "#c4d0d9", 2)
            box(269, y, 29, 15, "#68a7d8" if row % 2 == 0 else "#bbc8d1", 8)
    painter.end()
    return pixmap


class HookEvents(QObject):
    event = Signal(str)


class KeyboardHook(threading.Thread):
    def __init__(self, events: HookEvents):
        super().__init__(daemon=True)
        self.events = events
        self.thread_id = 0
        self.handle = None
        self.callback = None
        self.engaged = False
        self.installed = threading.Event()
        self.error = ""

    def run(self) -> None:
        self.thread_id = kernel32.GetCurrentThreadId()

        @HOOKPROC
        def callback(code: int, message: int, data: int) -> int:
            if code >= 0:
                event = ctypes.cast(data, ctypes.POINTER(KBDLLHOOKSTRUCT)).contents
                key = event.vkCode
                down = message in (WM_KEYDOWN, WM_SYSKEYDOWN)
                up = message in (WM_KEYUP, WM_SYSKEYUP)
                if key == VK_TAB and (self.engaged or event.flags & 0x20 or user32.GetAsyncKeyState(VK_MENU) & 0x8000):
                    if down:
                        first = not self.engaged
                        self.engaged = True
                        self.events.event.emit("open" if first else "next")
                    return 1
                if key == VK_OEM_3 and (self.engaged or user32.GetAsyncKeyState(VK_MENU) & 0x8000):
                    if down:
                        first = not self.engaged
                        self.engaged = True
                        self.events.event.emit("open_previous" if first else "previous")
                    return 1
                if self.engaged:
                    if key in (VK_MENU, VK_LMENU, VK_RMENU) and up:
                        self.engaged = False
                        self.events.event.emit("commit")
                    elif key == VK_ESCAPE:
                        if down:
                            self.engaged = False
                            self.events.event.emit("cancel")
                        return 1
                    elif key in (VK_LEFT, VK_UP, VK_RIGHT, VK_DOWN):
                        if down:
                            self.events.event.emit({VK_LEFT: "left", VK_UP: "up", VK_RIGHT: "right", VK_DOWN: "down"}[key])
                        return 1
            return user32.CallNextHookEx(self.handle, code, message, data)

        self.callback = callback
        self.handle = user32.SetWindowsHookExW(WH_KEYBOARD_LL, callback, kernel32.GetModuleHandleW(None), 0)
        if not self.handle:
            self.error = f"Keyboard hook failed: {ctypes.get_last_error()}"
            self.installed.set()
            return
        self.installed.set()
        message = MSG()
        while user32.GetMessageW(ctypes.byref(message), None, 0, 0) > 0:
            user32.TranslateMessage(ctypes.byref(message))
            user32.DispatchMessageW(ctypes.byref(message))
        user32.UnhookWindowsHookEx(self.handle)
        self.handle = None

    def stop(self) -> None:
        if self.thread_id and self.handle:
            user32.PostThreadMessageW(self.thread_id, WM_QUIT, 0, 0)


class RadialOverlay(QWidget):
    def __init__(self, demo: bool = False):
        super().__init__(None, Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.Tool)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAccessibleDescription("按 Tab 选择下一个窗口，按 ·/~ 选择上一个窗口，松开 Alt 切换，按 Esc 取消")
        self.demo = demo
        self.items: list[tuple[int, str]] = []
        self.icons: dict[int, QPixmap] = {}
        self.demo_icons: dict[str, QPixmap] = {}
        self.demo_previews: dict[str, QPixmap] = {}
        self.thumbnails: dict[int, ctypes.c_void_p] = {}
        self.selected = 0
        self.source_hwnd = 0
        self.background: QPixmap | None = None
        self.open_animation = QPropertyAnimation(self, b"windowOpacity")
        self.open_animation.setDuration(190)
        self.open_animation.setEasingCurve(QEasingCurve.Type.OutCubic)

    def open(self, previous: bool = False) -> None:
        if self.isVisible():
            return
        source = int(user32.GetForegroundWindow() or 0)
        self.source_hwnd = source
        if self.demo:
            self.items = [(0, name) for name in ["Chrome", "VS Code", "微信", "视频播放器", "文件资源管理器", "Photoshop", "终端", "设置"]]
        else:
            self.items = windows()
        if not self.items:
            return
        screen = QGuiApplication.screenAt(QCursor.pos()) or QGuiApplication.primaryScreen()
        if source:
            rect = wintypes.RECT()
            if user32.GetWindowRect(source, ctypes.byref(rect)):
                active_screen = QGuiApplication.screenAt(QPoint((rect.left + rect.right) // 2, (rect.top + rect.bottom) // 2))
                screen = active_screen or screen
        geometry = screen.geometry()
        self.setGeometry(geometry)
        source_index = next((i for i, (hwnd, _) in enumerate(self.items) if hwnd == source), None)
        if source_index is not None and not self.demo:
            self.selected = (source_index + (-1 if previous else 1)) % len(self.items)
        else:
            self.selected = len(self.items) - 1 if previous else 0
        self.announce_selection()
        self.icons = {hwnd: icon for hwnd, _ in self.items if hwnd and (icon := window_icon(hwnd)) is not None}
        if self.demo:
            self.demo_icons = {title: demo_icon(title) for _, title in self.items}
            self.demo_previews = {title: demo_preview(title) for _, title in self.items}
        self.background = None
        if not self.demo:
            try:
                screenshot = screen.grabWindow(0).toImage().convertToFormat(QImage.Format.Format_RGBA8888)
                image = Image.frombytes("RGBA", (screenshot.width(), screenshot.height()), screenshot.bits().tobytes())
                image = image.resize((max(1, image.width // 2), max(1, image.height // 2)), Image.Resampling.BILINEAR)
                self.background = QPixmap.fromImage(pil_image_to_qimage(image.filter(ImageFilter.GaussianBlur(8))))
            except (OSError, ValueError):
                pass
        self.setWindowOpacity(0)
        self.show()
        self.raise_()
        self.activateWindow()
        self.setFocus()
        self.open_animation.stop()
        self.open_animation.setStartValue(0)
        self.open_animation.setEndValue(1)
        self.open_animation.start()
        self.sync_thumbnails()

    def thumbnail_rect(self, local: int, count: int, selected: bool) -> QRect:
        outer, inner, center = self.radii()
        angle = -math.pi / 2 + local * 2 * math.pi / count
        radial = inner + (outer - inner) * 0.52
        x = center.x() + radial * math.cos(angle)
        y = center.y() + radial * math.sin(angle)
        width = min(260, radial * 2 * math.pi / count * 0.8) * (1.08 if selected else 1)
        height = width * 0.60
        return QRect(int(x - width / 2), int(y - height / 2), int(width), int(height))

    def sync_thumbnails(self) -> None:
        if self.demo or not self.isVisible():
            return
        visible = self.items
        handles = {hwnd for hwnd, _ in visible}
        for hwnd in list(self.thumbnails):
            if hwnd not in handles or not user32.IsWindow(hwnd):
                dwmapi.DwmUnregisterThumbnail(self.thumbnails.pop(hwnd))
        scale = self.devicePixelRatioF()
        for local, (hwnd, _) in enumerate(visible):
            if hwnd not in self.thumbnails:
                handle = ctypes.c_void_p()
                if dwmapi.DwmRegisterThumbnail(int(self.winId()), hwnd, ctypes.byref(handle)) != 0:
                    continue
                self.thumbnails[hwnd] = handle
            rect = self.thumbnail_rect(local, len(visible), local == self.selected).adjusted(2, 2, -2, -2)
            props = DWM_THUMBNAIL_PROPERTIES()
            props.dwFlags = 0x1 | 0x4 | 0x8
            props.rcDestination = wintypes.RECT(round(rect.left() * scale), round(rect.top() * scale),
                                                 round((rect.right() + 1) * scale), round((rect.bottom() + 1) * scale))
            props.opacity = 255
            props.fVisible = True
            if dwmapi.DwmUpdateThumbnailProperties(self.thumbnails[hwnd], ctypes.byref(props)) != 0:
                dwmapi.DwmUnregisterThumbnail(self.thumbnails.pop(hwnd))

    def hideEvent(self, event) -> None:
        for handle in self.thumbnails.values():
            dwmapi.DwmUnregisterThumbnail(handle)
        self.thumbnails.clear()
        super().hideEvent(event)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self.sync_thumbnails()

    def announce_selection(self) -> None:
        if self.items:
            self.setAccessibleName(f"环形窗口切换器，已选中 {self.items[self.selected][1]}")

    def move_selection(self, step: int) -> None:
        if self.items and self.isVisible():
            self.selected = (self.selected + step) % len(self.items)
            self.announce_selection()
            self.update()
            self.sync_thumbnails()

    def direction(self, direction: str) -> None:
        if not self.isVisible():
            return
        vectors = {"up": -math.pi / 2, "right": 0, "down": math.pi / 2, "left": math.pi}
        count = len(self.items)
        angle = vectors[direction]
        self.selected = min(range(count), key=lambda i: abs(math.atan2(math.sin(-math.pi / 2 + i * 2 * math.pi / count - angle), math.cos(-math.pi / 2 + i * 2 * math.pi / count - angle))))
        self.announce_selection()
        self.update()
        self.sync_thumbnails()

    def commit(self) -> None:
        if not self.isVisible():
            return
        hwnd = self.items[self.selected][0]
        self.hide()
        activate_window(hwnd)

    def cancel(self) -> None:
        self.hide()
        activate_window(self.source_hwnd)

    def handle(self, action: str) -> None:
        if action == "open":
            self.open()
        elif action == "open_previous":
            self.open(previous=True)
        elif action == "next":
            self.move_selection(1)
        elif action == "previous":
            self.move_selection(-1)
        elif action == "commit":
            self.commit()
        elif action == "cancel":
            self.cancel()
        elif action in ("up", "right", "down", "left"):
            self.direction(action)

    def radii(self) -> tuple[float, float, QPoint]:
        outer = min(350, self.width() * 0.31, self.height() * 0.42)
        return outer, outer * 0.34, QPoint(self.width() // 2, self.height() // 2)

    def sector_at(self, point: QPoint) -> int | None:
        outer, inner, center = self.radii()
        x, y = point.x() - center.x(), point.y() - center.y()
        distance = math.hypot(x, y)
        if not inner < distance < outer:
            return None
        count = len(self.items)
        angle = (math.atan2(y, x) + math.pi / 2) % (2 * math.pi)
        if count == 1 and min(angle, 2 * math.pi - angle) > math.pi / 3:
            return None
        return round(angle / (2 * math.pi / count)) % count

    def mouseMoveEvent(self, event) -> None:
        index = self.sector_at(event.position().toPoint())
        if index is not None and index != self.selected:
            self.selected = index
            self.announce_selection()
            self.update()
            self.sync_thumbnails()

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            index = self.sector_at(event.position().toPoint())
            if index is None:
                self.cancel()
            else:
                self.selected = index
                self.commit()

    def wheelEvent(self, event) -> None:
        self.move_selection(-1 if event.angleDelta().y() > 0 else 1)

    def keyPressEvent(self, event) -> None:
        key = event.key()
        if key == Qt.Key.Key_Escape:
            self.cancel()
        elif key == Qt.Key.Key_Tab:
            self.move_selection(1)
        elif key == Qt.Key.Key_QuoteLeft and event.modifiers() & Qt.KeyboardModifier.AltModifier:
            self.move_selection(-1)
        elif key in (Qt.Key.Key_Left, Qt.Key.Key_Right, Qt.Key.Key_Up, Qt.Key.Key_Down):
            self.direction({Qt.Key.Key_Left: "left", Qt.Key.Key_Right: "right", Qt.Key.Key_Up: "up", Qt.Key.Key_Down: "down"}[key])
        elif key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self.commit()

    def paintEvent(self, _event) -> None:
        if not self.items:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        if self.background:
            painter.drawPixmap(self.rect(), self.background, self.background.rect())
        else:
            gradient = QLinearGradient(0, 0, self.width(), self.height())
            gradient.setColorAt(0, QColor("#27354f"))
            gradient.setColorAt(0.48, QColor("#111d30"))
            gradient.setColorAt(1, QColor("#091420"))
            painter.fillRect(self.rect(), gradient)
            glow = QRadialGradient(QPointF(self.width() * .51, self.height() * .42), self.width() * .51)
            glow.setColorAt(0, QColor(65, 102, 151, 38))
            glow.setColorAt(1, QColor(65, 102, 151, 0))
            painter.fillRect(self.rect(), glow)
        painter.fillRect(self.rect(), QColor(3, 9, 18, 174))
        outer, inner, center = self.radii()
        visible = self.items
        count = len(visible)
        centerf = QPointF(center)
        for local, (hwnd, title) in enumerate(visible):
            angle = -math.pi / 2 + local * 2 * math.pi / count
            half = math.pi / 3 if count == 1 else math.pi / count - 0.012
            path = QPainterPath()
            path.moveTo(center.x() + outer * math.cos(angle - half), center.y() + outer * math.sin(angle - half))
            path.arcTo(QRectF(center.x() - outer, center.y() - outer, outer * 2, outer * 2),
                       -math.degrees(angle - half), -math.degrees(half * 2))
            path.lineTo(center.x() + inner * math.cos(angle + half), center.y() + inner * math.sin(angle + half))
            path.arcTo(QRectF(center.x() - inner, center.y() - inner, inner * 2, inner * 2),
                       -math.degrees(angle + half), math.degrees(half * 2))
            path.closeSubpath()
            selected = local == self.selected
            if selected:
                painter.setPen(QPen(QColor(78, 175, 255, 38), 18))
                painter.drawPath(path)
                painter.setPen(QPen(QColor(91, 187, 255, 86), 8))
                painter.drawPath(path)
            fill = QRadialGradient(centerf, outer)
            if selected:
                fill.setColorAt(0, QColor(18, 54, 88, 240))
                fill.setColorAt(1, QColor(36, 91, 143, 232))
            else:
                fill.setColorAt(0, QColor(27, 40, 59, 226))
                fill.setColorAt(1, QColor(32, 45, 65, 211))
            painter.setBrush(fill)
            painter.setPen(QPen(QColor(141, 210, 255, 235) if selected else QColor(180, 199, 222, 62), 2 if selected else 1))
            painter.drawPath(path)

            thumb_rect = self.thumbnail_rect(local, count, selected)
            shadow_rect = thumb_rect.translated(0, 6)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(1, 7, 15, 125))
            painter.drawRoundedRect(shadow_rect, 7, 7)
            painter.save()
            clip = QPainterPath()
            clip.addRoundedRect(thumb_rect, 7, 7)
            painter.setClipPath(clip)
            painter.fillRect(thumb_rect, QColor("#0b1422"))
            preview = self.demo_previews.get(title) if self.demo else None
            if preview:
                painter.drawPixmap(thumb_rect, preview, preview.rect())
            else:
                self.draw_placeholder(painter, thumb_rect, title, self.icons.get(hwnd), bool(hwnd and user32.IsIconic(hwnd)))
            if not selected:
                painter.fillRect(thumb_rect, QColor(2, 8, 17, 65))
            painter.restore()
            painter.setPen(QPen(QColor(147, 215, 255, 235) if selected else QColor(206, 229, 246, 119), 1.5 if selected else 1))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRoundedRect(thumb_rect, 7, 7)

            label_x = thumb_rect.center().x()
            label_y = thumb_rect.bottom() + 18 if math.sin(angle) > 0.1 else thumb_rect.top() - 14
            font = QFont("Microsoft YaHei UI", 10)
            font.setWeight(QFont.Weight.DemiBold if selected else QFont.Weight.Normal)
            painter.setFont(font)
            metrics = QFontMetrics(font)
            text = metrics.elidedText(title, Qt.TextElideMode.ElideRight, thumb_rect.width() + 12)
            icon = self.demo_icons.get(title) if self.demo else self.icons.get(hwnd)
            text_width = metrics.horizontalAdvance(text)
            start_x = label_x - (text_width + (26 if icon else 0)) / 2
            if icon:
                painter.drawPixmap(QRect(int(start_x), int(label_y - 11), 20, 20), icon)
                start_x += 26
            painter.setPen(QColor("#f5f9ff") if selected else QColor("#d7e0eb"))
            painter.drawText(QPoint(int(start_x), int(label_y + 5)), text)

            badge_angle = angle - half * 0.65
            badge_radius = outer - 16
            badge_x = center.x() + badge_radius * math.cos(badge_angle)
            badge_y = center.y() + badge_radius * math.sin(badge_angle)
            painter.setPen(QPen(QColor(195, 225, 249, 80) if selected else QColor(195, 225, 249, 34), 1))
            painter.setBrush(QColor(158, 217, 255, 165) if selected else QColor(113, 135, 165, 65))
            painter.drawEllipse(QPointF(badge_x, badge_y), 11, 11)
            painter.setPen(QColor("#10243b") if selected else QColor("#d3dfef"))
            painter.setFont(QFont("Segoe UI", 8, QFont.Weight.DemiBold))
            painter.drawText(QRectF(badge_x - 10, badge_y - 10, 20, 20), Qt.AlignmentFlag.AlignCenter, str(local + 1))

        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(QColor(85, 172, 247, 24), 18))
        painter.drawEllipse(center, int(inner - 7), int(inner - 7))
        core = QRadialGradient(centerf, inner)
        core.setColorAt(0, QColor("#142740"))
        core.setColorAt(0.75, QColor("#0b1729"))
        core.setColorAt(1, QColor("#07111f"))
        painter.setBrush(core)
        painter.setPen(QPen(QColor(125, 190, 253, 205), 1.6))
        painter.drawEllipse(center, int(inner - 8), int(inner - 8))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor("#b6c8e1"))
        painter.drawRoundedRect(QRect(center.x() - 13, center.y() - 36, 23, 20), 3, 3)
        painter.setBrush(QColor("#e1ebfa"))
        painter.drawRoundedRect(QRect(center.x() - 4, center.y() - 29, 23, 20), 3, 3)
        title_rect = QRect(center.x() - int(inner - 12), center.y() - int(inner - 8),
                           int((inner - 12) * 2), int(inner * 0.46))
        title_flags = Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap
        font = QFont("Microsoft YaHei UI", 10, QFont.Weight.DemiBold)
        for size in range(10, 6, -1):
            font.setPointSize(size)
            if QFontMetrics(font).boundingRect(title_rect, title_flags, self.items[self.selected][1]).height() <= title_rect.height():
                break
        painter.setFont(font)
        painter.setPen(QColor("#a9c7e2"))
        painter.drawText(title_rect, title_flags, self.items[self.selected][1])
        mouse_footer = "鼠标选择切换"
        keyboard_footer = "     松开 ALT  切换     TAB  下一个     ·/~  上一个     ESC  取消"
        footer_font = QFont("Microsoft YaHei UI", 9)
        mouse_font = QFont(footer_font)
        mouse_font.setWeight(QFont.Weight.DemiBold)
        mouse_width = QFontMetrics(mouse_font).horizontalAdvance(mouse_footer)
        keyboard_width = QFontMetrics(footer_font).horizontalAdvance(keyboard_footer)
        footer_x = (self.width() - mouse_width - keyboard_width) // 2
        footer_y = int(center.y() + outer + 18)
        painter.setFont(mouse_font)
        painter.setPen(QColor("#d7f2ff"))
        painter.drawText(QRect(footer_x, footer_y, mouse_width, 32), Qt.AlignmentFlag.AlignLeft, mouse_footer)
        painter.setFont(footer_font)
        painter.setPen(QColor("#a9b9cf"))
        painter.drawText(QRect(footer_x + mouse_width, footer_y, keyboard_width, 32), Qt.AlignmentFlag.AlignLeft, keyboard_footer)

    @staticmethod
    def draw_placeholder(painter: QPainter, rect: QRect, title: str, icon: QPixmap | None, minimized: bool) -> None:
        gradient = QLinearGradient(rect.topLeft(), rect.bottomRight())
        gradient.setColorAt(0, QColor("#273e5b"))
        gradient.setColorAt(1, QColor("#101b2d"))
        painter.fillRect(rect, gradient)
        painter.fillRect(QRect(rect.x(), rect.y(), rect.width(), 12), QColor(224, 236, 249, 40))
        size = min(40, rect.height() // 2)
        symbol = QRect(rect.center().x() - size // 2, rect.y() + 15, size, size)
        painter.setPen(QColor("#b9d6ef"))
        if icon:
            painter.drawPixmap(symbol, icon)
        else:
            painter.setFont(QFont("Microsoft YaHei UI", 16, QFont.Weight.Bold))
            painter.drawText(symbol, Qt.AlignmentFlag.AlignCenter, title[:1].upper())
        painter.setFont(QFont("Microsoft YaHei UI", 8))
        painter.drawText(QRect(rect.x() + 4, rect.bottom() - 21, rect.width() - 8, 17),
                         Qt.AlignmentFlag.AlignCenter, "已最小化" if minimized else "预览不可用")


def tray_icon() -> QIcon:
    pixmap = QPixmap(64, 64)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setBrush(QColor("#142944"))
    painter.setPen(QPen(QColor("#7bc5ff"), 5))
    painter.drawEllipse(6, 6, 52, 52)
    painter.setPen(QColor("#f5f9ff"))
    painter.setFont(QFont("Segoe UI", 23, QFont.Weight.Bold))
    painter.drawText(pixmap.rect(), Qt.AlignmentFlag.AlignCenter, "↹")
    painter.end()
    return QIcon(pixmap)


def self_test() -> None:
    app = QApplication.instance() or QApplication([])
    assert ctypes.sizeof(INPUT) == (40 if ctypes.sizeof(ctypes.c_void_p) == 8 else 28)
    assert not activate_window(0)
    name = f"Local\\RadialAltTab.SelfTest.{os.getpid()}"
    first = kernel32.CreateMutexW(None, False, name)
    second = kernel32.CreateMutexW(None, False, name)
    duplicate_error = ctypes.get_last_error()
    try:
        assert first and second and duplicate_error == ERROR_ALREADY_EXISTS
    finally:
        if second:
            kernel32.CloseHandle(second)
        if first:
            kernel32.CloseHandle(first)
    overlay = RadialOverlay(demo=True)
    overlay.items = [(0, str(i)) for i in range(11)]
    overlay.resize(1200, 800)
    assert overlay.sector_at(QPoint(600, 100)) == 0
    assert overlay.sector_at(QPoint(600, 400)) is None
    overlay.selected = 8
    assert overlay.sector_at(QPoint(600, 100)) == 0
    assert overlay.thumbnail_rect(0, 8, True).width() > overlay.thumbnail_rect(0, 8, False).width()
    overlay.open(previous=True)
    assert overlay.selected == 7
    overlay.cancel()
    source = QWidget()
    source.resize(300, 200)
    source.show()
    test_overlay = RadialOverlay()
    test_overlay.setGeometry(100, 100, 800, 600)
    test_overlay.items = [(int(source.winId()), "DWM test")]
    try:
        test_overlay.show()
        app.processEvents()
        test_overlay.sync_thumbnails()
        assert len(test_overlay.thumbnails) == 1, "DWM thumbnail registration failed"
    finally:
        test_overlay.hide()
        source.close()
    assert not test_overlay.thumbnails, "DWM thumbnails were not released"
    hook = KeyboardHook(HookEvents())
    hook.start()
    assert hook.installed.wait(2), "Hook did not start"
    assert not hook.error, hook.error
    hook.stop()
    hook.join(2)
    assert not hook.is_alive(), "Hook did not stop"
    print("Self-test passed")


def main() -> None:
    parser = argparse.ArgumentParser(description="Radial Alt+Tab for Windows")
    parser.add_argument("--demo", action="store_true", help="show a sample ring without installing a keyboard hook")
    parser.add_argument("--snapshot", metavar="PNG", help="save a demo screenshot and exit")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    mutex = None
    if not (args.demo or args.snapshot or args.self_test):
        mutex = kernel32.CreateMutexW(None, False, "Local\\RadialAltTab.SingleInstance")
        if not mutex:
            raise SystemExit(f"Single-instance mutex failed: {ctypes.get_last_error()}")
        if ctypes.get_last_error() == ERROR_ALREADY_EXISTS:
            kernel32.CloseHandle(mutex)
            return
    app = QApplication(sys.argv[:1])
    app.setQuitOnLastWindowClosed(False)
    if args.self_test:
        self_test()
        return
    overlay = RadialOverlay(demo=args.demo or bool(args.snapshot))
    if args.snapshot:
        overlay.open()
        QTimer.singleShot(300, lambda: (overlay.grab().save(args.snapshot), app.quit()))
        app.exec()
        return
    if args.demo:
        QTimer.singleShot(0, overlay.open)
    else:
        events = HookEvents()
        events.event.connect(overlay.handle)
        hook = KeyboardHook(events)
        hook.start()
        hook.installed.wait(2)
        if hook.error:
            raise SystemExit(hook.error)
        app.aboutToQuit.connect(hook.stop)
    tray = QSystemTrayIcon(tray_icon(), app)
    menu = QMenu()
    menu.addAction("显示切换器", overlay.open)
    menu.addAction("退出", app.quit)
    tray.setContextMenu(menu)
    tray.setToolTip("Radial Alt+Tab")
    tray.show()
    try:
        app.exec()
    finally:
        if mutex:
            hook.stop()
            hook.join(2)
            kernel32.CloseHandle(mutex)


if __name__ == "__main__":
    main()
