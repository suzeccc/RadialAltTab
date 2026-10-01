"""A small Windows radial Alt+Tab switcher."""

from __future__ import annotations

import argparse
import ctypes
from ctypes import wintypes
import json
import math
import os
import re
from queue import Empty, SimpleQueue
import subprocess
import sys
import threading
import urllib.request
import webbrowser

from PIL import Image, ImageFilter
from PySide6.QtCore import QEvent, QObject, QPoint, QPointF, QRect, QRectF, QSize, Qt, QTimer, Signal, QVariantAnimation
from PySide6.QtCore import QSettings
from PySide6.QtCore import QEasingCurve, QPropertyAnimation
from PySide6.QtGui import (
    QActionGroup, QColor, QCursor, QFont, QFontMetrics, QGuiApplication, QIcon,
    QImage, QLinearGradient, QPainter, QPainterPath, QPen, QPixmap, QRadialGradient,
)
from PySide6.QtWidgets import (
    QApplication, QDialog, QDialogButtonBox, QFileDialog, QFormLayout,
    QHBoxLayout,
    QCheckBox, QLabel, QLineEdit, QListWidget, QListWidgetItem, QMessageBox, QPushButton, QMenu,
    QSystemTrayIcon, QVBoxLayout, QWidget,
)


if sys.platform != "win32":
    raise SystemExit("Radial Alt+Tab requires Windows.")

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
dwmapi = ctypes.WinDLL("dwmapi", use_last_error=True)
shell32 = ctypes.WinDLL("shell32", use_last_error=True)

HWND = wintypes.HWND
LPARAM = wintypes.LPARAM
WNDPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, HWND, LPARAM)
HOOKPROC = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, ctypes.c_int, wintypes.WPARAM, LPARAM)
WINEVENTPROC = ctypes.WINFUNCTYPE(
    None, wintypes.HANDLE, wintypes.DWORD, HWND, wintypes.LONG, wintypes.LONG,
    wintypes.DWORD, wintypes.DWORD,
)


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


class ACCENT_POLICY(ctypes.Structure):
    _fields_ = [("state", ctypes.c_int), ("flags", ctypes.c_int),
                ("color", ctypes.c_int), ("animation", ctypes.c_int)]


class WINDOWCOMPOSITIONATTRIBDATA(ctypes.Structure):
    _fields_ = [("attribute", ctypes.c_int), ("data", ctypes.c_void_p),
                ("size", ctypes.c_size_t)]


user32.EnumWindows.argtypes = [WNDPROC, LPARAM]
user32.GetWindowTextLengthW.argtypes = [HWND]
user32.GetWindowTextW.argtypes = [HWND, wintypes.LPWSTR, ctypes.c_int]
user32.GetWindowThreadProcessId.argtypes = [HWND, ctypes.POINTER(wintypes.DWORD)]
user32.GetWindowRect.argtypes = [HWND, ctypes.POINTER(wintypes.RECT)]
user32.GetWindow.argtypes = [HWND, wintypes.UINT]
user32.GetWindow.restype = HWND
user32.GetWindowLongW.argtypes = [HWND, ctypes.c_int]
user32.IsWindowEnabled.argtypes = [HWND]
user32.GetClassLongPtrW.argtypes = [HWND, ctypes.c_int]
user32.GetClassLongPtrW.restype = ctypes.c_void_p
user32.SendMessageTimeoutW.argtypes = [HWND, wintypes.UINT, wintypes.WPARAM, LPARAM, wintypes.UINT, wintypes.UINT, ctypes.POINTER(ctypes.c_size_t)]
user32.GetForegroundWindow.restype = HWND
user32.SetForegroundWindow.argtypes = [HWND]
user32.SetForegroundWindow.restype = wintypes.BOOL
user32.SwitchToThisWindow.argtypes = [HWND, wintypes.BOOL]
user32.SwitchToThisWindow.restype = None
user32.ShowWindow.argtypes = [HWND, ctypes.c_int]
user32.IsIconic.argtypes = [HWND]
user32.IsWindow.argtypes = [HWND]
user32.IsWindowVisible.argtypes = [HWND]
user32.SetWindowCompositionAttribute.argtypes = [HWND, ctypes.POINTER(WINDOWCOMPOSITIONATTRIBDATA)]
user32.SetWindowCompositionAttribute.restype = wintypes.BOOL
user32.SetWindowsHookExW.argtypes = [ctypes.c_int, HOOKPROC, wintypes.HINSTANCE, wintypes.DWORD]
user32.SetWindowsHookExW.restype = ctypes.c_void_p
user32.CallNextHookEx.argtypes = [ctypes.c_void_p, ctypes.c_int, wintypes.WPARAM, LPARAM]
user32.CallNextHookEx.restype = ctypes.c_ssize_t
user32.UnhookWindowsHookEx.argtypes = [ctypes.c_void_p]
user32.SetWinEventHook.argtypes = [wintypes.DWORD, wintypes.DWORD, wintypes.HMODULE,
                                   WINEVENTPROC, wintypes.DWORD, wintypes.DWORD, wintypes.DWORD]
user32.SetWinEventHook.restype = wintypes.HANDLE
user32.UnhookWinEvent.argtypes = [wintypes.HANDLE]
user32.UnhookWinEvent.restype = wintypes.BOOL
user32.GetMessageW.argtypes = [ctypes.POINTER(MSG), HWND, wintypes.UINT, wintypes.UINT]
user32.GetMessageW.restype = ctypes.c_int
user32.SetTimer.argtypes = [HWND, ctypes.c_size_t, wintypes.UINT, ctypes.c_void_p]
user32.SetTimer.restype = ctypes.c_size_t
user32.KillTimer.argtypes = [HWND, ctypes.c_size_t]
user32.TranslateMessage.argtypes = [ctypes.POINTER(MSG)]
user32.DispatchMessageW.argtypes = [ctypes.POINTER(MSG)]
user32.PostThreadMessageW.argtypes = [wintypes.DWORD, wintypes.UINT, wintypes.WPARAM, LPARAM]
user32.PostMessageW.argtypes = [HWND, wintypes.UINT, wintypes.WPARAM, LPARAM]
user32.PostMessageW.restype = wintypes.BOOL
user32.SendInput.argtypes = [wintypes.UINT, ctypes.POINTER(INPUT), ctypes.c_int]
user32.SendInput.restype = wintypes.UINT
kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
kernel32.OpenProcess.restype = wintypes.HANDLE
kernel32.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
kernel32.QueryFullProcessImageNameW.restype = wintypes.BOOL
kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
kernel32.GetModuleHandleW.restype = wintypes.HMODULE
kernel32.GetCurrentThreadId.restype = wintypes.DWORD
kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
kernel32.CreateMutexW.restype = wintypes.HANDLE
kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
dwmapi.DwmGetWindowAttribute.argtypes = [HWND, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD]
dwmapi.DwmSetWindowAttribute.argtypes = [HWND, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD]
dwmapi.DwmSetWindowAttribute.restype = ctypes.c_long
dwmapi.DwmRegisterThumbnail.argtypes = [HWND, HWND, ctypes.POINTER(ctypes.c_void_p)]
dwmapi.DwmUnregisterThumbnail.argtypes = [ctypes.c_void_p]
dwmapi.DwmUpdateThumbnailProperties.argtypes = [ctypes.c_void_p, ctypes.POINTER(DWM_THUMBNAIL_PROPERTIES)]
version = ctypes.WinDLL("version", use_last_error=True)
version.GetFileVersionInfoSizeW.argtypes = [wintypes.LPCWSTR, ctypes.POINTER(wintypes.DWORD)]
version.GetFileVersionInfoSizeW.restype = wintypes.DWORD
version.GetFileVersionInfoW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p]
version.GetFileVersionInfoW.restype = wintypes.BOOL
version.VerQueryValueW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR,
                                   ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(wintypes.UINT)]
version.VerQueryValueW.restype = wintypes.BOOL
shell32.SetCurrentProcessExplicitAppUserModelID.argtypes = [wintypes.LPCWSTR]
shell32.SetCurrentProcessExplicitAppUserModelID.restype = ctypes.c_long
shell32.IsUserAnAdmin.restype = wintypes.BOOL
shell32.ShellExecuteW.argtypes = [HWND, wintypes.LPCWSTR, wintypes.LPCWSTR,
                                   wintypes.LPCWSTR, wintypes.LPCWSTR, ctypes.c_int]
shell32.ShellExecuteW.restype = ctypes.c_void_p

VK_TAB, VK_ESCAPE, VK_MENU, VK_OEM_3, VK_DELETE, VK_F24 = 0x09, 0x1B, 0x12, 0xC0, 0x2E, 0x87
VK_LMENU, VK_RMENU = 0xA4, 0xA5
WM_KEYDOWN, WM_KEYUP, WM_SYSKEYDOWN, WM_SYSKEYUP = 0x100, 0x101, 0x104, 0x105
WM_CLOSE, WM_SYSCOMMAND, WM_QUIT, WM_TIMER, WM_REHOOK, WH_KEYBOARD_LL = 0x10, 0x112, 0x12, 0x113, 0x8001, 13
SC_CLOSE = 0xF060
HOOK_PROBE = 0x52415442
LLKHF_ALTDOWN = 0x20
EVENT_SYSTEM_FOREGROUND = 0x0003
WINEVENT_OUTOFCONTEXT = 0x0000
WINEVENT_SKIPOWNPROCESS = 0x0002
OBJID_WINDOW = 0
CHILDID_SELF = 0
WS_EX_TOOLWINDOW, WS_EX_APPWINDOW, WS_EX_NOACTIVATE = 0x80, 0x40000, 0x08000000
DWMWA_CLOAKED = 14
ERROR_ALREADY_EXISTS = 183
APP_NAME = "RadialAltTab"
# ponytail: bump with each release; the release workflow rejects mismatched tags.
APP_VERSION = "v1.2.0"
LANGUAGES = ("zh_CN", "zh_TW", "en")
TRANSLATIONS = {
    "简体中文": ("简体中文", "簡體中文", "Simplified Chinese"),
    "繁體中文": ("繁體中文", "繁體中文", "Traditional Chinese"),
    "English": ("English", "English", "English"),
    "是": ("是", "是", "Yes"),
    "否": ("否", "否", "No"),
    "语言": ("语言", "語言", "Language"),
    "显示切换": ("显示切换", "顯示切換", "Show switcher"),
    "窗口映射": ("窗口映射", "視窗映射", "Window mapping"),
    "背景模糊": ("背景模糊", "背景模糊", "Background effect"),
    "主题颜色": ("主题颜色", "主題顏色", "Theme"),
    "检查更新": ("检查更新", "檢查更新", "Check for updates"),
    "开机启动": ("开机启动", "開機啟動", "Run at startup"),
    "管理员运行": ("管理员运行", "以系統管理員身分執行", "Run as administrator"),
    "鼠标切换": ("鼠标切换", "滑鼠切換", "Mouse switching"),
    "普通权限运行时，切换管理员窗口（如任务管理器）可能异常，并可能出现 Windows 原生切换界面。":
        ("普通权限运行时，切换管理员窗口（如任务管理器）可能异常，并可能出现 Windows 原生切换界面。",
         "以一般權限執行時，切換系統管理員視窗（如工作管理員）可能異常，並可能出現 Windows 原生切換介面。",
         "Without administrator rights, switching elevated windows such as Task Manager may fail or show the Windows switcher."),
    "管理员模式设置将在下次启动时生效。":
        ("管理员模式设置将在下次启动时生效。", "系統管理員模式設定將於下次啟動時生效。",
         "The administrator setting takes effect on the next launch."),
    "退出": ("退出", "結束", "Exit"),
    "透明背景": ("透明背景", "透明背景", "Transparent background"),
    "浅深背景": ("浅深背景", "淺深背景", "Light/dark background"),
    "轻微模糊": ("轻微模糊", "輕微模糊", "Subtle blur"),
    "高斯模糊": ("高斯模糊", "高斯模糊", "Gaussian blur"),
    "亚克力背景": ("亚克力背景", "壓克力背景", "Acrylic background"),
    "深色": ("深色", "深色", "Dark"),
    "浅深色": ("浅深色", "淺深色", "Light/Dark"),
    "深海蓝": ("深海蓝", "深海藍", "Deep Sea Blue"),
    "暮紫": ("暮紫", "暮紫", "Dusk Purple"),
    "薄荷绿": ("薄荷绿", "薄荷綠", "Mint Green"),
    "输入进程，可自定义设置在切换界面中显示的名称。":
        ("输入进程，可自定义设置在切换界面中显示的名称。", "輸入處理程序，可自訂在切換介面中顯示的名稱。", "Enter a process and choose the name shown in the switcher."),
    "可在任务管理器里查找进程位置。": ("可在任务管理器里查找进程位置。", "可在工作管理員中尋找處理程序位置。", "Find the process location in Task Manager."),
    "例如：code.exe；可在任务管理器查看": ("例如：code.exe；可在任务管理器查看", "例如：code.exe；可在工作管理員中查看", "e.g. code.exe; find it in Task Manager"),
    "例如：微信或 PowerShell 7": ("例如：微信或 PowerShell 7", "例如：微信或 PowerShell 7", "e.g. WeChat or PowerShell 7"),
    "识别产品名": ("识别产品名", "識別產品名稱", "Detect product name"),
    "选择文件…": ("选择文件…", "選擇檔案…", "Browse…"),
    "输入进程": ("输入进程", "輸入處理程序", "Process"),
    "输入显示名称": ("输入显示名称", "輸入顯示名稱", "Display name"),
    "新增映射": ("新增映射", "新增映射", "Add mapping"),
    "保存": ("保存", "儲存", "Save"),
    "删除映射": ("删除映射", "刪除映射", "Delete mapping"),
    "删除当前映射并从列表中移除": ("删除当前映射并从列表中移除", "刪除目前映射並從清單中移除", "Delete this mapping and remove it from the list"),
    "完成": ("完成", "完成", "Done"),
    "选择进程文件": ("选择进程文件", "選擇處理程序檔案", "Select process file"),
    "可执行文件 (*.exe);;所有文件 (*.*)": ("可执行文件 (*.exe);;所有文件 (*.*)", "可執行檔 (*.exe);;所有檔案 (*.*)", "Executable files (*.exe);;All files (*.*)"),
    "找不到对应的进程文件，请先选择 EXE 文件或启动该程序。": ("找不到对应的进程文件，请先选择 EXE 文件或启动该程序。", "找不到對應的處理程序檔案，請先選擇 EXE 檔案或啟動該程式。", "Process file not found. Select its EXE file or start the app first."),
    "无法读取该进程的产品名。": ("无法读取该进程的产品名。", "無法讀取此處理程序的產品名稱。", "Could not read the product name from this process."),
    "有未保存的修改。": ("有未保存的修改。", "有尚未儲存的變更。", "You have unsaved changes."),
    "要保存修改后退出吗？": ("要保存修改后退出吗？", "要儲存變更後離開嗎？", "Save changes before closing?"),
    "保存并退出": ("保存并退出", "儲存並離開", "Save and exit"),
    "放弃修改": ("放弃修改", "放棄變更", "Discard changes"),
    "继续编辑": ("继续编辑", "繼續編輯", "Continue editing"),
    "进程": ("进程", "處理程序", "Process"),
    "仅开启或关闭映射，不会删除映射": ("仅开启或关闭映射，不会删除映射", "僅啟用或停用映射，不會刪除映射", "Toggle this mapping without deleting it"),
    "开启或关闭映射（不删除）": ("开启或关闭映射（不删除）", "啟用或停用映射（不刪除）", "Enable or disable mapping (not deleted)"),
    "取消新增": ("取消新增", "取消新增", "Cancel add"),
    "请填写输入进程和输入显示名称。": ("请填写输入进程和输入显示名称。", "請填寫處理程序和顯示名稱。", "Enter both a process and a display name."),
    "这个进程已经存在映射，不能重复添加。请直接选择已有映射进行修改。": ("这个进程已经存在映射，不能重复添加。请直接选择已有映射进行修改。", "此處理程序已有映射，無法重複新增。請直接選取現有映射進行修改。", "This process already has a mapping. Select it from the list to edit it."),
    "请先选择一条映射。": ("请先选择一条映射。", "請先選取一筆映射。", "Select a mapping first."),
    "这条映射已经不存在。": ("这条映射已经不存在。", "此映射已不存在。", "This mapping no longer exists."),
    "左键选择窗口，右键关闭窗口，按 Tab 选择下一个窗口，按 ·/~ 选择上一个窗口，松开 Alt 切换，按 Esc 取消":
        ("左键选择窗口，右键关闭窗口，按 Tab 选择下一个窗口，按 ·/~ 选择上一个窗口，松开 Alt 切换，按 Esc 取消", "按滑鼠左鍵選取視窗，按右鍵關閉視窗，按 Tab 選取下一個視窗，按 ·/~ 選取上一個視窗，放開 Alt 切換，按 Esc 取消", "Left-click to select; right-click to close. Tab/·~ select windows; release Alt to switch; Esc to cancel."),
    "环形窗口切换器，已选中 {title}": ("环形窗口切换器，已选中 {title}", "環形視窗切換器，已選取 {title}", "Radial window switcher, selected {title}"),
    "鼠标悬停切换     右键关闭窗口": ("鼠标悬停切换     右键关闭窗口", "滑鼠懸停切換     按右鍵關閉視窗", "Hover to switch     Right-click to close"),
    "右键关闭窗口": ("右键关闭窗口", "按右鍵關閉視窗", "Right-click to close"),
    "右键关闭窗口，按 Tab 选择下一个窗口，按 ·/~ 选择上一个窗口，松开 Alt 切换，按 Esc 取消":
        ("右键关闭窗口，按 Tab 选择下一个窗口，按 ·/~ 选择上一个窗口，松开 Alt 切换，按 Esc 取消",
         "按右鍵關閉視窗，按 Tab 選取下一個視窗，按 ·/~ 選取上一個視窗，放開 Alt 切換，按 Esc 取消",
         "Right-click to close. Tab/·~ select windows; release Alt to switch; Esc to cancel."),
    "     松开 ALT  切换     TAB  下一个     ·/~  上一个     ESC  取消": ("     松开 ALT  切换     TAB  下一个     ·/~  上一个     ESC  取消", "     放開 ALT  切換     TAB  下一個     ·/~  上一個     ESC  取消", "     Release ALT  Switch     TAB  Next     ·/~  Previous     ESC  Cancel"),
    "已最小化": ("已最小化", "已最小化", "Minimized"),
    "预览不可用": ("预览不可用", "預覽無法使用", "Preview unavailable"),
    "正在读取窗口…": ("正在读取窗口…", "正在讀取視窗…", "Loading windows…"),
    "连接 GitHub 失败，请检查网络后重试。": ("连接 GitHub 失败，请检查网络后重试。", "連線至 GitHub 失敗，請檢查網路後再試。", "Could not connect to GitHub. Check your connection and try again."),
    "当前版本 {current} 已是最新版本。": ("当前版本 {current} 已是最新版本。", "目前版本 {current} 已是最新版本。", "Version {current} is up to date."),
    "当前版本 {current} 不低于最新发行版 {latest}。": ("当前版本 {current} 不低于最新发行版 {latest}。", "目前版本 {current} 不低於最新發行版 {latest}。", "Version {current} is not older than the latest release ({latest})."),
    "发现新版本": ("发现新版本", "發現新版本", "Update available"),
    "发现新版本 {latest}（当前版本 {current}）。现在打开下载页？": ("发现新版本 {latest}（当前版本 {current}）。现在打开下载页？", "發現新版本 {latest}（目前版本 {current}）。現在開啟下載頁面嗎？", "Version {latest} is available (current: {current}). Open the download page?"),
    "开机启动设置失败：{error}": ("开机启动设置失败：{error}", "開機啟動設定失敗：{error}", "Could not change startup setting: {error}"),
    "程序已运行，已驻留系统托盘。按 Alt+Tab 呼出切换器。": ("程序已运行，已驻留系统托盘。按 Alt+Tab 呼出切换器。", "程式已執行並常駐於系統匣。按 Alt+Tab 開啟切換器。", "The app is running in the system tray. Press Alt+Tab to open the switcher."),
}


