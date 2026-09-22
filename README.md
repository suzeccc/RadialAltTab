# Radial Alt+Tab

一个面向 Windows 的环形窗口切换器。按住熟悉的 `Alt`，就能在同一个圆环里预览并切换所有可切换窗口；窗口多、桌面乱时，比连续盲按 `Tab` 更容易找到目标。

![Radial Alt+Tab 界面预览](preview.png)

## 你可以用它做什么

- **一眼看全窗口**：把当前可切换的窗口排列在同一个圆环上，并显示缩略图与窗口名称。
- **键盘、鼠标都能操作**：继续使用 `Alt + Tab`，也可以悬停、点击、滚轮或使用方向键选择。
- **兼容最小化窗口**：优先显示 Windows DWM 缩略图；没有可用预览时使用图标占位。
- **按需定制显示**：可以为进程设置自定义名称，也可以隐藏不需要显示的窗口。
- **背景效果可调**：支持轻微模糊、透明背景、亚克力、快速、高斯和双边模糊，并可切换主题颜色。
- **安静地待在后台**：启动后常驻系统托盘，只保留一个运行实例，不弹出额外主窗口。
- **本机运行**：运行时不需要网络服务；窗口信息只用于本机切换。

## 开始使用

### 运行发布版

发布版本后，请从项目的发布页下载对应的发布包并解压。双击其中的 `RadialAltTab.exe` 即可启动；也可以在 PowerShell 中运行：

```powershell
.\RadialAltTab.exe
```

启动后请到系统托盘查看 Radial Alt+Tab 图标。右键图标可以编辑名称映射、在“背景模糊”中选择轻微模糊、透明背景、亚克力背景、快速模糊、高斯模糊或双边模糊（默认高斯模糊）、切换主题颜色、设置开机启动或退出程序。

在环形窗口上左键切换并打开，右键可直接关闭对应窗口。

### 从源码运行

需要 Windows 和 Python 3.10+：

```powershell
python -m pip install -r requirements.txt
python radial_tab.py
```

## 操作方式

| 操作 | 结果 |
| --- | --- |
| 按住 `Alt`，按 `Tab` | 打开切换器；再次按 `Tab` 选择下一个窗口 |
| 按住 `Alt`，按反引号/波浪号键 `` ` / ~ `` | 选择上一个窗口 |
| 按住 `Alt`，按方向键 | 按方向选择窗口 |
| 松开 `Alt` | 切换到当前选中的窗口 |
| `Esc` | 取消并回到原窗口 |
| 鼠标悬停 | 选择对应扇区 |
| 鼠标左键 | 选中并立即切换；点击圆环外取消 |
| 鼠标滚轮 | 上下切换窗口 |
| `Delete` | 关闭当前选中的窗口（应用仍可弹出未保存确认） |

也可以在切换器打开后按 `Enter` 确认选择。

## 使用边界

- 这是 **Windows 专用** 工具，依赖 Windows 的窗口枚举、全局键盘钩子和 DWM 缩略图能力。
- Windows 可能拒绝将某些提权程序或系统窗口置于前台；这属于系统权限限制，不是切换器丢失窗口。
- 某些窗口无法提供实时缩略图时，会显示图标或“预览不可用”占位图。
- 程序需要监听全局键盘事件才能接管 `Alt+Tab`；不需要安装后台服务。

## 从源码构建 EXE

```powershell
python -m pip install pyinstaller
powershell -ExecutionPolicy Bypass -File .\build.ps1
```

构建结果为 `dist\RadialAltTab.exe`，可在没有 Python 的 Windows 环境中直接运行。

<details>
<summary>开发者：预览与自检命令</summary>

```powershell
# 打开不接管系统快捷键的示例界面
python radial_tab.py --demo

# 保存示例截图后退出
python radial_tab.py --snapshot preview.png

# 运行基础检查
python radial_tab.py --self-test
```

</details>
