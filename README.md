# Radial Alt+Tab

Windows 环形窗口切换器。按 `Alt + Tab` 呼出圆环，直接查看窗口缩略图和名称，再用键盘或鼠标选择目标窗口。

![Radial Alt+Tab 界面预览](preview.png)

## 快速开始

从 [Releases](https://github.com/suzeccc/RadialAltTab/releases/latest) 下载 `RadialAltTab.exe` 并双击运行，无需安装 Python。程序启动后驻留系统托盘；按 `Alt + Tab` 呼出切换器，右键托盘图标可打开设置或退出。

首次启动默认请求管理员权限，以便切换到以管理员身份运行的窗口。拒绝授权后仍可使用普通权限运行，但切换管理员窗口可能失败。首次运行默认使用深色主题和透明背景。

> 发布版可能落后于当前源码。需要本文所述的最新功能时，请从源码运行或自行构建。

## 操作方式

| 操作 | 结果 |
| --- | --- |
| 按住 `Alt`，按 `Tab` | 呼出切换器；继续按 `Tab` 选择下一个窗口 |
| 按住 `Alt`，按 `` ` / ~ `` 键 | 呼出切换器并向前选择；继续按该键选择上一个窗口 |
| 松开 `Alt` 或按 `Enter` | 切换到选中的窗口 |
| `Esc` | 取消切换 |
| `Delete` | 请求关闭选中的窗口 |
| 鼠标悬停、滚轮 | 选择窗口 |
| 鼠标左键 | 点击窗口立即切换；点击圆环外取消 |
| 鼠标右键 | 请求关闭对应窗口 |

关闭窗口时，如有未保存内容，由目标应用决定是否弹出确认。托盘中的“鼠标切换”可关闭悬停、滚轮和左键操作；右键关闭窗口仍可使用。

## 功能与设置

- **窗口预览**：在圆环上显示可切换窗口的名称和缩略图，包括最小化窗口；无法获取缩略图时显示图标或占位图。
- **窗口映射**：按进程 EXE 名自定义显示名称。取消勾选只停用名称映射，不会隐藏窗口；也可以删除映射。
- **背景与主题**：可选透明、浅深、轻微模糊、高斯模糊或亚克力背景，以及深色、浅深色、深海蓝、暮紫、薄荷绿主题。“窗口映射”和管理员提示使用深色毛玻璃效果，系统不支持时回退为深色背景。
- **其他托盘设置**：可切换简体中文、繁体中文或英语，设置开机启动、管理员运行，并手动检查 GitHub 最新发行版。管理员运行设置在下次启动时生效。

程序只启动一个实例，常规窗口切换无需网络；仅手动检查更新时连接 GitHub。窗口信息只用于本机切换。

## 从源码运行与构建

需要 Windows 和 Python 3.10+。在项目目录中运行：

```powershell
python -m pip install -r requirements.txt
python radial_tab.py
```

构建无需 Python 的单文件 EXE：

```powershell
python -m pip install -r requirements.txt pyinstaller==6.21.0
powershell -ExecutionPolicy Bypass -File .\build.ps1
```

构建结果为 `dist\RadialAltTab.exe`。

<details>
<summary>开发者：预览与自检命令</summary>

```powershell
python radial_tab.py --demo                 # 示例界面，不接管系统快捷键
python radial_tab.py --snapshot preview.png # 保存示例截图后退出
python radial_tab.py --self-test            # 运行基础检查
```

</details>

## 使用边界

- 仅支持 Windows，依赖窗口枚举、全局键盘钩子和 DWM 缩略图；无需安装后台服务。
- 普通权限下，Windows 可能拒绝切换到管理员窗口或系统窗口，并显示原生切换界面。
- 目标窗口被子对话框阻挡时，关闭操作会转到该对话框；请先处理对话框。

本项目采用 [MIT 许可证](LICENSE)。
