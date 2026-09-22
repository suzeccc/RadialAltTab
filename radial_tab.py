"""A small Windows radial Alt+Tab switcher."""

from __future__ import annotations

import argparse
import ctypes
from ctypes import wintypes
import json
import math
import os
from queue import Empty, SimpleQueue
import sys
import threading

from PIL import Image, ImageFilter
import numpy as np
from PySide6.QtCore import QObject, QPoint, QPointF, QRect, QRectF, QSize, Qt, QTimer, Signal, QVariantAnimation
from PySide6.QtCore import QSettings
from PySide6.QtCore import QEasingCurve, QPropertyAnimation
from PySide6.QtGui import (
    QActionGroup, QColor, QCursor, QFont, QFontMetrics, QGuiApplication, QIcon,
    QImage, QLinearGradient, QPainter, QPainterPath, QPen, QPixmap, QRadialGradient,
)
from PySide6.QtWidgets import (
    QApplication, QDialog, QDialogButtonBox, QFileDialog, QFormLayout,
    QHBoxLayout,
    QCheckBox, QFrame, QLabel, QLineEdit, QListWidget, QListWidgetItem, QMessageBox, QPushButton, QMenu,
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
user32.SwitchToThisWindow.argtypes = [HWND, wintypes.BOOL]
user32.SwitchToThisWindow.restype = None
user32.ShowWindow.argtypes = [HWND, ctypes.c_int]
user32.IsIconic.argtypes = [HWND]
user32.IsWindow.argtypes = [HWND]
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
user32.TranslateMessage.argtypes = [ctypes.POINTER(MSG)]
user32.DispatchMessageW.argtypes = [ctypes.POINTER(MSG)]
user32.PostThreadMessageW.argtypes = [wintypes.DWORD, wintypes.UINT, wintypes.WPARAM, LPARAM]
user32.PostMessageW.argtypes = [HWND, wintypes.UINT, wintypes.WPARAM, LPARAM]
user32.PostMessageW.restype = wintypes.BOOL
user32.GetAsyncKeyState.argtypes = [ctypes.c_int]
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

VK_TAB, VK_ESCAPE, VK_MENU, VK_OEM_3, VK_DELETE = 0x09, 0x1B, 0x12, 0xC0, 0x2E
VK_LEFT, VK_UP, VK_RIGHT, VK_DOWN = 0x25, 0x26, 0x27, 0x28
VK_LMENU, VK_RMENU = 0xA4, 0xA5
WM_KEYDOWN, WM_KEYUP, WM_SYSKEYDOWN, WM_SYSKEYUP = 0x100, 0x101, 0x104, 0x105
WM_CLOSE, WM_SYSCOMMAND, WM_QUIT, WH_KEYBOARD_LL = 0x10, 0x112, 0x12, 13
SC_CLOSE = 0xF060
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
STARTUP_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
PROCESS_LABELS = {"msedge": "Edge"}
BLUR_MODES = {
    "none": "关闭模糊",
    "transparent": "透明背景",
    "gaussian": "高斯模糊",
    "acrylic": "亚克力背景",
    "fast": "快速模糊",
    "bilateral": "双边模糊",
}
def load_name_mappings(settings: QSettings) -> dict[str, str]:
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
        if source:
            mappings[f"process:{source.casefold()}"] = str(value).strip()
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
        if source:
            deleted.add(f"process:{source.casefold()}")
    return deleted


def save_deleted_name_mappings(settings: QSettings, deleted: set[str]) -> None:
    process_deleted = set()
    for key in deleted:
        if not key.startswith("process:"):
            continue
        source = executable_name(key.split(":", 1)[1])
        if source:
            process_deleted.add(f"process:{source.casefold()}")
    settings.setValue("deleted_name_mappings", json.dumps(sorted(process_deleted), ensure_ascii=False))


class MappingRow(QWidget):
    clicked = Signal()

    def mousePressEvent(self, event) -> None:
        self.clicked.emit()
        super().mousePressEvent(event)


class NameMappingDialog(QDialog):
    def __init__(self, parent: QWidget, current: dict[str, str], disabled: set[str] | None = None,
                 deleted: set[str] | None = None):
        super().__init__(None)
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

        self.setWindowTitle("名称映射")
        self.setWindowModality(Qt.WindowModality.NonModal)
        self.setWindowFlag(Qt.WindowType.Window, True)
        self.setWindowFlag(Qt.WindowType.WindowMinimizeButtonHint, True)
        self.setWindowFlag(Qt.WindowType.WindowMaximizeButtonHint, False)
        self.setWindowFlag(Qt.WindowType.WindowCloseButtonHint, True)
        self.setMinimumWidth(560)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("输入进程，可自定义设置在切换界面中显示的名称。"))
        layout.addWidget(QLabel("可在任务管理器里查找进程位置。"))

        self.mapping_list = QListWidget()
        self.mapping_list.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.mapping_list.currentItemChanged.connect(self.load_selected)
        layout.addWidget(self.mapping_list)

        form = QFormLayout()
        self.source = QLineEdit()
        self.source.setPlaceholderText("例如：code.exe；可在任务管理器查看")
        self.display = QLineEdit()
        self.display.setPlaceholderText("例如：微信或 PowerShell 7")
        display_row = QWidget()
        display_layout = QHBoxLayout(display_row)
        display_layout.setContentsMargins(0, 0, 0, 0)
        display_layout.addWidget(self.display)
        identify_button = QPushButton("识别产品名")
        identify_button.clicked.connect(self.identify_product_name)
        display_layout.addWidget(identify_button)
        source_row = QWidget()
        source_layout = QHBoxLayout(source_row)
        source_layout.setContentsMargins(0, 0, 0, 0)
        source_layout.addWidget(self.source)
        choose_button = QPushButton("选择文件…")
        choose_button.clicked.connect(self.choose_process)
        source_layout.addWidget(choose_button)
        form.addRow("输入进程", source_row)
        form.addRow("输入显示名称", display_row)
        layout.addLayout(form)

        actions = QHBoxLayout()
        self.add_button = QPushButton("新增映射")
        self.save_button = QPushButton("保存")
        self.delete_button = QPushButton("删除映射")
        self.delete_button.setToolTip("删除当前映射并从列表中移除")
        self.add_button.clicked.connect(self.new_mapping)
        self.save_button.clicked.connect(self.save_mapping)
        self.delete_button.clicked.connect(self.delete_mapping)
        actions.addWidget(self.add_button)
        actions.addWidget(self.delete_button)
        actions.addWidget(self.save_button)
        actions.addStretch()
        layout.addLayout(actions)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("完成")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.refresh()

    def choose_process(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "选择进程文件", "", "可执行文件 (*.exe);;所有文件 (*.*)")
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
            QMessageBox.information(self, "识别产品名", "找不到对应的进程文件，请先选择 EXE 文件或启动该程序。")
            return
        product_name = file_product_name(path)
        if not product_name:
            QMessageBox.information(self, "识别产品名", "无法读取该进程的产品名。")
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
        box.setWindowTitle("名称映射")
        box.setText("有未保存的修改。")
        box.setInformativeText("要保存修改后退出吗？")
        save = box.addButton("保存并退出", QMessageBox.ButtonRole.AcceptRole)
        discard = box.addButton("放弃修改", QMessageBox.ButtonRole.DestructiveRole)
        box.addButton("继续编辑", QMessageBox.ButtonRole.RejectRole)
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
        return f"进程：{source}.exe  →  {self.mappings.get(key, self.defaults.get(key, ''))}"

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
            toggle.setToolTip("仅开启或关闭映射，不会删除映射")
            toggle.setAccessibleName("开启或关闭映射（不删除）")

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
        self.add_button.setText("新增映射")
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
        self.add_button.setText("取消新增")
        self.source.setFocus()

    def cancel_new_mapping(self) -> None:
        previous = self.previous_selected_key
        self.new_mode = False
        self.previous_selected_key = ""
        self.selected_key = previous
        self.add_button.setText("新增映射")
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
            QMessageBox.warning(self, "名称映射", "请填写输入进程和输入显示名称。")
            return False
        key = f"process:{source.casefold()}"
        if key in self.mappings and key != self.selected_key:
            QMessageBox.information(self, "名称映射", "这个进程已经存在映射，不能重复添加。请直接选择已有映射进行修改。")
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
            QMessageBox.information(self, "名称映射", "请先选择一条映射。")
            return
        row = self.mapping_list.currentRow()
        if key in self.defaults:
            self.mappings.pop(key, None)
            self.deleted.add(key)
        elif key in self.mappings:
            self.mappings.pop(key, None)
        else:
            QMessageBox.information(self, "名称映射", "这条映射已经不存在。")
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
                      deleted: set[str]) -> NameMappingDialog:
    dialog = NameMappingDialog(parent, current, disabled, deleted)
    dialog.accepted.connect(lambda: (save_name_mappings(settings, dialog.mappings),
                                      save_disabled_name_mappings(settings, dialog.disabled),
                                      save_deleted_name_mappings(settings, dialog.deleted)))
    dialog.show()
    dialog.raise_()
    dialog.activateWindow()
    return dialog