def tr(text: str, language: str = "zh_CN") -> str:
    translations = TRANSLATIONS.get(text)
    return translations[LANGUAGES.index(language)] if translations and language in LANGUAGES else text


LATEST_RELEASE_API = "https://api.github.com/repos/suzeccc/RadialAltTab/releases/latest"
LATEST_RELEASE_PAGE = "https://github.com/suzeccc/RadialAltTab/releases/latest"
STARTUP_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
PROCESS_LABELS = {"msedge": "Edge"}


def version_key(value: str) -> tuple[int, int, int]:
    match = re.fullmatch(r"v?(\d+)\.(\d+)\.(\d+)", value, re.IGNORECASE)
    if not match:
        raise ValueError(f"Unsupported release version: {value}")
    return tuple(map(int, match.groups()))


BLUR_MODES = {
    "transparent": "透明背景",
    "none": "浅深背景",
    "fast": "轻微模糊",
    "gaussian": "高斯模糊",
    "acrylic": "亚克力背景",
}


class PersistentMenu(QMenu):
    def mouseReleaseEvent(self, event) -> None:
        action = self.actionAt(event.position().toPoint())
        if (event.button() == Qt.MouseButton.LeftButton and action and action.isCheckable()
                and action.isEnabled() and not action.menu()):
            action.trigger()
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def keyPressEvent(self, event) -> None:
        action = self.activeAction()
        if (event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space)
                and action and action.isCheckable() and action.isEnabled() and not action.menu()):
            action.trigger()
            event.accept()
            return
        super().keyPressEvent(event)


GLASS_DIALOG_STYLE = """
QLabel, QCheckBox { color: #f4f7fb; background: transparent; }
QListWidget, QLineEdit {
    background-color: rgba(19, 27, 38, 145);
    color: #f4f7fb;
    border: 1px solid #4b596a;
    border-radius: 5px;
    padding: 4px;
}
QListWidget::item:selected { background-color: rgba(76, 117, 151, 155); }
QPushButton {
    background-color: rgba(43, 53, 67, 225);
    color: #f4f7fb;
    border: 1px solid #5c6f84;
    border-radius: 5px;
    padding: 5px 12px;
}
QPushButton:hover { background-color: #36516b; }
QPushButton:pressed { background-color: #263b50; }
QPushButton:disabled { color: #8693a2; border-color: #3b4653; }
"""


def style_glass_dialog(dialog: QDialog) -> None:
    dialog.setObjectName("glassDialog")
    dialog.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
    dialog.setStyleSheet(GLASS_DIALOG_STYLE)
    hwnd = int(dialog.winId())
    dark = wintypes.BOOL(True)
    dwmapi.DwmSetWindowAttribute(hwnd, 20, ctypes.byref(dark), ctypes.sizeof(dark))
    accent = ACCENT_POLICY(4, 0, 0x801F1A14, 0)  # Dark acrylic, ARGB tint.
    data = WINDOWCOMPOSITIONATTRIBDATA(19, ctypes.addressof(accent), ctypes.sizeof(accent))
    if not user32.SetWindowCompositionAttribute(hwnd, ctypes.byref(data)):
        dialog.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, False)
        dialog.setStyleSheet("#glassDialog { background-color: #171d26; }\n" + GLASS_DIALOG_STYLE)


def load_name_mappings(settings: QSettings) -> dict[str, str]:
    legacy_defaults = {
        "msedge": "Edge", "code": "VS Code", "chatgpt": "ChatGPT",
        "v2rayn": "v2rayN", "weixin": "微信", "taskmgr": "任务管理器",
        "lenovopcmanager": "联想电脑管家", "applicationframehost": "设置",
        "systemsettings": "设置",
    }
    raw = settings.value("name_mappings", "")
    try:
        values = json.loads(str(raw)) if raw else {}
    except (TypeError, ValueError):
        return {}
    if not isinstance(values, dict):
        return {}
    mappings = {}
    for key, value in values.items():
        raw_key = str(key)
        if not raw_key.startswith("process:") or not str(value).strip():
            continue
        source = executable_name(raw_key.split(":", 1)[1])
        value = str(value).strip()
        legacy_value = legacy_defaults.get(source.casefold())
        if source and (legacy_value is None or legacy_value.casefold() != value.casefold()):
            mappings[f"process:{source.casefold()}"] = value
    return mappings


def save_name_mappings(settings: QSettings, mappings: dict[str, str]) -> None:
    process_mappings = {}
    for key, value in mappings.items():
        if not key.startswith("process:") or not str(value).strip():
            continue
        source = executable_name(key.split(":", 1)[1])
        if source:
            process_mappings[f"process:{source.casefold()}"] = str(value).strip()
    settings.setValue("name_mappings", json.dumps(process_mappings, ensure_ascii=False, sort_keys=True))


def load_disabled_name_mappings(settings: QSettings) -> set[str]:
    raw = settings.value("disabled_name_mappings", "")
    try:
        values = json.loads(str(raw)) if raw else []
    except (TypeError, ValueError):
        return set()
    if not isinstance(values, list):
        return set()
    disabled = set()
    for key in values:
        raw_key = str(key).strip()
        if not raw_key.startswith("process:"):
            continue
        source = executable_name(raw_key.split(":", 1)[1])
        if source:
            disabled.add(f"process:{source.casefold()}")
    return disabled


def save_disabled_name_mappings(settings: QSettings, disabled: set[str]) -> None:
    process_disabled = set()
    for key in disabled:
        if not key.startswith("process:"):
            continue
        source = executable_name(key.split(":", 1)[1])
        if source:
            process_disabled.add(f"process:{source.casefold()}")
    settings.setValue("disabled_name_mappings", json.dumps(sorted(process_disabled), ensure_ascii=False))


def load_deleted_name_mappings(settings: QSettings) -> set[str]:
    raw = settings.value("deleted_name_mappings", "")
    try:
        values = json.loads(str(raw)) if raw else []
    except (TypeError, ValueError):
        return set()
    if not isinstance(values, list):
        return set()
    deleted = set()
    for key in values:
        raw_key = str(key).strip()
        if not raw_key.startswith("process:"):
            continue
        source = executable_name(raw_key.split(":", 1)[1])
        if source and source.casefold() in PROCESS_LABELS:
            deleted.add(f"process:{source.casefold()}")
    return deleted


def save_deleted_name_mappings(settings: QSettings, deleted: set[str]) -> None:
    process_deleted = set()
    for key in deleted:
        if not key.startswith("process:"):
            continue
        source = executable_name(key.split(":", 1)[1])
        if source and source.casefold() in PROCESS_LABELS:
            process_deleted.add(f"process:{source.casefold()}")
    settings.setValue("deleted_name_mappings", json.dumps(sorted(process_deleted), ensure_ascii=False))


class MappingRow(QWidget):
    clicked = Signal()

    def mousePressEvent(self, event) -> None:
        self.clicked.emit()
        super().mousePressEvent(event)


class NameMappingDialog(QDialog):
    def __init__(self, parent: QWidget, current: dict[str, str], disabled: set[str] | None = None,
                 deleted: set[str] | None = None, language: str = "zh_CN"):
        super().__init__(None)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.language = language if language in LANGUAGES else "zh_CN"
        self.mappings = dict(current)
        self.original_mappings = dict(current)
        self.disabled = set(disabled or ())
        self.original_disabled = set(self.disabled)
        self.deleted = set(deleted or ())
        self.original_deleted = set(self.deleted)
        self.defaults = {f"process:{key}": value for key, value in PROCESS_LABELS.items()}
        self.selected_key = ""
        self.new_mode = False
        self.previous_selected_key = ""
        self.selected_process_path = ""

        self.setWindowModality(Qt.WindowModality.NonModal)
        self.setWindowFlag(Qt.WindowType.Window, True)
        self.setWindowFlag(Qt.WindowType.WindowMinimizeButtonHint, True)
        self.setWindowFlag(Qt.WindowType.WindowMaximizeButtonHint, False)
        self.setWindowFlag(Qt.WindowType.WindowCloseButtonHint, True)
        self.setMinimumWidth(560)
        layout = QVBoxLayout(self)
        self.instructions_label = QLabel()
        self.task_manager_label = QLabel()
        layout.addWidget(self.instructions_label)
        layout.addWidget(self.task_manager_label)

        self.mapping_list = QListWidget()
        self.mapping_list.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.mapping_list.currentItemChanged.connect(self.load_selected)
        layout.addWidget(self.mapping_list)

        form = QFormLayout()
        self.source = QLineEdit()
        self.display = QLineEdit()
        display_row = QWidget()
        display_layout = QHBoxLayout(display_row)
        display_layout.setContentsMargins(0, 0, 0, 0)
        display_layout.addWidget(self.display)
        self.identify_button = QPushButton()
        self.identify_button.clicked.connect(self.identify_product_name)
        display_layout.addWidget(self.identify_button)
        source_row = QWidget()
        source_layout = QHBoxLayout(source_row)
        source_layout.setContentsMargins(0, 0, 0, 0)
        source_layout.addWidget(self.source)
        self.choose_button = QPushButton()
        self.choose_button.clicked.connect(self.choose_process)
        source_layout.addWidget(self.choose_button)
        self.source_label = QLabel()
        self.display_label = QLabel()
        form.addRow(self.source_label, source_row)
        form.addRow(self.display_label, display_row)
        layout.addLayout(form)

        actions = QHBoxLayout()
        self.add_button = QPushButton()
        self.save_button = QPushButton()
        self.delete_button = QPushButton()
        self.add_button.clicked.connect(self.new_mapping)
        self.save_button.clicked.connect(self.save_mapping)
        self.delete_button.clicked.connect(self.delete_mapping)
        actions.addWidget(self.add_button)
        actions.addWidget(self.delete_button)
        actions.addWidget(self.save_button)
        actions.addStretch()
        layout.addLayout(actions)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok)
        self.done_button = buttons.button(QDialogButtonBox.StandardButton.Ok)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.retranslate()
        self.refresh()

    def retranslate(self) -> None:
        self.setWindowTitle(tr("窗口映射", self.language))
        self.instructions_label.setText(tr("输入进程，可自定义设置在切换界面中显示的名称。", self.language))
        self.task_manager_label.setText(tr("可在任务管理器里查找进程位置。", self.language))
        self.source.setPlaceholderText(tr("例如：code.exe；可在任务管理器查看", self.language))
        self.display.setPlaceholderText(tr("例如：微信或 PowerShell 7", self.language))
        self.identify_button.setText(tr("识别产品名", self.language))
        self.choose_button.setText(tr("选择文件…", self.language))
        self.source_label.setText(tr("输入进程", self.language))
        self.display_label.setText(tr("输入显示名称", self.language))
        self.add_button.setText(tr("取消新增" if self.new_mode else "新增映射", self.language))
        self.save_button.setText(tr("保存", self.language))
        self.delete_button.setText(tr("删除映射", self.language))
        self.delete_button.setToolTip(tr("删除当前映射并从列表中移除", self.language))
        self.done_button.setText(tr("完成", self.language))
        for index in range(self.mapping_list.count()):
            item = self.mapping_list.item(index)
            key = str(item.data(Qt.ItemDataRole.UserRole))
            row = self.mapping_list.itemWidget(item)
            label = row.findChild(QLabel) if row else None
            toggle = row.findChild(QCheckBox) if row else None
            if label:
                label.setText(self.label_for(key))
            if toggle:
                toggle.setToolTip(tr("仅开启或关闭映射，不会删除映射", self.language))
                toggle.setAccessibleName(tr("开启或关闭映射（不删除）", self.language))

    def set_language(self, language: str) -> None:
        self.language = language if language in LANGUAGES else "zh_CN"
        self.retranslate()

    def choose_process(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, tr("选择进程文件", self.language), "", tr("可执行文件 (*.exe);;所有文件 (*.*)", self.language))
        if path:
            self.source.setText(os.path.basename(path))
            self.selected_process_path = path
            self.source.setFocus()

    def identify_product_name(self) -> None:
        source = self.source.text().strip()
        target = executable_name(source).casefold()
        path = self.selected_process_path if target and executable_name(self.selected_process_path).casefold() == target else ""
        if not path and os.path.isfile(source):
            path = source
        if not path and target:
            path = process_file_path(target)
        if not path:
            QMessageBox.information(self, tr("识别产品名", self.language),
                                    tr("找不到对应的进程文件，请先选择 EXE 文件或启动该程序。", self.language))
            return
        product_name = file_product_name(path)
        if not product_name:
            QMessageBox.information(self, tr("识别产品名", self.language), tr("无法读取该进程的产品名。", self.language))
            return
        self.display.setText(product_name)
        self.display.setFocus()

    def current_form_key(self) -> str:
        source = self.source.text().strip()
        if not source:
            return ""
        source = executable_name(source)
        return f"process:{source.casefold()}" if source else ""

    def form_is_dirty(self) -> bool:
        key = self.current_form_key()
        value = self.display.text().strip()
        if not key and not value:
            return False
        expected = self.mappings.get(key, self.defaults.get(key, ""))
        return key != self.selected_key or value != expected

    def has_unsaved_changes(self) -> bool:
        return (self.mappings != self.original_mappings or self.disabled != self.original_disabled
                or self.deleted != self.original_deleted or self.form_is_dirty())

    def confirm_exit(self) -> str:
        if not self.has_unsaved_changes():
            return "discard"
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Warning)
        box.setWindowTitle(tr("窗口映射", self.language))
        box.setText(tr("有未保存的修改。", self.language))
        box.setInformativeText(tr("要保存修改后退出吗？", self.language))
        save = box.addButton(tr("保存并退出", self.language), QMessageBox.ButtonRole.AcceptRole)
        discard = box.addButton(tr("放弃修改", self.language), QMessageBox.ButtonRole.DestructiveRole)
        box.addButton(tr("继续编辑", self.language), QMessageBox.ButtonRole.RejectRole)
        box.exec()
        clicked = box.clickedButton()
        if clicked is save:
            if self.form_is_dirty() and not self.save_mapping():
                return "cancel"
            return "save"
        if clicked is discard:
            return "discard"
        return "cancel"

    def accept(self) -> None:
        if not self.has_unsaved_changes():
            super().accept()
            return
        decision = self.confirm_exit()
        if decision == "save":
            super().accept()
        elif decision == "discard":
            super().reject()

    def reject(self) -> None:
        if not self.has_unsaved_changes():
            super().reject()
            return
        decision = self.confirm_exit()
        if decision == "save":
            super().accept()
        elif decision == "discard":
            super().reject()

    def keys(self) -> list[str]:
        defaults = [key for key in self.defaults if key not in self.deleted]
        custom = [key for key in self.mappings if key.startswith("process:") and key not in self.defaults]
        return defaults + custom

    def label_for(self, key: str) -> str:
        source = key.split(":", 1)[1]
        return f"{tr('进程', self.language)}: {source}.exe  →  {self.mappings.get(key, self.defaults.get(key, ''))}"

    def refresh(self, select: str = "") -> None:
        self.mapping_list.blockSignals(True)
        self.mapping_list.clear()
        selected = None
        for key in self.keys():
            item = QListWidgetItem()
            item.setData(Qt.ItemDataRole.UserRole, key)
            self.mapping_list.addItem(item)
            row = MappingRow()
            row_layout = QHBoxLayout(row)
            row_layout.setContentsMargins(4, 0, 6, 0)
            label = QLabel(self.label_for(key))
            label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
            row_layout.addWidget(label)
            row_layout.addStretch()
            toggle = QCheckBox()
            toggle.setChecked(key not in self.disabled)
            toggle.setToolTip(tr("仅开启或关闭映射，不会删除映射", self.language))
            toggle.setAccessibleName(tr("开启或关闭映射（不删除）", self.language))

            def update_toggle(checked: bool, label=label) -> None:
                label.setEnabled(checked)

            row.clicked.connect(lambda item=item: self.mapping_list.setCurrentItem(item))
            toggle.toggled.connect(lambda checked, key=key: self.toggle_mapping(key, checked))
            toggle.toggled.connect(update_toggle)
            toggle.clicked.connect(lambda _checked=False, item=item: self.mapping_list.setCurrentItem(item))
            update_toggle(toggle.isChecked())
            row_layout.addWidget(toggle)
            self.mapping_list.setItemWidget(item, row)
            item.setSizeHint(QSize(0, row.sizeHint().height()))
            if key == select:
                selected = item
        self.mapping_list.blockSignals(False)
        if selected:
            self.mapping_list.setCurrentItem(selected)
        else:
            self.mapping_list.clearSelection()
            self.mapping_list.setCurrentRow(-1)
            self.selected_key = ""
            self.source.clear()
            self.display.clear()
        self._fit_mapping_rows()

    def _fit_mapping_rows(self) -> None:
        for index in range(self.mapping_list.count()):
            item = self.mapping_list.item(index)
            item.setSizeHint(QSize(0, item.sizeHint().height()))
        self.mapping_list.updateGeometries()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        QTimer.singleShot(0, self._fit_mapping_rows)

    def load_selected(self, item, _previous) -> None:
        if not item:
            return
        self.new_mode = False
        self.previous_selected_key = ""
        self.add_button.setText(tr("新增映射", self.language))
        self.selected_key = str(item.data(Qt.ItemDataRole.UserRole))
        source = self.selected_key.split(":", 1)[1]
        self.source.setText(f"{source}.exe")
        self.display.setText(self.mappings.get(self.selected_key, self.defaults.get(self.selected_key, "")))

    def new_mapping(self) -> None:
        if self.new_mode:
            self.cancel_new_mapping()
            return
        self.new_mode = True
        self.previous_selected_key = self.selected_key
        self.selected_key = ""
        self.mapping_list.clearSelection()
        self.source.clear()
        self.display.clear()
        self.add_button.setText(tr("取消新增", self.language))
        self.source.setFocus()

    def cancel_new_mapping(self) -> None:
        previous = self.previous_selected_key
        self.new_mode = False
        self.previous_selected_key = ""
        self.selected_key = previous
        self.add_button.setText(tr("新增映射", self.language))
        if previous:
            self.refresh(previous)
            return
        self.source.clear()
        self.display.clear()
        self.mapping_list.clearSelection()

    def save_mapping(self) -> bool:
        source = self.source.text().strip()
        value = self.display.text().strip()
        source = executable_name(source)
        if not source or not value:
            QMessageBox.warning(self, tr("窗口映射", self.language), tr("请填写输入进程和输入显示名称。", self.language))
            return False
        key = f"process:{source.casefold()}"
        if key in self.mappings and key != self.selected_key:
            QMessageBox.information(self, tr("窗口映射", self.language),
                                    tr("这个进程已经存在映射，不能重复添加。请直接选择已有映射进行修改。", self.language))
            return False
        if self.selected_key and self.selected_key != key:
            self.mappings.pop(self.selected_key, None)
        self.deleted.discard(key)
        self.mappings[key] = value
        self.refresh(key)
        self.original_mappings = dict(self.mappings)
        self.original_disabled = set(self.disabled)
        self.original_deleted = set(self.deleted)
        return True

    def delete_mapping(self) -> None:
        item = self.mapping_list.currentItem()
        key = str(item.data(Qt.ItemDataRole.UserRole)) if item else self.selected_key
        if not key:
            QMessageBox.information(self, tr("窗口映射", self.language), tr("请先选择一条映射。", self.language))
            return
        row = self.mapping_list.currentRow()
        if key in self.defaults:
            self.mappings.pop(key, None)
            self.deleted.add(key)
        elif key in self.mappings:
            self.mappings.pop(key, None)
        else:
            QMessageBox.information(self, tr("窗口映射", self.language), tr("这条映射已经不存在。", self.language))
            return
        self.disabled.discard(key)
        self.selected_key = ""
        self.refresh()
        if self.mapping_list.count():
            self.mapping_list.setCurrentRow(min(row, self.mapping_list.count() - 1))
        else:
            self.source.clear()
            self.display.clear()

    def toggle_mapping(self, key: str, enabled: bool) -> None:
        if enabled:
            self.disabled.discard(key)
        else:
            self.disabled.add(key)


def edit_name_mapping(parent: QWidget, settings: QSettings, current: dict[str, str], disabled: set[str],
                      deleted: set[str], language: str = "zh_CN") -> NameMappingDialog:
    dialog = NameMappingDialog(parent, current, disabled, deleted, language)
    dialog.accepted.connect(lambda: (save_name_mappings(settings, dialog.mappings),
                                      save_disabled_name_mappings(settings, dialog.disabled),
                                      save_deleted_name_mappings(settings, dialog.deleted)))
    style_glass_dialog(dialog)
    dialog.show()
    dialog.raise_()
    dialog.activateWindow()
    return dialog


THEMES = {
    "dark": {
        "label": "深色", "accent": "#b8c7d9", "accent_light": "#eef4fb",
        "selected_start": "#27313d", "selected_end": "#465361", "glow": "#637387",
        "segment_start": "#171c24", "segment_end": "#252c36",
        "core_start": "#252d36", "core_mid": "#11161d", "core_end": "#07090d",
        "background_start": "#171a20", "background_mid": "#0e1116", "background_end": "#07090c",
    },
    "transparent": {
        "label": "浅深色", "alpha": "0.25", "accent": "#60cdff", "accent_light": "#f3f3f3",
        "selected_start": "#3a3a3a", "selected_end": "#454545", "glow": "#60cdff",
        "segment_start": "#252525", "segment_end": "#2d2d2d",
        "core_start": "#3a3a3a", "core_mid": "#2b2b2b", "core_end": "#202020",
        "background_start": "#292929", "background_mid": "#202020", "background_end": "#181818",
    },
    "blue": {
        "label": "深海蓝", "accent": "#5bbaff", "accent_light": "#8dd2ff",
        "selected_start": "#123658", "selected_end": "#245b8f", "glow": "#416697",
        "segment_start": "#1b283b", "segment_end": "#202d41",
        "core_start": "#142740", "core_mid": "#0b1729", "core_end": "#07111f",
        "background_start": "#27354f", "background_mid": "#111d30", "background_end": "#091420",
    },
    "purple": {
        "label": "暮紫", "accent": "#c39cff", "accent_light": "#e0c8ff",
        "selected_start": "#39264f", "selected_end": "#704a9c", "glow": "#7653a5",
        "segment_start": "#2b2438", "segment_end": "#3b304d",
        "core_start": "#2a2040", "core_mid": "#19132c", "core_end": "#100b1d",
        "background_start": "#30283f", "background_mid": "#1b1528", "background_end": "#100b1b",
    },
    "green": {
        "label": "薄荷绿", "accent": "#6de0bd", "accent_light": "#a5f2dc",
        "selected_start": "#16483e", "selected_end": "#277d69", "glow": "#3b9e83",
        "segment_start": "#203a38", "segment_end": "#2d4d49",
        "core_start": "#153732", "core_mid": "#0d2724", "core_end": "#071a19",
        "background_start": "#233c3b", "background_mid": "#142a28", "background_end": "#091b1b",
    },
}


def themed_color(theme: dict[str, str], key: str, alpha: int = 255) -> QColor:
    color = QColor(theme[key])
    color.setAlpha(round(alpha * float(theme.get("alpha", "1"))))
    return color


def set_windows_app_identity() -> None:
    shell32.SetCurrentProcessExplicitAppUserModelID(APP_NAME)