def onboarding_position(menu_geometry: QRect, area: QRect, width: int, height: int,
                        gap: int = 16) -> QPoint:
    return QPoint(
        max(area.left(), min(menu_geometry.left() - width - gap, area.right() - width)),
        max(area.top(), min(menu_geometry.top(), area.bottom() - height)),
    )


class OnboardingGuide(QWidget):
    STEPS = (
        (None, "设置就在这里", "右键系统托盘图标，可以打开 Radial Alt+Tab 的设置菜单。"),
        ("mapping", "名称映射", "修改窗口在切换器里显示的名称，也可以隐藏不需要的窗口。"),
        ("blur", "背景模糊", "调整切换器的背景效果，让画面更清晰或更柔和。"),
        ("theme", "主题颜色", "选择你喜欢的环形切换器主题颜色。"),
        ("startup", "开机启动", "打开后，Windows 启动时会自动运行 Radial Alt+Tab。"),
    )

    def __init__(self, tray: QSystemTrayIcon, menu: QMenu, settings: QSettings,
                 actions: dict[str, object]):
        super().__init__(None, Qt.WindowType.FramelessWindowHint | Qt.WindowType.Tool |
                         Qt.WindowType.WindowStaysOnTopHint)
        self.tray = tray
        self.menu = menu
        self.settings = settings
        self.actions = actions
        self.menu_flags = self.menu.windowFlags()
        self.step = 0
        self.guide_position: QPoint | None = None
        self.highlight = QFrame(self.menu)
        self.highlight.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.highlight.setStyleSheet(
            "QFrame { background: transparent; border: 2px solid #ffffff; border-radius: 5px; }"
        )
        self.highlight.hide()
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setFixedSize(360, 190)
        self.setStyleSheet("""
            QWidget#guide {
                background: #132037;
                border: 1px solid #57b4ff;
                border-radius: 14px;
            }
            QLabel { border: none; }
            QLabel#eyebrow { color: #72caff; font-size: 12px; }
            QLabel#title { color: #f1f7ff; font-size: 21px; font-weight: 600; }
            QLabel#body { color: #c9d9ec; font-size: 14px; }
            QLabel#progress { color: #72caff; font-size: 12px; }
            QPushButton {
                color: #ffffff;
                background: #2d8fe0;
                border: none;
                border-radius: 8px;
                padding: 7px 16px;
            }
            QPushButton:hover { background: #45a1ed; }
            QPushButton#skip {
                color: #9bb1c9;
                background: transparent;
                padding: 7px 8px;
            }
            QPushButton#skip:hover { color: #d8e7f6; }
        """)
        self.setObjectName("guide")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 18, 22, 16)
        layout.setSpacing(8)
        self.eyebrow = QLabel("新手指引")
        self.eyebrow.setObjectName("eyebrow")
        self.title = QLabel()
        self.title.setObjectName("title")
        self.body = QLabel()
        self.body.setObjectName("body")
        self.body.setWordWrap(True)
        layout.addWidget(self.eyebrow)
        layout.addWidget(self.title)
        layout.addWidget(self.body)
        buttons = QHBoxLayout()
        buttons.setContentsMargins(0, 6, 0, 0)
        self.progress = QLabel()
        self.progress.setObjectName("progress")
        skip = QPushButton("跳过")
        skip.setObjectName("skip")
        skip.clicked.connect(self.finish)
        self.next_button = QPushButton("下一步")
        self.next_button.clicked.connect(self.next_step)
        buttons.addWidget(self.progress)
        buttons.addStretch()
        buttons.addWidget(skip)
        buttons.addWidget(self.next_button)
        layout.addLayout(buttons)

    def start(self) -> None:
        self.step = 0
        self.guide_position = None
        self.setFixedSize(360, 190)
        self.render_step()

    def next_step(self) -> None:
        if self.step == len(self.STEPS) - 1:
            self.finish()
            return
        self.step += 1
        self.render_step()

    def finish(self) -> None:
        self.settings.setValue("onboarding_done", True)
        self.menu.close()
        self.menu.setWindowFlags(self.menu_flags)
        self.highlight.hide()
        self.guide_position = None
        self.close()

    def render_step(self) -> None:
        key, title, body = self.STEPS[self.step]
        self.title.setText(title)
        self.body.setText(body)
        self.progress.setText(f"{self.step + 1} / {len(self.STEPS)}")
        self.next_button.setText("完成" if self.step == len(self.STEPS) - 1 else "下一步")
        self.adjustSize()
        self.show()
        self.raise_()
        self.show_guide_menu()
        if key is None:
            self.highlight.hide()
            QTimer.singleShot(0, self.place_beside_menu)
            return
        QTimer.singleShot(0, lambda key=key: self.highlight_action(key))

    def show_guide_menu(self) -> None:
        flags = ((self.menu_flags & ~Qt.WindowType.Popup) |
                 Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint |
                 Qt.WindowType.WindowStaysOnTopHint)
        if self.menu.windowFlags() != flags:
            self.menu.setWindowFlags(flags)
        self.menu.adjustSize()
        self.menu.move(self.menu_position())
        self.menu.show()
        self.menu.raise_()

    def highlight_action(self, key: str) -> None:
        action = self.actions[key]
        rect = self.menu.actionGeometry(action)
        if rect.isValid():
            self.highlight.setGeometry(rect.adjusted(4, 2, -4, -2))
            self.highlight.raise_()
            self.highlight.show()
        self.place_beside_menu()

    def place_beside_menu(self) -> None:
        if self.menu.isVisible():
            if self.guide_position is not None:
                self.move(self.guide_position)
                return
            geometry = self.menu.frameGeometry()
            screen = QGuiApplication.screenAt(geometry.center()) or QGuiApplication.primaryScreen()
            area = screen.availableGeometry()
            gap = 16
            position = onboarding_position(geometry, area, self.width(), self.height(), gap)
            self.move(position)
            self.guide_position = QPoint(self.pos())

    def menu_position(self) -> QPoint:
        size = self.menu.sizeHint()
        anchor = self.tray.geometry()
        point = (QPoint(anchor.center().x() - size.width() // 2, anchor.top() - size.height() - 8)
                 if anchor.isValid() else QCursor.pos() - QPoint(size.width() // 2, size.height() // 2))
        screen = QGuiApplication.screenAt(anchor.center()) or QGuiApplication.primaryScreen()
        area = screen.availableGeometry()
        return QPoint(max(area.left(), min(point.x(), area.right() - size.width())),
                      max(area.top(), min(point.y(), area.bottom() - size.height())))

THEMES = {
    "blue": {
        "label": "深海蓝", "accent": "#5bbaff", "accent_light": "#8dd2ff",
        "selected_start": "#123658", "selected_end": "#245b8f", "glow": "#416697",
        "segment_start": "#1b283b", "segment_end": "#202d41",
        "core_start": "#142740", "core_mid": "#0b1729", "core_end": "#07111f",
    },
    "purple": {
        "label": "暮紫", "accent": "#c39cff", "accent_light": "#e0c8ff",
        "selected_start": "#39264f", "selected_end": "#704a9c", "glow": "#7653a5",
        "segment_start": "#2b2438", "segment_end": "#3b304d",
        "core_start": "#2a2040", "core_mid": "#19132c", "core_end": "#100b1d",
    },
    "green": {
        "label": "薄荷绿", "accent": "#6de0bd", "accent_light": "#a5f2dc",
        "selected_start": "#16483e", "selected_end": "#277d69", "glow": "#3b9e83",
        "segment_start": "#203a38", "segment_end": "#2d4d49",
        "core_start": "#153732", "core_mid": "#0d2724", "core_end": "#071a19",
    },
}


def themed_color(theme: dict[str, str], key: str, alpha: int = 255) -> QColor:
    color = QColor(theme[key])
    color.setAlpha(alpha)
    return color


def set_windows_app_identity() -> None:
    shell32.SetCurrentProcessExplicitAppUserModelID("SuZe.RadialAltTab")


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


def display_window_name(title: str, executable: str, process_count: int,
                        mappings: dict[str, str] | None = None,
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
    found: list[tuple[int, str, str, str]] = []

    @WNDPROC
    def visit(hwnd: int, _unused: int) -> bool:
        if hwnd == exclude or not user32.IsWindowVisible(hwnd):
            return True
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
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
            app_key = path.casefold() if path else executable.casefold()
            found.append((int(hwnd), title, executable, app_key))
        return True

    user32.EnumWindows(visit, 0)
    found = order_by_recent(found, recent_order)
    counts: dict[str, int] = {}
    for _, _, _, app_key in found:
        if app_key:
            counts[app_key] = counts.get(app_key, 0) + 1
    return [(hwnd, display_window_name(title, executable, counts.get(app_key, 0), mappings, disabled))
            for hwnd, title, executable, app_key in found]


def qpixmap_to_pil(pixmap: QPixmap) -> Image.Image:
    image = pixmap.toImage().convertToFormat(QImage.Format.Format_RGBA8888)
    return Image.frombytes("RGBA", (image.width(), image.height()), image.bits().tobytes())


def pil_to_qpixmap(image: Image.Image) -> QPixmap:
    return QPixmap.fromImage(pil_image_to_qimage(image))


def bilateral_blur_image(image: Image.Image) -> Image.Image:
    """Edge-preserving bilateral filter on a reduced image, then restore size."""
    source = image.convert("RGBA")
    width, height = source.size
    scale = max(1, max(width, height) // 480)
    small_size = (max(1, width // scale), max(1, height // scale))
    small = source.resize(small_size, Image.Resampling.BILINEAR)
    rgba = np.asarray(small, dtype=np.float32)
    rgb = rgba[..., :3]
    alpha = rgba[..., 3:4]
    radius = 2
    sigma_space = 2.0
    sigma_color = 38.0
    padded = np.pad(rgb, ((radius, radius), (radius, radius), (0, 0)), mode="edge")
    result = np.zeros_like(rgb)
    weights_total = np.zeros(rgb.shape[:2], dtype=np.float32)
    for dy in range(-radius, radius + 1):
        for dx in range(-radius, radius + 1):
            sample = padded[radius + dy:radius + dy + rgb.shape[0],
                            radius + dx:radius + dx + rgb.shape[1]]
            spatial = math.exp(-(dx * dx + dy * dy) / (2 * sigma_space * sigma_space))
            color = np.sum((sample - rgb) ** 2, axis=2)
            weights = spatial * np.exp(-color / (2 * sigma_color * sigma_color))
            result += sample * weights[..., None]
            weights_total += weights
    result = np.clip(result / np.maximum(weights_total[..., None], 1e-6), 0, 255).astype(np.uint8)
    filtered = Image.fromarray(np.concatenate((result, alpha.astype(np.uint8)), axis=2), "RGBA")
    return filtered.resize(source.size, Image.Resampling.LANCZOS)


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
    if mode == "bilateral":
        return bilateral_blur_image(image)
    return image


def gaussian_blur_pixmap(pixmap: QPixmap, radius: float = 14.0) -> QPixmap:
    if pixmap.isNull():
        return pixmap
    return pil_to_qpixmap(qpixmap_to_pil(pixmap).filter(ImageFilter.GaussianBlur(radius)))


def blur_pixmap(pixmap: QPixmap, mode: str) -> QPixmap:
    return pil_to_qpixmap(blur_image(qpixmap_to_pil(pixmap), mode)) if mode != "none" else pixmap


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
        self.alt_down = False
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
                key = event.vkCode
                down = message in (WM_KEYDOWN, WM_SYSKEYDOWN)
                up = message in (WM_KEYUP, WM_SYSKEYUP)
                if key in (VK_MENU, VK_LMENU, VK_RMENU):
                    if down:
                        self.alt_down = True
                    elif up:
                        self.alt_down = False
                alt_pressed = self.alt_down or bool(event.flags & LLKHF_ALTDOWN)
                alt_pressed = alt_pressed or message in (WM_SYSKEYDOWN, WM_SYSKEYUP)
                alt_pressed = alt_pressed or bool(user32.GetAsyncKeyState(VK_MENU) & 0x8000)
                alt_pressed = alt_pressed or bool(user32.GetAsyncKeyState(VK_LMENU) & 0x8000)
                alt_pressed = alt_pressed or bool(user32.GetAsyncKeyState(VK_RMENU) & 0x8000)
                if key == VK_TAB and (self.engaged or alt_pressed):
                    if down:
                        first = not self.engaged
                        self.engaged = True
                        emit("open" if first else "next")
                    return 1
                if key == VK_OEM_3 and (self.engaged or self.alt_down or user32.GetAsyncKeyState(VK_MENU) & 0x8000):
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
                    elif key in (VK_LEFT, VK_UP, VK_RIGHT, VK_DOWN):
                        if down:
                            emit({VK_LEFT: "left", VK_UP: "up", VK_RIGHT: "right", VK_DOWN: "down"}[key])
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
    def __init__(self, demo: bool = False, theme: str = "blue", name_mappings: dict[str, str] | None = None,
                 disabled_name_mappings: set[str] | None = None, blur_mode: str = "none"):
        super().__init__(None, Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.Tool)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAccessibleDescription("左键选择窗口，右键关闭窗口，按 Tab 选择下一个窗口，按 ·/~ 选择上一个窗口，松开 Alt 切换，按 Esc 取消")
        self.demo = demo
        self.theme = THEMES.get(theme, THEMES["blue"])
        self.name_mappings = dict(name_mappings or {})
        self.disabled_name_mappings = set(disabled_name_mappings or ())
        self.blur_mode = blur_mode if blur_mode in BLUR_MODES else "none"
        self.blur_enabled = self.blur_mode != "none"
        self.items: list[tuple[int, str]] = []
        self.icons: dict[int, QPixmap] = {}
        self.demo_icons: dict[str, QPixmap] = {}
        self.demo_previews: dict[str, QPixmap] = {}
        self.thumbnails: dict[int, ctypes.c_void_p] = {}
        self.window_cache: list[tuple[int, str]] = []
        self.scan_results = SimpleQueue()
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
        self.background_results = SimpleQueue()
        self.background_generation = 0
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
            self._recent_windows = [hwnd] + [item for item in self._recent_windows if item != hwnd]

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
        self.blur_enabled = self.blur_mode != "none"
        if self.isVisible():
            self.refresh_background()

    def set_blur_enabled(self, enabled: bool) -> None:
        self.set_blur_mode("gaussian" if enabled else "none")

    def refresh_background(self) -> None:
        self.background_generation += 1
        if self.demo or self.blur_mode == "transparent":
            self.background = None
            self.background_pending = False
            self.background_timer.stop()
            self.update()
            return
        if self.background_source is None or self.background_source.isNull():
            return
        background = self.background_source.scaled(
            self.size(), Qt.AspectRatioMode.IgnoreAspectRatio,
            Qt.TransformationMode.SmoothTransformation)
        self.background = background
        generation = self.background_generation
        self.background_pending = self.blur_mode != "none"
        if not self.background_pending:
            self.background_timer.stop()
            self.update()
            return
        image = qpixmap_to_pil(background)
        mode = self.blur_mode

        def render() -> None:
            try:
                result = blur_image(image, mode)
                payload = (result.size, result.tobytes("raw", "RGBA"))
            except Exception:
                payload = (None, None)
            self.background_results.put((generation, *payload))

        threading.Thread(target=render, name="background-blur", daemon=True).start()
        self.background_timer.start()
        self.update()

    def poll_background(self) -> None:
        latest = None
        while True:
            try:
                result = self.background_results.get_nowait()
            except Empty:
                break
            if result[0] == self.background_generation:
                latest = result
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
        self.scan_generation += 1
        generation = self.scan_generation
        self.scan_previous = previous
        mappings = dict(self.name_mappings)
        disabled = set(self.disabled_name_mappings)
        recent_order = self.recent_window_order()

        def scan() -> None:
            try:
                items = windows(exclude=int(self.winId()), mappings=mappings, disabled=disabled,
                                recent_order=recent_order)
            except Exception:
                items = []
            self.scan_results.put((generation, items))

        threading.Thread(target=scan, name="window-scan", daemon=True).start()
        self.scan_timer.start()

    def poll_window_scan(self) -> None:
        try:
            generation, items = self.scan_results.get_nowait()
        except Empty:
            return
        if generation != self.scan_generation:
            return
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
            self.items = order_by_recent(self.window_cache, self.recent_window_order()) or [(0, "正在读取窗口…")]
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
        self.icons = {}
        self.demo_icons = {}
        self.demo_previews = {}
        self.background = None
        self.background_source = None if self.demo or self.blur_mode == "transparent" else screen.grabWindow(0)
        self.setWindowOpacity(0)
        self.show()
        self.raise_()
        self.activateWindow()
        self.setFocus()
        self.open_animation.stop()
        self.open_animation.setStartValue(0)
        self.open_animation.setEndValue(1)
        self.open_animation.start()
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
        if self.items and self.isVisible() and self.deleting_index is None:
            self.selected = (self.selected + step) % len(self.items)
            self.announce_selection()
            self.update()
            self.sync_thumbnails()

    def direction(self, direction: str) -> None:
        if not self.isVisible() or self.deleting_index is not None:
            return
        vectors = {"up": -math.pi / 2, "right": 0, "down": math.pi / 2, "left": math.pi}
        count = len(self.items)
        angle = vectors[direction]
        self.selected = min(range(count), key=lambda i: abs(math.atan2(math.sin(-math.pi / 2 + i * 2 * math.pi / count - angle), math.cos(-math.pi / 2 + i * 2 * math.pi / count - angle))))
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
        if not hwnd or not user32.IsWindow(hwnd):
            closed = True
        else:
            result = ctypes.c_size_t()
            closed = user32.SendMessageTimeoutW(hwnd, WM_SYSCOMMAND, SC_CLOSE, 0, 0x0002, 300, ctypes.byref(result))
            if not closed:
                closed = user32.SendMessageTimeoutW(hwnd, WM_CLOSE, 0, 0, 0x0002, 300, ctypes.byref(result))
            if not closed:
                closed = user32.PostMessageW(hwnd, WM_CLOSE, 0, 0)
        if not closed:
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
        elif action in ("up", "right", "down", "left"):
            self.direction(action)

    def radii(self) -> tuple[float, float, QPoint]:
        outer = min(350, self.width() * 0.31, self.height() * 0.42)
        return outer, outer * 0.38, QPoint(self.width() // 2, self.height() // 2)

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
        if self.deleting_index is not None:
            return
        point = event.position().toPoint()
        self.setCursor(Qt.CursorShape.ArrowCursor)
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
        if self.deleting_index is not None:
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
        elif key in (Qt.Key.Key_Left, Qt.Key.Key_Right, Qt.Key.Key_Up, Qt.Key.Key_Down):
            self.direction({Qt.Key.Key_Left: "left", Qt.Key.Key_Right: "right", Qt.Key.Key_Up: "up", Qt.Key.Key_Down: "down"}[key])
        elif key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self.commit()
        elif key == Qt.Key.Key_Delete:
            self.close_selected()

    def paintEvent(self, _event) -> None:
        if not self.items:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        if self.blur_mode == "transparent":
            pass
        elif self.background:
            painter.drawPixmap(self.rect(), self.background, self.background.rect())
        else:
            gradient = QLinearGradient(0, 0, self.width(), self.height())
            gradient.setColorAt(0, QColor("#27354f"))
            gradient.setColorAt(0.48, QColor("#111d30"))
            gradient.setColorAt(1, QColor("#091420"))
            painter.fillRect(self.rect(), gradient)
            glow = QRadialGradient(QPointF(self.width() * .51, self.height() * .42), self.width() * .51)
            glow.setColorAt(0, themed_color(self.theme, "glow", 38))
            glow.setColorAt(1, themed_color(self.theme, "glow", 0))
            painter.fillRect(self.rect(), glow)
        if self.blur_mode != "transparent":
            overlay_alpha = {"none": 174, "gaussian": 132, "acrylic": 96, "fast": 146, "bilateral": 112}.get(self.blur_mode, 174)
            painter.fillRect(self.rect(), QColor(3, 9, 18, overlay_alpha))
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
                fill.setColorAt(0, themed_color(self.theme, "selected_start", 240))
                fill.setColorAt(1, themed_color(self.theme, "selected_end", 232))
            else:
                fill.setColorAt(0, themed_color(self.theme, "segment_start", 226))
                fill.setColorAt(1, themed_color(self.theme, "segment_end", 211))
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
                self.draw_placeholder(painter, thumb_rect, title, self.icons.get(hwnd), bool(hwnd and user32.IsIconic(hwnd)))
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
            if icon:
                painter.drawPixmap(QRect(int(start_x), int(label_y - 11), 20, 20), icon)
                start_x += 26
            painter.setPen(themed_color(self.theme, "accent_light") if selected else QColor("#d7e0eb"))
            painter.drawText(QPoint(int(start_x), int(label_y + 5)), text)

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
        core.setColorAt(0, QColor(self.theme["core_start"]))
        core.setColorAt(0.75, QColor(self.theme["core_mid"]))
        core.setColorAt(1, QColor(self.theme["core_end"]))
        painter.setBrush(core)
        painter.setPen(QPen(themed_color(self.theme, "accent_light", 205), 1.6))
        painter.drawEllipse(center, int(inner - 8), int(inner - 8))
        painter.setPen(Qt.PenStyle.NoPen)
        content_shift = int(inner * 0.12)
        painter.setBrush(QColor("#b6c8e1"))
        painter.drawRoundedRect(QRect(center.x() - 23, center.y() - 43 + content_shift, 34, 29), 4, 4)
        painter.setBrush(QColor("#e1ebfa"))
        painter.drawRoundedRect(QRect(center.x() - 10, center.y() - 34 + content_shift, 34, 29), 4, 4)
        alt_title_rect = QRect(center.x() - int(inner), center.y() - int(inner * 0.72) + content_shift, int(inner * 2), 32)
        painter.setFont(QFont("Segoe UI", 16, QFont.Weight.ExtraBold))
        painter.setPen(QColor(1, 8, 18, 210))
        painter.drawText(alt_title_rect.translated(0, 2), Qt.AlignmentFlag.AlignCenter, "Alt + Tab")
        painter.setPen(QColor("#ffffff"))
        painter.drawText(alt_title_rect, Qt.AlignmentFlag.AlignCenter, "Alt + Tab")
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
        mouse_footer = "鼠标悬停切换     右键关闭窗口"
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


def self_test() -> None:
    from PySide6.QtTest import QTest

    app = QApplication.instance() or QApplication([])
    assert onboarding_position(QRect(400, 200, 120, 180), QRect(0, 0, 1000, 700), 360, 190) == QPoint(24, 200)
    assert onboarding_position(QRect(244, 72, 111, 170), QRect(0, 0, 381, 247), 228, 170) == QPoint(0, 72)
    mapping_dialog = NameMappingDialog(None, {})
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
    mapping_dialog.refresh("process:msedge")
    QTest.mouseClick(mapping_dialog.delete_button, Qt.MouseButton.LeftButton)
    assert "process:msedge" not in mapping_dialog.keys()
    assert "process:msedge" in mapping_dialog.deleted
    mapping_dialog.disabled.clear()
    mapping_dialog.deleted.clear()
    mapping_dialog.hide()
    assert short_window_title("README.md - tab - Visual Studio Code") == "README.md"
    assert short_window_title("Tibo on X: 2026") == "Tibo on X: 2026"
    assert short_window_title("下载和文件资源管理器") == "下载"
    assert short_window_title("Downloads and File Explorer") == "Downloads"
    assert short_window_title("Android Studio") == "Android Studio"
    assert display_window_name("README.md - tab - Visual Studio Code", "code", 1) == "README.md"
    assert display_window_name("Microsoft Edge", "msedge", 1) == "Edge"
    assert display_window_name("Microsoft Edge", "msedge", 1,
                               disabled={"process:msedge"}) == "Microsoft Edge"
    assert display_window_name("ChatGPT", "ChatGPT", 1) == "ChatGPT"
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
    assert display_window_name("PowerShell 7 (x64)", "WindowsTerminal", 1) == "PowerShell 7"
    assert display_window_name("README.md - tab - Visual Studio Code - 你好你好", "unknown", 1) == "README.md"
    assert display_window_name("微信", "Weixin", 1, {"process:weixin": "微信客户端"}) == "微信客户端"
    assert display_window_name("ChatGPT", "ChatGPT", 1,
                               {"process:chatgpt": "AI 助手"}) == "AI 助手"
    assert display_window_name("PowerShell 7", "WindowsTerminal", 2,
                               {"process:windowsterminal": "终端"}) == "终端"
    assert display_window_name("Minecraft NeoForge* 26.1.2 - 单人游戏", "java", 1) == "Minecraft NeoForge* 26.1.2"
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
    assert order_by_recent([(1, "a"), (2, "b"), (3, "c")], [3, 1]) == [(3, "c"), (1, "a"), (2, "b")]
    assert not tray_icon("blue").isNull()
    overlay.set_theme("purple")
    assert overlay.theme["accent"] == THEMES["purple"]["accent"]
    overlay.set_theme("missing")
    assert overlay.theme is THEMES["blue"]
    blur_source = QPixmap(16, 16)
    blur_source.fill(QColor("white"))
    assert not gaussian_blur_pixmap(blur_source).isNull()
    blur_image_source = Image.fromarray(np.dstack((
        np.tile(np.arange(48, dtype=np.uint8), (32, 1)),
        np.tile(np.arange(32, dtype=np.uint8)[:, None], (1, 48)),
        np.full((32, 48), 80, dtype=np.uint8),
        np.full((32, 48), 255, dtype=np.uint8),
    )), "RGBA")
    blur_outputs = {mode: blur_image(blur_image_source, mode) for mode in BLUR_MODES if mode != "none"}
    assert all(output.size == blur_image_source.size for output in blur_outputs.values())
    assert any(output.tobytes() != blur_image_source.tobytes() for output in blur_outputs.values())
    overlay.set_blur_enabled(True)
    assert overlay.blur_enabled
    overlay.set_blur_enabled(False)
    overlay.set_blur_mode("transparent")
    assert overlay.blur_mode == "transparent"
    overlay.items = [(0, str(i)) for i in range(11)]
    overlay.resize(1200, 800)
    assert overlay.sector_at(QPoint(600, 100)) == 0
    assert overlay.sector_at(QPoint(600, 400)) is None
    overlay.selected = 8
    assert overlay.sector_at(QPoint(600, 100)) == 0
    assert overlay.thumbnail_rect(0, 8, True).width() > overlay.thumbnail_rect(0, 8, False).width()
    assert overlay.item_at(overlay.thumbnail_rect(8, len(overlay.items), True).center()) == 8
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
        point = test_overlay.thumbnail_rect(0, 1, True).center()
        QTest.mouseClick(test_overlay, Qt.MouseButton.RightButton, Qt.KeyboardModifier.NoModifier, point)
        QTest.qWait(240)
        assert not source.isVisible(), "Right-click close did not close the target window"
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
    set_windows_app_identity()
    app = QApplication(sys.argv[:1])
    app.setQuitOnLastWindowClosed(False)
    app.setApplicationName(APP_NAME)
    app.setApplicationDisplayName("Radial Alt+Tab")
    app.setOrganizationName("SuZe")
    settings = QSettings(APP_NAME, APP_NAME)
    name_mappings = load_name_mappings(settings)
    disabled_name_mappings = load_disabled_name_mappings(settings)
    deleted_name_mappings = load_deleted_name_mappings(settings)
    saved_blur_mode = str(settings.value("blur_mode", "")).casefold()
    if saved_blur_mode not in BLUR_MODES:
        old_blur_setting = str(settings.value("gaussian_blur", "")).casefold()
        if old_blur_setting:
            saved_blur_mode = "gaussian" if old_blur_setting in {"1", "true", "yes", "on"} else "none"
        else:
            saved_blur_mode = "gaussian"
    theme_name = str(settings.value("theme", "blue"))
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
                            blur_mode=saved_blur_mode)
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
        hook.installed.wait(2)
        if hook.error:
            raise SystemExit(hook.error)
        app.aboutToQuit.connect(hook.stop)
    tray = QSystemTrayIcon(app_icon, app)
    menu = QMenu()
    menu.addAction("显示切换", overlay.open)
    mapping_dialog: NameMappingDialog | None = None

    def edit_mappings() -> None:
        nonlocal name_mappings, disabled_name_mappings, deleted_name_mappings, mapping_dialog
        if mapping_dialog is not None and mapping_dialog.isVisible():
            mapping_dialog.showNormal()
            mapping_dialog.raise_()
            mapping_dialog.activateWindow()
            return
        mapping_dialog = edit_name_mapping(overlay, settings, name_mappings, disabled_name_mappings,
                                           deleted_name_mappings)

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

    mapping_action = menu.addAction("名称映射", edit_mappings)

    blur_menu = menu.addMenu("背景模糊")
    blur_group = QActionGroup(blur_menu)
    blur_group.setExclusive(True)
    blur_actions = {}

    def apply_blur_mode(mode: str) -> None:
        settings.setValue("blur_mode", mode)
        overlay.set_blur_mode(mode)
        for key, action in blur_actions.items():
            action.setChecked(key == mode)

    for mode, label in BLUR_MODES.items():
        action = blur_menu.addAction(label)
        action.setCheckable(True)
        action.triggered.connect(lambda _checked=False, mode=mode: apply_blur_mode(mode))
        blur_group.addAction(action)
        blur_actions[mode] = action
    blur_actions[saved_blur_mode].setChecked(True)

    theme_menu = menu.addMenu("主题颜色")
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
        action = theme_menu.addAction(theme["label"])
        action.setCheckable(True)
        action.triggered.connect(lambda _checked=False, key=key: apply_theme(key))
        theme_actions[key] = action
    apply_theme(theme_name)

    startup_action = menu.addAction("开机启动")
    startup_action.setCheckable(True)
    startup_action.setChecked(autostart_enabled())

    def toggle_autostart(enabled: bool) -> None:
        try:
            set_autostart(enabled)
        except OSError as error:
            startup_action.blockSignals(True)
            startup_action.setChecked(not enabled)
            startup_action.blockSignals(False)
            tray.showMessage("Radial Alt+Tab", f"开机启动设置失败：{error}",
                             QSystemTrayIcon.MessageIcon.Warning, 4000)

    startup_action.toggled.connect(toggle_autostart)
    menu.addSeparator()
    guide_action = menu.addAction("新手指引")
    menu.addAction("退出", app.quit)
    tray.setContextMenu(menu)
    tray.setToolTip("Radial Alt+Tab")
    tray.show()
    onboarding = OnboardingGuide(
        tray, menu, settings,
        {"mapping": mapping_action, "blur": blur_menu.menuAction(),
         "theme": theme_menu.menuAction(), "startup": startup_action},
    )
    guide_action.triggered.connect(onboarding.start)
    onboarding_done = str(settings.value("onboarding_done", "")).casefold() in {
        "1", "true", "yes", "on"
    }
    onboarding_auto_shown = str(settings.value("onboarding_auto_shown", "")).casefold() in {
        "1", "true", "yes", "on"
    }
    if not args.demo and not onboarding_done and not onboarding_auto_shown:
        settings.setValue("onboarding_auto_shown", True)
        QTimer.singleShot(900, onboarding.start)
    if not args.demo:
        tray.showMessage("Radial Alt+Tab", "程序已运行，已驻留系统托盘。按 Alt+Tab 呼出切换器。",
                         QSystemTrayIcon.MessageIcon.NoIcon, 4000)
    try:
        app.exec()
    finally:
        if mutex:
            hook.stop()
            hook.join(2)
            kernel32.CloseHandle(mutex)


if __name__ == "__main__":
    main()