def activate_window(hwnd: int) -> bool:
    if not hwnd or not user32.IsWindow(hwnd):
        return False
    if user32.GetForegroundWindow() == hwnd and not user32.IsIconic(hwnd):
        return True
    # Use the same native path as Windows Alt+Tab; it also restores elevated windows such as Task Manager.
    user32.SwitchToThisWindow(hwnd, True)
    if user32.GetForegroundWindow() == hwnd and not user32.IsIconic(hwnd):
        return True
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


def active_owned_dialog(owner: int) -> int:
    found = 0

    @WNDPROC
    def visit(hwnd: int, _unused: int) -> bool:
        nonlocal found
        if not user32.IsWindowVisible(hwnd) or not user32.IsWindowEnabled(hwnd):
            return True
        current = int(user32.GetWindow(hwnd, 4) or 0)  # GW_OWNER
        while current:
            if current == owner:
                found = int(hwnd)
                return False
            current = int(user32.GetWindow(current, 4) or 0)
        return True

    user32.EnumWindows(visit, 0)
    return found


def window_title(hwnd: int) -> str:
    length = user32.GetWindowTextLengthW(hwnd)
    if not length:
        return ""
    buffer = ctypes.create_unicode_buffer(length + 1)
    user32.GetWindowTextW(hwnd, buffer, length + 1)
    return buffer.value.strip()


def process_path(hwnd: int) -> str:
    pid = wintypes.DWORD()
    if not user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid)) or not pid.value:
        return ""
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid.value)
    if not handle:
        return ""
    try:
        buffer = ctypes.create_unicode_buffer(1024)
        length = wintypes.DWORD(len(buffer))
        if not kernel32.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(length)):
            return ""
        return buffer.value
    finally:
        kernel32.CloseHandle(handle)


def process_file_path(target: str) -> str:
    found = ""

    @WNDPROC
    def visit(hwnd: HWND, _lparam: LPARAM) -> bool:
        nonlocal found
        path = process_path(hwnd)
        if path and executable_name(path).casefold() == target:
            found = path
            return False
        return True

    user32.EnumWindows(visit, 0)
    return found


def file_product_name(path: str) -> str:
    if not path:
        return ""
    handle = wintypes.DWORD()
    size = version.GetFileVersionInfoSizeW(path, ctypes.byref(handle))
    if not size:
        return ""
    data = ctypes.create_string_buffer(size)
    if not version.GetFileVersionInfoW(path, 0, size, data):
        return ""

    value = ctypes.c_void_p()
    length = wintypes.UINT()
    translations = []
    if version.VerQueryValueW(data, r"\VarFileInfo\Translation", ctypes.byref(value), ctypes.byref(length)):
        pairs = ctypes.cast(value, ctypes.POINTER(wintypes.WORD))
        for index in range(length.value // 4):
            translations.append(f"{pairs[index * 2]:04x}{pairs[index * 2 + 1]:04x}")
    for translation in translations + ["040904b0", "040904e4"]:
        value = ctypes.c_void_p()
        length = wintypes.UINT()
        key = fr"\StringFileInfo\{translation}\ProductName"
        if version.VerQueryValueW(data, key, ctypes.byref(value), ctypes.byref(length)) and value.value:
            product = ctypes.wstring_at(value.value).strip()
            if product:
                return product
    return ""


def executable_name(path: str) -> str:
    return os.path.splitext(os.path.basename(path))[0] if path else ""


def process_name(hwnd: int) -> str:
    return executable_name(process_path(hwnd))


def short_window_title(title: str) -> str:
    title = " ".join(title.split())
    for separator in (" - ", " — ", " · ", " | ", " :: ", " ／ ", " → ", "和"):
        if separator in title:
            title = title.split(separator, 1)[0].strip() or title
            break
    else:
        and_index = title.casefold().find(" and ")
        if and_index >= 0:
            title = title[:and_index].strip() or title
    for suffix in (
        " (x64)", " (x86)", " (arm64)", " (64-bit)",
        " 以管理员身份运行", " (管理员)", " (administrator)",
        " [管理员]", " [administrator]",
    ):
        if title.casefold().endswith(suffix.casefold()):
            title = title[:-len(suffix)].rstrip()
            break
    return title


def normalized_window_title(title: str) -> str:
    return " ".join(title.split()).strip()


def display_window_name(title: str, executable: str, mappings: dict[str, str] | None = None,
                        disabled: set[str] | None = None) -> str:
    mappings = mappings or {}
    disabled = disabled or set()
    title = short_window_title(title)
    process_key = f"process:{executable.casefold()}"
    mapping = "" if process_key in disabled else mappings.get(
        process_key, PROCESS_LABELS.get(executable.casefold(), ""))
    if mapping:
        return mapping
    return title


def order_by_recent(items: list[tuple], recent_order: list[int] | None) -> list[tuple]:
    if not recent_order:
        return list(items)
    by_hwnd = {item[0]: item for item in items}
    ordered = [by_hwnd[hwnd] for hwnd in recent_order if hwnd in by_hwnd]
    ordered_hwnds = {item[0] for item in ordered}
    return ordered + [item for item in items if item[0] not in ordered_hwnds]


def windows(exclude: int = 0, mappings: dict[str, str] | None = None,
            disabled: set[str] | None = None,
            recent_order: list[int] | None = None) -> list[tuple[int, str]]:
    found: list[tuple[int, str, str]] = []

    @WNDPROC
    def visit(hwnd: int, _unused: int) -> bool:
        if hwnd == exclude or not user32.IsWindowVisible(hwnd):
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
            path = process_path(hwnd)
            executable = executable_name(path)
            found.append((int(hwnd), title, executable))
        return True

    user32.EnumWindows(visit, 0)
    found = order_by_recent(found, recent_order)
    return [(hwnd, display_window_name(title, executable, mappings, disabled))
            for hwnd, title, executable in found]


def qpixmap_to_pil(pixmap: QPixmap) -> Image.Image:
    image = pixmap.toImage().convertToFormat(QImage.Format.Format_RGBA8888)
    return Image.frombytes("RGBA", (image.width(), image.height()), image.bits().tobytes())


def blur_image(image: Image.Image, mode: str) -> Image.Image:
    if mode == "gaussian":
        return image.filter(ImageFilter.GaussianBlur(18))
    if mode == "acrylic":
        result = image.filter(ImageFilter.GaussianBlur(26))
        tint = Image.new("RGBA", result.size, (42, 66, 96, 105))
        result = Image.alpha_composite(result, tint)
        noise = Image.effect_noise(result.size, 6).convert("L")
        grain = Image.merge("RGBA", (noise, noise, noise, noise.point(lambda value: value // 10)))
        return Image.alpha_composite(result, grain)
    if mode == "fast":
        return image.filter(ImageFilter.BoxBlur(7))
    return image


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
        self.alt_down = False
        self.probe_seen = threading.Event()
        self.installed = threading.Event()
        self.error = ""

    def run(self) -> None:
        self.thread_id = kernel32.GetCurrentThreadId()

        def emit(action: str) -> None:
            try:
                self.events.event.emit(action)
            except Exception:
                pass

        @HOOKPROC
        def callback(code: int, message: int, data: int) -> int:
            if code >= 0:
                event = ctypes.cast(data, ctypes.POINTER(KBDLLHOOKSTRUCT)).contents
                if event.vkCode == VK_F24 and event.dwExtraInfo == HOOK_PROBE:
                    self.probe_seen.set()
                    return 1
                key = event.vkCode
                if not self.engaged and key not in (VK_MENU, VK_LMENU, VK_RMENU, VK_TAB, VK_OEM_3):
                    return user32.CallNextHookEx(self.handle, code, message, data)
                down = message in (WM_KEYDOWN, WM_SYSKEYDOWN)
                up = message in (WM_KEYUP, WM_SYSKEYUP)
                if key in (VK_MENU, VK_LMENU, VK_RMENU):
                    if down:
                        self.alt_down = True
                    elif up:
                        self.alt_down = False
                alt_pressed = self.alt_down or bool(event.flags & LLKHF_ALTDOWN)
                alt_pressed = alt_pressed or message in (WM_SYSKEYDOWN, WM_SYSKEYUP)
                if key == VK_TAB and (self.engaged or alt_pressed):
                    if down:
                        first = not self.engaged
                        self.engaged = True
                        emit("open" if first else "next")
                    return 1
                if key == VK_OEM_3 and (self.engaged or alt_pressed):
                    if down:
                        first = not self.engaged
                        self.engaged = True
                        emit("open_previous" if first else "previous")
                    return 1
                if self.engaged:
                    if key in (VK_MENU, VK_LMENU, VK_RMENU) and up:
                        self.engaged = False
                        emit("commit")
                    elif key == VK_ESCAPE:
                        if down:
                            self.engaged = False
                            emit("cancel")
                        return 1
                    elif key == VK_DELETE:
                        if down:
                            emit("close")
                        return 1
            return user32.CallNextHookEx(self.handle, code, message, data)

        self.callback = callback
        self.handle = user32.SetWindowsHookExW(WH_KEYBOARD_LL, callback, kernel32.GetModuleHandleW(None), 0)
        if not self.handle:
            self.error = f"Keyboard hook failed: {ctypes.get_last_error()}"
            self.installed.set()
            return
        timer_id = user32.SetTimer(None, 0, 5000, None)
        if not timer_id:
            self.error = f"Keyboard hook timer failed: {ctypes.get_last_error()}"
            user32.UnhookWindowsHookEx(self.handle)
            self.handle = None
            self.installed.set()
            return
        self.installed.set()
        message = MSG()
        while user32.GetMessageW(ctypes.byref(message), None, 0, 0) > 0:
            if message.message == WM_REHOOK or (message.message == WM_TIMER and message.wParam == timer_id):
                # Windows silently removes a low-level hook after a callback timeout.
                replacement = user32.SetWindowsHookExW(WH_KEYBOARD_LL, callback, kernel32.GetModuleHandleW(None), 0)
                if replacement:
                    old_handle, self.handle = self.handle, replacement
                    user32.UnhookWindowsHookEx(old_handle)
                continue
            user32.TranslateMessage(ctypes.byref(message))
            user32.DispatchMessageW(ctypes.byref(message))
        if timer_id:
            user32.KillTimer(None, timer_id)
        user32.UnhookWindowsHookEx(self.handle)
        self.handle = None

    def stop(self) -> None:
        if self.thread_id and self.handle:
            user32.PostThreadMessageW(self.thread_id, WM_QUIT, 0, 0)

    def probe(self) -> bool:
        self.probe_seen.clear()
        key = INPUT()
        key.type = 1
        # ponytail: A lone F24 release may reach the focused app if the hook has failed.
        key.ki = KEYBDINPUT(VK_F24, 0, 2, 0, HOOK_PROBE)
        return user32.SendInput(1, ctypes.byref(key), ctypes.sizeof(INPUT)) == 1

    def reinstall(self) -> bool:
        return bool(user32.PostThreadMessageW(self.thread_id, WM_REHOOK, 0, 0))


class RadialOverlay(QWidget):
    opened = Signal()

    def __init__(self, demo: bool = False, theme: str = "blue", name_mappings: dict[str, str] | None = None,
                 disabled_name_mappings: set[str] | None = None, blur_mode: str = "none",
                 language: str = "zh_CN", mouse_switching: bool = True):
        super().__init__(None, Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.Tool)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setMouseTracking(True)
        self.setCursor(Qt.CursorShape.ArrowCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.language = language if language in LANGUAGES else "zh_CN"
        self.mouse_switching = mouse_switching
        self.set_mouse_switching(mouse_switching)
        self.demo = demo
        self.theme = THEMES.get(theme, THEMES["blue"])
        self.name_mappings = dict(name_mappings or {})
        self.disabled_name_mappings = set(disabled_name_mappings or ())
        self.blur_mode = blur_mode if blur_mode in BLUR_MODES else "none"
        self.screen_geometry: QRect | None = None
        self.ring_outer: float | None = None
        self.items: list[tuple[int, str]] = []
        self.icons: dict[int, QPixmap] = {}
        self.demo_icons: dict[str, QPixmap] = {}
        self.demo_previews: dict[str, QPixmap] = {}
        self.thumbnails: dict[int, ctypes.c_void_p] = {}
        self.thumbnail_bounds: dict[int, tuple[int, int, int, int]] = {}
        self.window_cache: list[tuple[int, str]] = []
        self.scan_result = None
        self.scan_request = None
        self.scan_result_lock = threading.Lock()
        self.scan_worker = False
        self.scan_generation = 0
        self.scan_previous = False
        self.scan_timer = QTimer(self)
        self.scan_timer.setInterval(16)
        self.scan_timer.timeout.connect(self.poll_window_scan)
        self.selected = 0
        self.source_hwnd = 0
        self.icon_load_index = 0
        self.background: QPixmap | None = None
        self.background_source: QPixmap | None = None
        self.background_result = None
        self.background_request = None
        self.background_result_lock = threading.Lock()
        self.background_generation = 0
        self.background_worker = False
        self.background_pending = False
        self.background_timer = QTimer(self)
        self.background_timer.setInterval(16)
        self.background_timer.timeout.connect(self.poll_background)
        self.open_animation = QPropertyAnimation(self, b"windowOpacity")
        self.open_animation.setDuration(190)
        self.open_animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        self.delete_animation = QVariantAnimation(self)
        self.delete_animation.setDuration(180)
        self.delete_animation.setStartValue(0.0)
        self.delete_animation.setEndValue(1.0)
        self.delete_animation.valueChanged.connect(self._update_delete_animation)
        self.delete_animation.finished.connect(self._finish_close)
        self.deleting_index: int | None = None
        self.delete_progress = 0.0
        self._recent_windows: list[int] = []
        self._recent_windows_lock = threading.Lock()
        self._foreground_hook = None
        self._foreground_callback = None

    def remember_foreground(self, hwnd: int) -> None:
        if not hwnd:
            return
        with self._recent_windows_lock:
            # ponytail: 512 tracked windows; raise the cap if users keep more open at once.
            self._recent_windows = ([hwnd] + [item for item in self._recent_windows if item != hwnd])[:512]

    def recent_window_order(self) -> list[int]:
        with self._recent_windows_lock:
            return list(self._recent_windows)

    def start_foreground_tracking(self) -> None:
        if self._foreground_hook:
            return

        @WINEVENTPROC
        def callback(_hook, event, hwnd, object_id, child_id, _thread_id, _time):
            if event == EVENT_SYSTEM_FOREGROUND and hwnd and object_id == OBJID_WINDOW and child_id == CHILDID_SELF:
                self.remember_foreground(int(hwnd))

        self._foreground_callback = callback
        self._foreground_hook = user32.SetWinEventHook(
            EVENT_SYSTEM_FOREGROUND, EVENT_SYSTEM_FOREGROUND, None, callback, 0, 0,
            WINEVENT_OUTOFCONTEXT | WINEVENT_SKIPOWNPROCESS,
        )
        if self._foreground_hook:
            self.remember_foreground(int(user32.GetForegroundWindow() or 0))
        else:
            self._foreground_callback = None

    def stop_foreground_tracking(self) -> None:
        hook = self._foreground_hook
        self._foreground_hook = None
        if hook:
            user32.UnhookWinEvent(hook)
        self._foreground_callback = None

    def set_theme(self, theme: str) -> None:
        self.theme = THEMES.get(theme, THEMES["blue"])
        self.update()

    def set_blur_mode(self, mode: str) -> None:
        self.blur_mode = mode if mode in BLUR_MODES else "none"
        if self.isVisible():
            if self.screen_geometry is not None:
                self.configure_geometry(self.screen_geometry)
            self.refresh_background()

    def configure_geometry(self, screen_geometry: QRect) -> None:
        self.screen_geometry = screen_geometry
        self.ring_outer = None
        if self.blur_mode != "transparent":
            self.setGeometry(screen_geometry)
            return
        width = min(900, screen_geometry.width() - 32)
        height_limit = screen_geometry.height() - 32
        outer = min(350, screen_geometry.width() * .31, screen_geometry.height() * .42,
                    (height_limit - 100) / 2, (width - 200) / 2)
        self.ring_outer = outer
        height = min(height_limit, math.ceil(outer * 2 + 100))
        self.setGeometry(QRect(screen_geometry.x() + (screen_geometry.width() - width) // 2,
                               screen_geometry.y() + (screen_geometry.height() - height) // 2,
                               width, height))

    def refresh_background(self) -> None:
        with self.background_result_lock:
            self.background_generation += 1
            generation = self.background_generation
            self.background_result = None
            self.background_request = None
        if self.demo or self.blur_mode == "transparent":
            self.background = None
            self.background_pending = False
            self.background_timer.stop()
            self.update()
            return
        if self.background_source is None or self.background_source.isNull():
            self.background_pending = False
            self.background_timer.stop()
            return
        background = self.background_source.scaled(
            self.size(), Qt.AspectRatioMode.IgnoreAspectRatio,
            Qt.TransformationMode.SmoothTransformation)
        self.background = background
        self.background_pending = self.blur_mode != "none"
        if not self.background_pending:
            self.background_timer.stop()
            self.update()
            return
        image = qpixmap_to_pil(background)
        mode = self.blur_mode

        with self.background_result_lock:
            self.background_request = (generation, image, mode)
            if not self.background_worker:
                self.background_worker = True
                threading.Thread(target=self.render_background, name="background-blur", daemon=True).start()
        self.background_timer.start()
        self.update()

    def render_background(self) -> None:
        while True:
            with self.background_result_lock:
                if self.background_request is None:
                    self.background_worker = False
                    return
                generation, image, mode = self.background_request
                self.background_request = None
            try:
                result = blur_image(image, mode)
                payload = (result.size, result.tobytes("raw", "RGBA"))
            except Exception:
                payload = (None, None)
            with self.background_result_lock:
                if generation == self.background_generation:
                    self.background_result = (generation, *payload)

    def poll_background(self) -> None:
        with self.background_result_lock:
            latest = self.background_result
            self.background_result = None
        if latest is None:
            return
        _generation, size, data = latest
        if size is None or data is None:
            self.background_pending = False
            self.background_timer.stop()
            return
        width, height = size
        self.background = QPixmap.fromImage(
            QImage(data, width, height, width * 4, QImage.Format.Format_RGBA8888).copy())
        self.background_pending = False
        self.background_timer.stop()
        if self.isVisible():
            self.update()

    def start_window_scan(self, previous: bool) -> None:
        self.scan_previous = previous
        mappings = dict(self.name_mappings)
        disabled = set(self.disabled_name_mappings)
        recent_order = self.recent_window_order()
        exclude = int(self.winId())
        with self.scan_result_lock:
            self.scan_generation += 1
            self.scan_result = None
            self.scan_request = (self.scan_generation, exclude, mappings, disabled, recent_order)
            if not self.scan_worker:
                self.scan_worker = True
                threading.Thread(target=self.scan_windows, name="window-scan", daemon=True).start()
        self.scan_timer.start()

    def scan_windows(self) -> None:
        while True:
            with self.scan_result_lock:
                if self.scan_request is None:
                    self.scan_worker = False
                    return
                generation, exclude, mappings, disabled, recent_order = self.scan_request
                self.scan_request = None
            try:
                items = windows(exclude=exclude, mappings=mappings, disabled=disabled,
                                recent_order=recent_order)
            except Exception:
                items = []
            with self.scan_result_lock:
                if generation == self.scan_generation:
                    self.scan_result = (generation, items)

    def poll_window_scan(self) -> None:
        with self.scan_result_lock:
            latest = self.scan_result
            self.scan_result = None
        if latest is None:
            return
        _generation, items = latest
        self.scan_timer.stop()
        self.window_cache = items
        if not self.isVisible():
            return
        if not items:
            self.items = []
            self.hide()
            activate_window(self.source_hwnd)
            return
        self.items = items
        source_index = next((i for i, (hwnd, _) in enumerate(items) if hwnd == self.source_hwnd), None)
        if source_index is not None:
            self.selected = (source_index + (-1 if self.scan_previous else 1)) % len(items)
        else:
            self.selected = min(self.selected, len(items) - 1)
        self.announce_selection()
        self.icons = {}
        self.sync_thumbnails()
        QTimer.singleShot(0, self.load_visuals)
        self.update()

    def open(self, previous: bool = False) -> None:
        if self.isVisible():
            return
        source = int(user32.GetForegroundWindow() or 0)
        self.source_hwnd = source
        self.remember_foreground(source)
        if self.demo:
            self.items = [(0, name) for name in ["Chrome", "VS Code", "微信", "视频播放器", "文件资源管理器", "Photoshop", "终端", "设置"]]
        else:
            self.items = order_by_recent(self.window_cache, self.recent_window_order()) or [
                (0, tr("正在读取窗口…", self.language))]
        if not self.items:
            return
        screen = QGuiApplication.screenAt(QCursor.pos()) or QGuiApplication.primaryScreen()
        if source:
            rect = wintypes.RECT()
            if user32.GetWindowRect(source, ctypes.byref(rect)):
                active_screen = QGuiApplication.screenAt(QPoint((rect.left + rect.right) // 2, (rect.top + rect.bottom) // 2))
                screen = active_screen or screen
        self.configure_geometry(screen.geometry())
        source_index = next((i for i, (hwnd, _) in enumerate(self.items) if hwnd == source), None)
        if source_index is not None and not self.demo:
            self.selected = (source_index + (-1 if previous else 1)) % len(self.items)
        else:
            self.selected = len(self.items) - 1 if previous else 0
        self.announce_selection()
        self.icons = {}
        self.demo_icons = {}
        self.demo_previews = {}
        self.background = None
        self.background_source = None if self.demo else screen.grabWindow(0)
        self.setWindowOpacity(0)
        self.show()
        self.raise_()
        self.activateWindow()
        self.setFocus()
        self.open_animation.stop()
        self.open_animation.setStartValue(0)
        self.open_animation.setEndValue(1)
        self.open_animation.start()
        self.opened.emit()
        QTimer.singleShot(0, self.load_visuals)
        self.sync_thumbnails()
        if not self.demo:
            self.start_window_scan(previous)

    def load_visuals(self) -> None:
        if not self.isVisible():
            return
        if self.demo:
            self.demo_icons = {title: demo_icon(title) for _, title in self.items}
            self.demo_previews = {title: demo_preview(title) for _, title in self.items}
        else:
            self.icon_load_index = 0
            try:
                self.refresh_background()
            except (AttributeError, OSError):
                pass
            QTimer.singleShot(0, self.load_next_icon)
        self.update()

    def load_next_icon(self) -> None:
        if not self.isVisible() or self.demo or self.icon_load_index >= len(self.items):
            return
        hwnd = self.items[self.icon_load_index][0]
        self.icon_load_index += 1
        if hwnd:
            icon = window_icon(hwnd)
            if icon is not None:
                self.icons[hwnd] = icon
                self.update()
        QTimer.singleShot(0, self.load_next_icon)

    def thumbnail_rect(self, local: int, count: int, selected: bool) -> QRect:
        outer, inner, center = self.radii()
        angle = -math.pi / 2 + local * 2 * math.pi / count
        radial = inner + (outer - inner) * 0.52
        x = center.x() + radial * math.cos(angle)
        y = center.y() + radial * math.sin(angle)
        width = min(260, radial * 2 * math.pi / count * 0.8) * (1.08 if selected else 1)
        if local == self.deleting_index:
            width *= 1 - self.delete_progress * 0.18
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
                self.thumbnail_bounds.pop(hwnd, None)
        scale = self.devicePixelRatioF()
        for local, (hwnd, _) in enumerate(visible):
            if hwnd not in self.thumbnails:
                handle = ctypes.c_void_p()
                if dwmapi.DwmRegisterThumbnail(int(self.winId()), hwnd, ctypes.byref(handle)) != 0:
                    continue
                self.thumbnails[hwnd] = handle
            rect = self.thumbnail_rect(local, len(visible), local == self.selected).adjusted(2, 2, -2, -2)
            bounds = (round(rect.left() * scale), round(rect.top() * scale),
                      round((rect.right() + 1) * scale), round((rect.bottom() + 1) * scale))
            if self.thumbnail_bounds.get(hwnd) == bounds:
                continue
            props = DWM_THUMBNAIL_PROPERTIES()
            props.dwFlags = 0x1 | 0x4 | 0x8
            props.rcDestination = wintypes.RECT(*bounds)
            props.opacity = 255
            props.fVisible = True
            if dwmapi.DwmUpdateThumbnailProperties(self.thumbnails[hwnd], ctypes.byref(props)) != 0:
                dwmapi.DwmUnregisterThumbnail(self.thumbnails.pop(hwnd))
                self.thumbnail_bounds.pop(hwnd, None)
            else:
                self.thumbnail_bounds[hwnd] = bounds

    def hideEvent(self, event) -> None:
        for handle in self.thumbnails.values():
            dwmapi.DwmUnregisterThumbnail(handle)
        self.thumbnails.clear()
        self.thumbnail_bounds.clear()
        with self.background_result_lock:
            self.background_generation += 1
            self.background_result = None
            self.background_request = None
        self.background_timer.stop()
        self.background_pending = False
        self.background = None
        self.background_source = None
        super().hideEvent(event)

    def event(self, event) -> bool:
        if event.type() == QEvent.Type.WindowDeactivate and self.isVisible():
            self.hide()
        return super().event(event)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self.sync_thumbnails()

    def announce_selection(self) -> None:
        if self.items:
            self.setAccessibleName(tr("环形窗口切换器，已选中 {title}", self.language).format(
                title=self.items[self.selected][1]))

    def set_language(self, language: str) -> None:
        self.language = language if language in LANGUAGES else "zh_CN"
        self.set_mouse_switching(self.mouse_switching)
        self.announce_selection()
        self.update()

    def set_mouse_switching(self, enabled: bool) -> None:
        self.mouse_switching = enabled
        description = ("左键选择窗口，右键关闭窗口，按 Tab 选择下一个窗口，按 ·/~ 选择上一个窗口，松开 Alt 切换，按 Esc 取消"
                       if enabled else "右键关闭窗口，按 Tab 选择下一个窗口，按 ·/~ 选择上一个窗口，松开 Alt 切换，按 Esc 取消")
        self.setAccessibleDescription(tr(description, self.language))
        self.update()

    def move_selection(self, step: int) -> None:
        if self.items and self.isVisible() and self.deleting_index is None:
            self.selected = (self.selected + step) % len(self.items)
            self.announce_selection()
            self.update()
            self.sync_thumbnails()

    def commit(self) -> None:
        if not self.isVisible() or self.deleting_index is not None:
            return
        hwnd = self.items[self.selected][0]
        if not hwnd:
            self.cancel()
            return
        self.hide()
        activate_window(hwnd)

    def _update_delete_animation(self, value) -> None:
        self.delete_progress = float(value)
        self.update()
        self.sync_thumbnails()

    def close_selected(self) -> None:
        if not self.isVisible() or not self.items or self.deleting_index is not None:
            return

        self.deleting_index = self.selected
        self.delete_progress = 0.0
        self.delete_animation.start()

    def _finish_close(self) -> None:
        index = self.deleting_index
        self.deleting_index = None
        self.delete_progress = 0.0
        if index is None or not self.isVisible() or index >= len(self.items):
            self.update()
            return

        hwnd = self.items[index][0]
        if hwnd and user32.IsWindow(hwnd) and user32.IsWindowVisible(hwnd):
            if not user32.IsWindowEnabled(hwnd):
                dialog = active_owned_dialog(hwnd)
                if dialog:
                    self.hide()
                    activate_window(dialog)
                return
            if not user32.PostMessageW(hwnd, WM_SYSCOMMAND, SC_CLOSE, 0):
                self.update()
                return
        QTimer.singleShot(250, lambda: self._check_closed_window(hwnd, 4))

    def _check_closed_window(self, hwnd: int, retries: int) -> None:
        if not self.isVisible():
            return
        index = next((i for i, (item_hwnd, _) in enumerate(self.items) if item_hwnd == hwnd), None)
        if index is None:
            return
        if hwnd and user32.IsWindow(hwnd) and user32.IsWindowVisible(hwnd):
            if not user32.IsWindowEnabled(hwnd):
                dialog = active_owned_dialog(hwnd)
                if dialog:
                    self.hide()
                    activate_window(dialog)
                    return
            if retries:
                QTimer.singleShot(250, lambda: self._check_closed_window(hwnd, retries - 1))
            self.update()
            return
        self.items.pop(index)
        self.icons.pop(hwnd, None)
        if not self.items:
            self.hide()
            activate_window(self.source_hwnd)
            return
        self.selected = index % len(self.items)
        self.announce_selection()
        self.update()
        self.sync_thumbnails()

    def cancel(self) -> None:
        self.hide()
        activate_window(self.source_hwnd)

    def handle(self, action: str) -> None:
        if self.deleting_index is not None:
            return
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
        elif action == "close":
            self.close_selected()
        elif action == "cancel":
            self.cancel()

    def radii(self) -> tuple[float, float, QPoint]:
        outer = self.ring_outer if self.ring_outer is not None else min(350, self.width() * 0.31, self.height() * 0.42)
        return outer, outer * 0.38, QPoint(self.width() // 2, self.height() // 2)

    def sector_at(self, point: QPoint, sticky: int | None = None) -> int | None:
        outer, inner, center = self.radii()
        x, y = point.x() - center.x(), point.y() - center.y()
        distance = math.hypot(x, y)
        if not inner < distance < outer:
            return None
        count = len(self.items)
        angle = (math.atan2(y, x) + math.pi / 2) % (2 * math.pi)
        if count == 1 and min(angle, 2 * math.pi - angle) > math.pi / 3:
            return None
        step = 2 * math.pi / count
        if sticky is not None and count > 1 and 0 <= sticky < count:
            distance_from_selected = (angle - sticky * step + math.pi) % (2 * math.pi) - math.pi
            if abs(distance_from_selected) < step / 2 + min(math.radians(3), step * .12):
                return sticky
        return round(angle / step) % count

    def mouseMoveEvent(self, event) -> None:
        self.hover_at(event.position().toPoint())

    def hover_at(self, point: QPoint) -> None:
        if not self.mouse_switching or self.deleting_index is not None:
            return
        index = self.sector_at(point, self.selected)
        if index is None:
            index = self.item_at(point)
        if index is not None and index != self.selected:
            self.selected = index
            self.announce_selection()
            self.update()
            self.sync_thumbnails()

    def mousePressEvent(self, event) -> None:
        if self.deleting_index is not None:
            return
        point = event.position().toPoint()
        index = self.item_at(point)
        if event.button() == Qt.MouseButton.RightButton:
            if index is not None:
                self.selected = index
                self.close_selected()
            return
        if event.button() == Qt.MouseButton.LeftButton:
            if not self.mouse_switching:
                return
            if index is None:
                self.cancel()
            else:
                self.selected = index
                self.commit()

    def item_at(self, point: QPoint) -> int | None:
        if not self.items:
            return None
        count = len(self.items)
        for local in range(count):
            if self.thumbnail_rect(local, count, local == self.selected).contains(point):
                return local
        return self.sector_at(point)

    def wheelEvent(self, event) -> None:
        if not self.mouse_switching or self.deleting_index is not None:
            return
        self.move_selection(-1 if event.angleDelta().y() > 0 else 1)

    def keyPressEvent(self, event) -> None:
        if self.deleting_index is not None:
            return
        key = event.key()
        if key == Qt.Key.Key_Escape:
            self.cancel()
        elif key == Qt.Key.Key_Tab:
            self.move_selection(1)
        elif key == Qt.Key.Key_QuoteLeft and event.modifiers() & Qt.KeyboardModifier.AltModifier:
            self.move_selection(-1)
        elif key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self.commit()
        elif key == Qt.Key.Key_Delete:
            self.close_selected()

    def paintEvent(self, _event) -> None:
        if not self.items:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        if self.blur_mode != "transparent":
            if self.background:
                painter.drawPixmap(self.rect(), self.background, self.background.rect())
            else:
                gradient = QLinearGradient(0, 0, self.width(), self.height())
                gradient.setColorAt(0, QColor(self.theme["background_start"]))
                gradient.setColorAt(0.48, QColor(self.theme["background_mid"]))
                gradient.setColorAt(1, QColor(self.theme["background_end"]))
                painter.fillRect(self.rect(), gradient)
                glow = QRadialGradient(QPointF(self.width() * .51, self.height() * .42), self.width() * .51)
                glow.setColorAt(0, themed_color(self.theme, "glow", 38))
                glow.setColorAt(1, themed_color(self.theme, "glow", 0))
                painter.fillRect(self.rect(), glow)
            overlay_alpha = {"none": 174, "gaussian": 132, "acrylic": 96, "fast": 146}.get(self.blur_mode, 174)
            painter.fillRect(self.rect(), QColor(3, 9, 18, overlay_alpha))
        outer, inner, center = self.radii()
        visible = self.items
        count = len(visible)
        centerf = QPointF(center)
        labels = []
        transparent = self.blur_mode == "transparent"

        def sector_color(key: str, alpha: int) -> QColor:
            color = themed_color(self.theme, key, alpha)
            if transparent:
                color.setAlpha(alpha)
            return color

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
            deleting = local == self.deleting_index
            if deleting:
                painter.save()
                painter.setOpacity(max(0.0, 1.0 - self.delete_progress))
            if selected:
                painter.setPen(QPen(themed_color(self.theme, "accent", 38), 18))
                painter.drawPath(path)
                painter.setPen(QPen(themed_color(self.theme, "accent_light", 86), 8))
                painter.drawPath(path)
            fill = QRadialGradient(centerf, outer)
            if selected:
                fill.setColorAt(0, sector_color("selected_start", 235 if transparent else 240))
                fill.setColorAt(1, sector_color("selected_end", 235 if transparent else 232))
            else:
                fill.setColorAt(0, sector_color("segment_start", 185 if transparent else 226))
                fill.setColorAt(1, sector_color("segment_end", 185 if transparent else 211))
            painter.setBrush(fill)
            painter.setPen(QPen(themed_color(self.theme, "accent_light", 235) if selected else themed_color(self.theme, "accent", 62), 2 if selected else 1))
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
                self.draw_placeholder(painter, thumb_rect, title, self.icons.get(hwnd),
                                      bool(hwnd and user32.IsIconic(hwnd)), self.language)
            if not selected:
                painter.fillRect(thumb_rect, QColor(2, 8, 17, 65))
            painter.restore()
            painter.setPen(QPen(themed_color(self.theme, "accent_light", 235) if selected else themed_color(self.theme, "accent", 119), 1.5 if selected else 1))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRoundedRect(thumb_rect, 7, 7)
            label_x = thumb_rect.center().x()
            label_y = thumb_rect.bottom() + 18 if math.sin(angle) > 0.1 else thumb_rect.top() - 14
            font = QFont("Microsoft YaHei UI", 10)
            font.setWeight(QFont.Weight.DemiBold if selected else QFont.Weight.Normal)
            painter.setFont(font)
            metrics = QFontMetrics(font)
            text = metrics.elidedText(normalized_window_title(title), Qt.TextElideMode.ElideRight, thumb_rect.width() + 12)
            icon = self.demo_icons.get(title) if self.demo else self.icons.get(hwnd)
            text_width = metrics.horizontalAdvance(text)
            start_x = label_x - (text_width + (26 if icon else 0)) / 2
            icon_rect = None
            if icon:
                icon_rect = QRect(int(start_x), int(label_y - 11), 20, 20)
                start_x += 26
            labels.append((icon, icon_rect, font, text, QPoint(int(start_x), int(label_y + 5)), deleting))

            badge_angle = angle - half * 0.65
            badge_radius = outer - 16
            badge_x = center.x() + badge_radius * math.cos(badge_angle)
            badge_y = center.y() + badge_radius * math.sin(badge_angle)
            painter.setPen(QPen(themed_color(self.theme, "accent_light", 80) if selected else QColor(195, 225, 249, 34), 1))
            painter.setBrush(themed_color(self.theme, "accent", 165) if selected else QColor(113, 135, 165, 65))
            painter.drawEllipse(QPointF(badge_x, badge_y), 11, 11)
            painter.setPen(QColor("#10243b") if selected else QColor("#d3dfef"))
            painter.setFont(QFont("Segoe UI", 8, QFont.Weight.DemiBold))
            painter.drawText(QRectF(badge_x - 10, badge_y - 10, 20, 20), Qt.AlignmentFlag.AlignCenter, str(local + 1))
            if deleting:
                painter.restore()

        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(themed_color(self.theme, "accent", 24), 18))
        painter.drawEllipse(center, int(inner - 7), int(inner - 7))
        core = QRadialGradient(centerf, inner)
        core.setColorAt(0, themed_color(self.theme, "core_start"))
        core.setColorAt(0.75, themed_color(self.theme, "core_mid"))
        core.setColorAt(1, themed_color(self.theme, "core_end"))
        painter.setBrush(core)
        painter.setPen(QPen(themed_color(self.theme, "accent_light", 205), 1.6))
        painter.drawEllipse(center, int(inner - 8), int(inner - 8))
        painter.setPen(Qt.PenStyle.NoPen)
        content_shift = int(inner * 0.12)
        painter.setBrush(QColor("#b6c8e1"))
        painter.drawRoundedRect(QRect(center.x() - 28, center.y() - 49 + content_shift, 40, 34), 5, 5)
        painter.setBrush(QColor("#e1ebfa"))
        painter.drawRoundedRect(QRect(center.x() - 12, center.y() - 39 + content_shift, 40, 34), 5, 5)
        title_rect = QRect(center.x() - int(inner - 18), center.y() + 7 + content_shift,
                           int((inner - 18) * 2), int(inner * 0.62))
        title_flags = Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap
        font = QFont("Microsoft YaHei UI", 11, QFont.Weight.DemiBold)
        title = normalized_window_title(self.items[self.selected][1])
        for size in range(11, 5, -1):
            font.setPointSize(size)
            if QFontMetrics(font).boundingRect(title_rect, title_flags, title).height() <= title_rect.height():
                break
        painter.setFont(font)
        painter.setPen(QColor("#f0f7ff"))
        painter.drawText(title_rect, title_flags, title)
        mouse_footer = tr("鼠标悬停切换     右键关闭窗口" if self.mouse_switching else "右键关闭窗口", self.language)
        keyboard_footer = tr("     松开 ALT  切换     TAB  下一个     ·/~  上一个     ESC  取消", self.language)
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
        for icon, icon_rect, font, text, text_point, deleting in labels:
            if deleting:
                painter.save()
                painter.setOpacity(max(0.0, 1.0 - self.delete_progress))
            if icon and icon_rect:
                painter.drawPixmap(icon_rect, icon)
            painter.setFont(font)
            painter.setPen(QColor(0, 0, 0, 220))
            painter.drawText(QPoint(text_point.x() + 1, text_point.y() + 1), text)
            painter.setPen(QColor("#ffffff"))
            painter.drawText(text_point, text)
            if deleting:
                painter.restore()

    @staticmethod
    def draw_placeholder(painter: QPainter, rect: QRect, title: str, icon: QPixmap | None,
                         minimized: bool, language: str = "zh_CN") -> None:
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
                         Qt.AlignmentFlag.AlignCenter,
                         tr("已最小化" if minimized else "预览不可用", language))


def tray_icon(theme: str = "blue") -> QIcon:
    colors = THEMES.get(theme, THEMES["blue"])
    pixmap = QPixmap(64, 64)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor(colors["accent"]))
    painter.drawRoundedRect(3, 5, 38, 35, 6, 6)
    painter.setBrush(QColor(colors["accent_light"]))
    painter.drawRoundedRect(23, 25, 38, 35, 6, 6)
    painter.end()
    return QIcon(pixmap)


def autostart_enabled() -> bool:
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, STARTUP_KEY) as key:
            winreg.QueryValueEx(key, APP_NAME)
            return True
    except FileNotFoundError:
        return False


def set_autostart(enabled: bool) -> None:
    import winreg

    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, STARTUP_KEY) as key:
        if enabled:
            if getattr(sys, "frozen", False):
                command = f'"{sys.executable}"'
            else:
                command = f'"{sys.executable}" "{os.path.abspath(__file__)}"'
            winreg.SetValueEx(key, APP_NAME, 0, winreg.REG_SZ, command)
        else:
            try:
                winreg.DeleteValue(key, APP_NAME)
            except FileNotFoundError:
                pass


def launch_as_admin() -> bool:
    arguments = sys.argv[1:] if getattr(sys, "frozen", False) else [os.path.abspath(__file__), *sys.argv[1:]]
    result = shell32.ShellExecuteW(None, "runas", sys.executable,
                                    subprocess.list2cmdline(arguments), None, 1)
    return bool(result and result > 32)


def self_test() -> None:
    import gc
    import weakref
    from PySide6.QtTest import QTest

    assert tr("显示切换", "zh_CN") == "显示切换"
    assert tr("显示切换", "zh_TW") == "顯示切換"
    assert tr("显示切换", "en") == "Show switcher"
    assert tr("窗口映射", "en") == "Window mapping"
    assert all(len(values) == len(LANGUAGES) for values in TRANSLATIONS.values())
    assert version_key(APP_VERSION) == (1, 2, 0)
    assert version_key("v1.10.0") > version_key("v1.9.9")
    app = QApplication.instance() or QApplication([])
    mapping_dialog = NameMappingDialog(None, {})
    mapping_dialog.set_language("en")
    assert mapping_dialog.windowTitle() == "Window mapping"
    assert mapping_dialog.done_button.text() == "Done"
    mapping_dialog.set_language("zh_CN")
    mapping_dialog.show()
    app.processEvents()
    assert mapping_dialog.windowModality() == Qt.WindowModality.NonModal
    assert mapping_dialog.mapping_list.currentRow() == -1
    mapping_dialog.source.setText("demo.exe")
    mapping_dialog.display.setText("演示程序")
    assert mapping_dialog.has_unsaved_changes()
    assert mapping_dialog.save_mapping()
    assert mapping_dialog.mappings["process:demo"] == "演示程序"
    assert not mapping_dialog.has_unsaved_changes()
    mapping_dialog.new_mapping()
    assert mapping_dialog.add_button.text() == "取消新增"
    mapping_dialog.new_mapping()
    assert mapping_dialog.add_button.text() == "新增映射"
    assert mapping_dialog.source.text() == "demo.exe"
    toggle_x = {
        mapping_dialog.mapping_list.itemWidget(mapping_dialog.mapping_list.item(index))
        .findChild(QCheckBox).geometry().x()
        for index in range(mapping_dialog.mapping_list.count())
    }
    assert len(toggle_x) == 1
    assert mapping_dialog.mapping_list.count() == 2
    row_before_delete = mapping_dialog.mapping_list.currentRow()
    QTest.mouseClick(mapping_dialog.delete_button, Qt.MouseButton.LeftButton)
    assert "process:demo" not in mapping_dialog.mappings
    assert mapping_dialog.mapping_list.currentRow() == min(row_before_delete, mapping_dialog.mapping_list.count() - 1)
    assert mapping_dialog.keys() == ["process:msedge"]
    mapping_dialog.source.setText("demo.exe")
    mapping_dialog.display.setText("演示程序")
    assert mapping_dialog.save_mapping()
    mapping_dialog.toggle_mapping("process:demo", False)
    QTest.mouseClick(mapping_dialog.save_button, Qt.MouseButton.LeftButton)
    assert "process:demo" in mapping_dialog.disabled
    assert "process:demo" in mapping_dialog.keys()
    mapping_dialog.toggle_mapping("process:demo", True)
    assert "process:demo" not in mapping_dialog.disabled
    QTest.mouseClick(mapping_dialog.delete_button, Qt.MouseButton.LeftButton)
    assert "process:demo" not in mapping_dialog.keys()
    mapping_dialog.disabled.clear()
    mapping_dialog.hide()
    closed_dialog = NameMappingDialog(None, {})
    closed_dialog.show()
    closed_ref = weakref.ref(closed_dialog)
    closed_dialog.accept()
    app.processEvents()
    del closed_dialog
    gc.collect()
    assert closed_ref() is None, "Closed mapping dialog was retained"
    assert short_window_title("README.md - tab - Visual Studio Code") == "README.md"
    assert short_window_title("Tibo on X: 2026") == "Tibo on X: 2026"
    assert short_window_title("下载和文件资源管理器") == "下载"
    assert short_window_title("Downloads and File Explorer") == "Downloads"
    assert short_window_title("Android Studio") == "Android Studio"
    assert display_window_name("README.md - tab - Visual Studio Code", "code") == "README.md"
    assert display_window_name("Microsoft Edge", "msedge") == "Edge"
    assert display_window_name("Microsoft Edge", "msedge", disabled={"process:msedge"}) == "Microsoft Edge"
    assert display_window_name("ChatGPT", "ChatGPT") == "ChatGPT"
    assert short_window_title("PowerShell 7 (x64)") == "PowerShell 7"
    assert short_window_title("PowerShell 7 (x86)") == "PowerShell 7"
    assert short_window_title("程序 (ARM64)") == "程序"
    assert short_window_title("程序 (64-bit)") == "程序"
    assert short_window_title("PowerShell 以管理员身份运行") == "PowerShell"
    assert short_window_title("程序 (管理员)") == "程序"
    assert short_window_title("程序 [Administrator]") == "程序"
    assert short_window_title("项目 | 编辑器") == "项目"
    assert short_window_title("项目 :: 编辑器") == "项目"
    assert short_window_title("项目 ／ 编辑器") == "项目"
    assert short_window_title("项目 → 编辑器") == "项目"
    assert display_window_name("PowerShell 7 (x64)", "WindowsTerminal") == "PowerShell 7"
    assert display_window_name("README.md - tab - Visual Studio Code - 你好你好", "unknown") == "README.md"
    assert display_window_name("微信", "Weixin", {"process:weixin": "微信客户端"}) == "微信客户端"
    assert display_window_name("ChatGPT", "ChatGPT",
                               {"process:chatgpt": "AI 助手"}) == "AI 助手"
    assert display_window_name("PowerShell 7", "WindowsTerminal",
                               {"process:windowsterminal": "终端"}) == "终端"
    assert display_window_name("Minecraft NeoForge* 26.1.2 - 单人游戏", "java") == "Minecraft NeoForge* 26.1.2"
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
    overlay.remember_foreground(100)
    overlay.remember_foreground(200)
    overlay.remember_foreground(100)
    assert overlay.recent_window_order() == [100, 200]
    for hwnd in range(1, 514):
        overlay.remember_foreground(hwnd)
    assert len(overlay.recent_window_order()) == 512
    overlay.background_result = (overlay.background_generation, (1, 1), b"\0" * 4)
    overlay.refresh_background()
    assert overlay.background_result is None, "Stale background image was retained"
    memory_overlay = RadialOverlay(blur_mode="gaussian")
    memory_overlay.background_source = QPixmap(32, 32)
    memory_overlay.background_source.fill()
    memory_overlay.resize(32, 32)
    blur_started, release_blur = threading.Event(), threading.Event()
    original_blur = globals()["blur_image"]

    def delayed_blur(image: Image.Image, mode: str) -> Image.Image:
        blur_started.set()
        release_blur.wait(2)
        return image

    globals()["blur_image"] = delayed_blur
    try:
        memory_overlay.refresh_background()
        assert blur_started.wait(1)
        for _ in range(20):
            memory_overlay.refresh_background()
        assert sum(thread.name == "background-blur" for thread in threading.enumerate()) == 1
    finally:
        release_blur.set()
        for thread in threading.enumerate():
            if thread.name == "background-blur":
                thread.join(3)
        globals()["blur_image"] = original_blur
    assert not memory_overlay.background_worker
    memory_overlay.blur_mode = "transparent"
    memory_overlay.refresh_background()
    assert memory_overlay.background_request is None

    scan_started, release_scan = threading.Event(), threading.Event()
    original_windows = globals()["windows"]

    def delayed_windows(**_kwargs) -> list[tuple[int, str]]:
        scan_started.set()
        release_scan.wait(2)
        return [(1, "test")]

    globals()["windows"] = delayed_windows
    try:
        memory_overlay.start_window_scan(False)
        assert scan_started.wait(1)
        for _ in range(20):
            memory_overlay.start_window_scan(False)
        assert sum(thread.name == "window-scan" for thread in threading.enumerate()) == 1
    finally:
        release_scan.set()
        for thread in threading.enumerate():
            if thread.name == "window-scan":
                thread.join(3)
        globals()["windows"] = original_windows
    assert not memory_overlay.scan_worker
    memory_overlay.poll_window_scan()
    assert memory_overlay.window_cache == [(1, "test")]
    assert memory_overlay.scan_result is None
    memory_overlay.hide()
    assert order_by_recent([(1, "a"), (2, "b"), (3, "c")], [3, 1]) == [(3, "c"), (1, "a"), (2, "b")]
    assert not tray_icon("blue").isNull()
    overlay.set_theme("purple")
    assert overlay.theme["accent"] == THEMES["purple"]["accent"]
    overlay.set_theme("dark")
    assert overlay.theme["background_end"] == THEMES["dark"]["background_end"]
    overlay.set_theme("transparent")
    assert overlay.theme["accent"] == "#60cdff"
    assert themed_color(overlay.theme, "accent", 200).alpha() == 50
    assert overlay.blur_mode == "none"
    overlay.set_theme("missing")
    assert overlay.theme is THEMES["blue"]
    blur_image_source = Image.effect_noise((48, 32), 64).convert("RGBA")
    blur_outputs = {mode: blur_image(blur_image_source, mode) for mode in BLUR_MODES if mode != "none"}
    assert all(output.size == blur_image_source.size for output in blur_outputs.values())
    assert any(output.tobytes() != blur_image_source.tobytes() for output in blur_outputs.values())
    overlay.set_blur_mode("transparent")
    assert overlay.blur_mode == "transparent"
    overlay.items = [(0, str(i)) for i in range(11)]
    overlay.resize(1200, 800)
    overlay.set_theme("transparent")
    enhanced = overlay.grab().toImage()
    _, inner, center = overlay.radii()
    radius = inner + 30
    selected_point = QPoint(center.x(), round(center.y() - radius))
    normal_angle = -math.pi / 2 + 2 * math.pi / len(overlay.items)
    normal_point = QPoint(round(center.x() + radius * math.cos(normal_angle)),
                          round(center.y() + radius * math.sin(normal_angle)))
    assert all(enhanced.pixelColor(point).alpha() >= 160 for point in (selected_point, normal_point))
    overlay.set_theme("blue")
    assert overlay.sector_at(QPoint(600, 100)) == 0
    assert overlay.sector_at(QPoint(600, 400)) is None
    overlay.selected = 8
    assert overlay.sector_at(QPoint(600, 100)) == 0
    assert overlay.thumbnail_rect(0, 8, True).width() > overlay.thumbnail_rect(0, 8, False).width()
    assert overlay.item_at(overlay.thumbnail_rect(8, len(overlay.items), True).center()) == 8
    overlay.set_mouse_switching(False)
    overlay.hover_at(overlay.thumbnail_rect(0, len(overlay.items), False).center())
    assert overlay.selected == 8
    overlay.set_mouse_switching(True)
    overlay.hover_at(overlay.thumbnail_rect(0, len(overlay.items), False).center())
    assert overlay.selected == 0
    boundary = math.pi / len(overlay.items)

    def boundary_point(offset: int) -> QPoint:
        angle = -math.pi / 2 + boundary + math.radians(offset)
        return QPoint(round(center.x() + radius * math.cos(angle)),
                      round(center.y() + radius * math.sin(angle)))

    overlay.hover_at(boundary_point(1))
    assert overlay.selected == 0, "Hover jittered at a sector boundary"
    overlay.hover_at(boundary_point(5))
    assert overlay.selected == 1
    overlay.hover_at(boundary_point(1))
    assert overlay.selected == 1, "Hover jittered while reversing at a boundary"
    overlay.hover_at(boundary_point(-5))
    assert overlay.selected == 0
    overlay.open(previous=True)
    assert overlay.selected == 7
    assert overlay.width() <= overlay.screen_geometry.width() - 32
    assert overlay.height() <= overlay.screen_geometry.height() - 32
    overlay.set_blur_mode("none")
    assert overlay.geometry() == overlay.screen_geometry
    overlay.set_blur_mode("transparent")
    assert overlay.width() <= overlay.screen_geometry.width() - 32
    assert overlay.height() <= overlay.screen_geometry.height() - 32
    QTest.keyClick(overlay, Qt.Key.Key_Left)
    assert overlay.selected == 7, "Arrow keys should not change the selection"
    overlay.cancel()
    notice = QMessageBox(QMessageBox.Icon.Warning, "管理员运行", "Test", QMessageBox.StandardButton.Ok)
    notice.setWindowModality(Qt.WindowModality.NonModal)
    style_glass_dialog(notice)
    notice.show()
    app.processEvents()
    overlay.open()
    app.processEvents()
    assert overlay.isVisible() and any(hwnd == int(notice.winId()) for hwnd, _ in windows()), (
        "Administrator notice could not be switched to")
    overlay.cancel()
    notice.close()
    source = QWidget()
    source.resize(300, 200)
    source.show()
    test_overlay = RadialOverlay()
    test_overlay.setGeometry(100, 100, 800, 600)
    test_overlay.items = [(int(source.winId()), "DWM test")]
    try:
        test_overlay.show()
        test_overlay.activateWindow()
        app.processEvents()
        test_overlay.sync_thumbnails()
        assert len(test_overlay.thumbnails) == 1, "DWM thumbnail registration failed"
        original_update = dwmapi.DwmUpdateThumbnailProperties
        update_calls = []

        def record_update(*args):
            update_calls.append(True)
            return original_update(*args)

        dwmapi.DwmUpdateThumbnailProperties = record_update
        try:
            test_overlay.sync_thumbnails()
        finally:
            dwmapi.DwmUpdateThumbnailProperties = original_update
        assert not update_calls, "Unchanged thumbnail was updated again"
        source.activateWindow()
        app.processEvents()
        assert not test_overlay.isVisible(), "Inactive switcher stayed on screen"
        test_overlay.show()
        test_overlay.activateWindow()
        app.processEvents()
        test_overlay.sync_thumbnails()
        point = test_overlay.thumbnail_rect(0, 1, True).center()
        QTest.mouseClick(test_overlay, Qt.MouseButton.RightButton, Qt.KeyboardModifier.NoModifier, point)
        QTest.qWait(500)
        assert not source.isVisible(), "Right-click close did not close the target window"
        assert not test_overlay.items, "Closed window stayed in the switcher"

        class RefusingWindow(QWidget):
            def closeEvent(self, event) -> None:
                event.ignore()

        refusing = RefusingWindow()
        refusing.resize(300, 200)
        refusing.show()
        test_overlay.items = [(int(refusing.winId()), "Refuses close")]
        test_overlay.show()
        test_overlay.activateWindow()
        app.processEvents()
        QTest.mouseClick(test_overlay, Qt.MouseButton.RightButton, Qt.KeyboardModifier.NoModifier,
                         test_overlay.thumbnail_rect(0, 1, True).center())
        QTest.qWait(500)
        assert refusing.isVisible() and test_overlay.items[0][0] == int(refusing.winId()), (
            "Rejected close removed a live window")
        refusing.hide()

        owner = QWidget()
        owner.show()
        dialog = QDialog(owner)
        dialog.setModal(True)
        dialog.show()
        nested = QDialog(dialog)
        nested.setModal(True)
        nested.show()
        app.processEvents()
        assert active_owned_dialog(int(owner.winId())) == int(nested.winId()), (
            "Topmost owned dialog was not found")
        test_overlay.items = [(int(owner.winId()), "Blocked owner")]
        test_overlay.show()
        app.processEvents()
        test_overlay.deleting_index = 0
        test_overlay._finish_close()
        assert not test_overlay.isVisible() and owner.isVisible() and nested.isVisible(), (
            "Closing a blocked owner did not return to its dialog")
        nested.close()
        dialog.close()
        owner.close()
    finally:
        test_overlay.hide()
        source.close()
    assert not test_overlay.thumbnails, "DWM thumbnails were not released"
    assert not test_overlay.thumbnail_bounds, "DWM thumbnail positions were retained"
    hook = KeyboardHook(HookEvents())
    hook.start()
    assert hook.installed.wait(2), "Hook did not start"
    assert not hook.error, hook.error
    assert hook.probe(), "Keyboard hook probe could not be sent"
    assert hook.probe_seen.wait(1), "Keyboard hook did not receive the probe"
    first_handle = hook.handle
    for _ in range(70):
        if hook.handle != first_handle:
            break
        QTest.qWait(100)
    assert hook.handle and hook.handle != first_handle, "Keyboard hook was not renewed"
    hook.stop()
    hook.join(2)
    assert not hook.is_alive(), "Hook did not stop"
    test_menu = PersistentMenu()
    test_menu.addAction("Settings")
    test_menu.popup(QPoint(20, 20))
    overlay.opened.connect(test_menu.raise_)
    overlay.open()
    current = int(test_menu.winId())
    for _ in range(512):
        if current == int(overlay.winId()):
            break
        current = int(user32.GetWindow(current, 2) or 0)  # GW_HWNDNEXT
    assert current == int(overlay.winId()), "Settings menu fell behind the overlay"
    test_menu.hide()
    overlay.cancel()
    print("Self-test passed")


def main() -> None:
    parser = argparse.ArgumentParser(description="Radial Alt+Tab for Windows")
    parser.add_argument("--demo", action="store_true", help="show a sample ring without installing a keyboard hook")
    parser.add_argument("--snapshot", metavar="PNG", help="save a demo screenshot and exit")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    mutex = None
    settings = QSettings(APP_NAME, APP_NAME)
    admin_mode = settings.value("admin_mode", True, type=bool)
    if not (args.demo or args.snapshot or args.self_test):
        mutex = kernel32.CreateMutexW(None, False, "Local\\RadialAltTab.SingleInstance")
        if not mutex:
            raise SystemExit(f"Single-instance mutex failed: {ctypes.get_last_error()}")
        if ctypes.get_last_error() == ERROR_ALREADY_EXISTS:
            kernel32.CloseHandle(mutex)
            return
        if admin_mode and not shell32.IsUserAnAdmin():
            kernel32.CloseHandle(mutex)
            mutex = None
            if launch_as_admin():
                return
            mutex = kernel32.CreateMutexW(None, False, "Local\\RadialAltTab.SingleInstance")
            if not mutex:
                raise SystemExit(f"Single-instance mutex failed: {ctypes.get_last_error()}")
            if ctypes.get_last_error() == ERROR_ALREADY_EXISTS:
                kernel32.CloseHandle(mutex)
                return
    set_windows_app_identity()
    app = QApplication(sys.argv[:1])
    app.setQuitOnLastWindowClosed(False)
    app.setApplicationName(APP_NAME)
    app.setApplicationDisplayName("Radial Alt+Tab")
    app.setOrganizationName(APP_NAME)
    language = str(settings.value("language", "zh_CN"))
    if language not in LANGUAGES:
        language = "zh_CN"
    name_mappings = load_name_mappings(settings)
    disabled_name_mappings = load_disabled_name_mappings(settings)
    deleted_name_mappings = load_deleted_name_mappings(settings)
    saved_blur_mode = str(settings.value("blur_mode", "")).casefold()
    if saved_blur_mode not in BLUR_MODES:
        old_blur_setting = str(settings.value("gaussian_blur", "")).casefold()
        if old_blur_setting:
            saved_blur_mode = "gaussian" if old_blur_setting in {"1", "true", "yes", "on"} else "none"
        else:
            saved_blur_mode = "transparent"
    theme_name = str(settings.value("theme", "dark"))
    if theme_name not in THEMES:
        theme_name = "blue"
    app_icon = tray_icon(theme_name)
    app.setWindowIcon(app_icon)
    if args.self_test:
        self_test()
        return
    overlay = RadialOverlay(demo=args.demo or bool(args.snapshot), theme=theme_name,
                            name_mappings=name_mappings,
                            disabled_name_mappings=disabled_name_mappings | deleted_name_mappings,
                            blur_mode=saved_blur_mode, language=language,
                            mouse_switching=settings.value("mouse_switching", True, type=bool))
    if not args.demo and not args.snapshot:
        overlay.start_foreground_tracking()
        app.aboutToQuit.connect(overlay.stop_foreground_tracking)
    if args.snapshot:
        overlay.open()
        QTimer.singleShot(300, lambda: (overlay.grab().save(args.snapshot), app.quit()))
        app.exec()
        return
    if args.demo:
        QTimer.singleShot(0, overlay.open)
    else:
        events = HookEvents()
        events.event.connect(overlay.handle, Qt.ConnectionType.QueuedConnection)
        hook = KeyboardHook(events)
        hook.start()
        if not hook.installed.wait(2):
            raise SystemExit("Keyboard hook startup timed out")
        if hook.error:
            raise SystemExit(hook.error)
        app.aboutToQuit.connect(hook.stop)
        probe_pending = False

        def check_hook() -> None:
            nonlocal probe_pending
            if probe_pending and not hook.probe_seen.is_set():
                hook.reinstall()
            probe_pending = hook.probe()

        hook_watchdog = QTimer(app)
        hook_watchdog.setInterval(1000)
        hook_watchdog.timeout.connect(check_hook)
        hook_watchdog.start()
    tray = QSystemTrayIcon(app_icon, app)
    menu = PersistentMenu()
    show_action = menu.addAction(tr("显示切换", language), overlay.open)
    mapping_dialog: NameMappingDialog | None = None

    def edit_mappings() -> None:
        nonlocal name_mappings, disabled_name_mappings, deleted_name_mappings, mapping_dialog
        if mapping_dialog is not None and mapping_dialog.isVisible():
            mapping_dialog.showNormal()
            mapping_dialog.raise_()
            mapping_dialog.activateWindow()
            return
        mapping_dialog = edit_name_mapping(overlay, settings, name_mappings, disabled_name_mappings,
                                           deleted_name_mappings, language)

        def apply_mappings() -> None:
            nonlocal name_mappings, disabled_name_mappings, deleted_name_mappings
            name_mappings = dict(mapping_dialog.mappings)
            disabled_name_mappings = set(mapping_dialog.disabled)
            deleted_name_mappings = set(mapping_dialog.deleted)
            overlay.name_mappings = dict(name_mappings)
            overlay.disabled_name_mappings = disabled_name_mappings | deleted_name_mappings

        def clear_mapping_dialog() -> None:
            nonlocal mapping_dialog
            mapping_dialog = None

        mapping_dialog.accepted.connect(apply_mappings)
        mapping_dialog.finished.connect(clear_mapping_dialog)

    mapping_action = menu.addAction(tr("窗口映射", language), edit_mappings)

    blur_menu = PersistentMenu(tr("背景模糊", language), menu)
    menu.addMenu(blur_menu)
    blur_group = QActionGroup(blur_menu)
    blur_group.setExclusive(True)
    blur_actions = {}

    def apply_blur_mode(mode: str) -> None:
        settings.setValue("blur_mode", mode)
        overlay.set_blur_mode(mode)
        for key, action in blur_actions.items():
            action.setChecked(key == mode)

    for mode, label in BLUR_MODES.items():
        action = blur_menu.addAction(tr(label, language))
        action.setCheckable(True)
        action.triggered.connect(lambda _checked=False, mode=mode: apply_blur_mode(mode))
        blur_group.addAction(action)
        blur_actions[mode] = action
    blur_actions[saved_blur_mode].setChecked(True)

    theme_menu = PersistentMenu(tr("主题颜色", language), menu)
    menu.addMenu(theme_menu)
    theme_actions = {}

    def apply_theme(name: str) -> None:
        settings.setValue("theme", name)
        overlay.set_theme(name)
        icon = tray_icon(name)
        tray.setIcon(icon)
        app.setWindowIcon(icon)
        for key, action in theme_actions.items():
            action.setChecked(key == name)

    for key, theme in THEMES.items():
        action = theme_menu.addAction(tr(theme["label"], language))
        action.setCheckable(True)
        action.triggered.connect(lambda _checked=False, key=key: apply_theme(key))
        theme_actions[key] = action
    apply_theme(theme_name)

    language_menu = PersistentMenu(tr("语言", language), menu)
    language_group = QActionGroup(language_menu)
    language_group.setExclusive(True)
    language_actions = {}
    language_labels = {"zh_CN": "简体中文", "zh_TW": "繁體中文", "en": "English"}
    for code, label in language_labels.items():
        action = language_menu.addAction(label)
        action.setCheckable(True)
        action.triggered.connect(lambda _checked=False, code=code: apply_language(code))
        language_group.addAction(action)
        language_actions[code] = action

    mouse_action = menu.addAction(tr("鼠标切换", language))
    mouse_action.setCheckable(True)
    mouse_action.setChecked(overlay.mouse_switching)

    def toggle_mouse_switching(enabled: bool) -> None:
        settings.setValue("mouse_switching", enabled)
        overlay.set_mouse_switching(enabled)

    mouse_action.toggled.connect(toggle_mouse_switching)
    admin_action = menu.addAction(tr("管理员运行", language))
    admin_action.setCheckable(True)
    admin_action.setChecked(admin_mode)
    admin_notices: set[QMessageBox] = set()

    def show_admin_notice(text: str, icon: QMessageBox.Icon = QMessageBox.Icon.Warning) -> None:
        box = QMessageBox(icon, tr("管理员运行", language), text, QMessageBox.StandardButton.Ok)
        box.setWindowModality(Qt.WindowModality.NonModal)
        box.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        admin_notices.add(box)
        box.finished.connect(lambda _result: admin_notices.discard(box))
        style_glass_dialog(box)
        box.show()

    def toggle_admin_mode(enabled: bool) -> None:
        settings.setValue("admin_mode", enabled)
        menu.hide()
        if enabled:
            show_admin_notice(tr("管理员模式设置将在下次启动时生效。", language),
                              QMessageBox.Icon.Information)
        else:
            show_admin_notice(tr("普通权限运行时，切换管理员窗口（如任务管理器）可能异常，并可能出现 Windows 原生切换界面。", language)
                              + "\n" + tr("管理员模式设置将在下次启动时生效。", language))

    admin_action.toggled.connect(toggle_admin_mode)

    def keep_menus_above_overlay() -> None:
        for part in (menu, blur_menu, theme_menu, language_menu):
            if part.isVisible():
                part.raise_()

    overlay.opened.connect(keep_menus_above_overlay)

    mouse_preview_timer = QTimer(app)
    mouse_preview_timer.setInterval(16)

    def preview_menu_mouse() -> None:
        if not overlay.isVisible():
            return
        position = QCursor.pos()
        if any(part.isVisible() and part.geometry().contains(position)
               for part in (menu, blur_menu, theme_menu, language_menu)):
            return
        overlay.hover_at(overlay.mapFromGlobal(position))

    mouse_preview_timer.timeout.connect(preview_menu_mouse)
    menu.aboutToShow.connect(mouse_preview_timer.start)
    menu.aboutToHide.connect(mouse_preview_timer.stop)

    update_results = SimpleQueue()
    update_timer = QTimer(app)
    update_timer.setInterval(100)

    def show_update_result(status: str, latest: str) -> None:
        update_action.setEnabled(True)
        if status == "error":
            QMessageBox.warning(None, tr("检查更新", language),
                                tr("连接 GitHub 失败，请检查网络后重试。", language))
        elif status == "current":
            QMessageBox.information(None, tr("检查更新", language),
                                    tr("当前版本 {current} 已是最新版本。", language).format(current=APP_VERSION))
        elif status == "ahead":
            QMessageBox.information(None, tr("检查更新", language),
                                    tr("当前版本 {current} 不低于最新发行版 {latest}。", language).format(
                                        current=APP_VERSION, latest=latest))
        else:
            box = QMessageBox(QMessageBox.Icon.Question, tr("发现新版本", language),
                              tr("发现新版本 {latest}（当前版本 {current}）。现在打开下载页？", language).format(
                                  latest=latest, current=APP_VERSION))
            open_page = box.addButton(tr("是", language), QMessageBox.ButtonRole.AcceptRole)
            box.addButton(tr("否", language), QMessageBox.ButtonRole.RejectRole)
            box.setDefaultButton(open_page)
            box.exec()
            if box.clickedButton() is open_page:
                webbrowser.open(LATEST_RELEASE_PAGE)

    def poll_update_result() -> None:
        try:
            status, latest = update_results.get_nowait()
        except Empty:
            return
        update_timer.stop()
        show_update_result(status, latest)

    update_timer.timeout.connect(poll_update_result)

    def check_for_updates() -> None:
        update_action.setEnabled(False)

        def request_latest() -> None:
            try:
                request = urllib.request.Request(
                    LATEST_RELEASE_API,
                    headers={"Accept": "application/vnd.github+json", "User-Agent": APP_NAME},
                )
                with urllib.request.urlopen(request, timeout=8) as response:
                    latest = json.load(response)["tag_name"]
                latest_version = version_key(latest)
                current_version = version_key(APP_VERSION)
                status = "current" if latest_version == current_version else (
                    "update" if latest_version > current_version else "ahead")
                update_results.put((status, latest))
            except (OSError, ValueError, KeyError, TypeError):
                update_results.put(("error", ""))

        update_timer.start()
        threading.Thread(target=request_latest, name="update-check", daemon=True).start()

    startup_action = menu.addAction(tr("开机启动", language))
    startup_action.setCheckable(True)
    startup_action.setChecked(autostart_enabled())

    def toggle_autostart(enabled: bool) -> None:
        try:
            set_autostart(enabled)
        except OSError as error:
            startup_action.blockSignals(True)
            startup_action.setChecked(not enabled)
            startup_action.blockSignals(False)
            tray.showMessage("Radial Alt+Tab", tr("开机启动设置失败：{error}", language).format(error=error),
                             tray.icon(), 4000)

    startup_action.toggled.connect(toggle_autostart)
    update_action = menu.addAction(tr("检查更新", language))
    update_action.triggered.connect(check_for_updates)
    menu.addMenu(language_menu)
    menu.addSeparator()
    exit_action = menu.addAction(tr("退出", language), app.quit)

    def apply_language(code: str) -> None:
        nonlocal language
        if code not in LANGUAGES:
            return
        language = code
        settings.setValue("language", code)
        overlay.set_language(code)
        if mapping_dialog is not None:
            mapping_dialog.set_language(code)
        show_action.setText(tr("显示切换", code))
        mapping_action.setText(tr("窗口映射", code))
        blur_menu.setTitle(tr("背景模糊", code))
        for key, action in blur_actions.items():
            action.setText(tr(BLUR_MODES[key], code))
        theme_menu.setTitle(tr("主题颜色", code))
        for key, action in theme_actions.items():
            action.setText(tr(THEMES[key]["label"], code))
        language_menu.setTitle(tr("语言", code))
        update_action.setText(tr("检查更新", code))
        startup_action.setText(tr("开机启动", code))
        admin_action.setText(tr("管理员运行", code))
        mouse_action.setText(tr("鼠标切换", code))
        exit_action.setText(tr("退出", code))
        language_actions[code].setChecked(True)

    language_actions[language].setChecked(True)
    tray.activated.connect(
        lambda reason: menu.popup(QCursor.pos())
        if reason == QSystemTrayIcon.ActivationReason.Context else None
    )
    tray.setToolTip("Radial Alt+Tab")
    tray.show()
    if not args.demo:
        tray.showMessage("Radial Alt+Tab", tr("程序已运行，已驻留系统托盘。按 Alt+Tab 呼出切换器。", language),
                         app_icon, 4000)
        if not shell32.IsUserAnAdmin():
            QTimer.singleShot(0, lambda: show_admin_notice(
                tr("普通权限运行时，切换管理员窗口（如任务管理器）可能异常，并可能出现 Windows 原生切换界面。", language)))
    try:
        app.exec()
    finally:
        if mutex:
            hook.stop()
            hook.join(2)
            kernel32.CloseHandle(mutex)


if __name__ == "__main__":
    main()
