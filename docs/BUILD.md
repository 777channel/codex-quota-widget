# 开发与打包

## 环境

- Windows 10 / 11 x64。
- 本地完整安装的 Python 3.14 x64，含 `tkinter`、`tcl` 与运行库。
- Windows 自带的 .NET Framework 4.x C# 编译器：`C:\Windows\Microsoft.NET\Framework64\v4.0.30319\csc.exe`。
- 调试额度读取时，需已安装并登录 Codex 桌面客户端。

项目使用 Python 标准库、Windows GDI+ 与 .NET Framework，不需要通过 pip 安装依赖。

## 源码运行

在项目目录执行：

```powershell
python -B quota_widget.py
```

源码运行时，位置、周期与缓存保存在源码旁的 `state.json`。安装版本保存于 `%LOCALAPPDATA%\CodexQuotaWidget`。不应提交这些运行数据。

## 构建安装包

使用完整 Python 安装目录里的解释器运行：

```powershell
python -B packaging/build.py
```

构建脚本根据当前解释器的位置收集运行环境，编译安装器和启动器，生成：

```text
dist/Codex额度显示-安装.exe   对外分发的安装程序
dist/CodexQuota.exe         安装后使用的轻量启动器
dist/payload.zip            中间产物，包含代码与运行环境
```

对外只需要提供安装程序。运行环境来自执行构建脚本的 Python 目录；使用虚拟环境或缺少 Tcl/Tk 的精简 Python 可能无法构建出完整包。

安装包内有 Python 运行环境的 `LICENSE.txt` 以及 Tcl/Tk 随附的许可证。发布时应保留这些文件。

## 安装后的行为

安装器使用当前用户权限，把文件写入 `%LOCALAPPDATA%\CodexQuotaWidget`，创建开始菜单与登录启动快捷方式，并写入当前用户的卸载信息。

`CodexQuota.exe --start` 使用内置 `pythonw.exe` 启动 `entry.py`。入口会限制重复启动，记录进程号，并在异常时写入 `runtime-error.log`。安装器异常记录在 `setup-error.log`。

重复安装时先关闭已有的安装版本，然后更新程序文件，保留 `state.json`。卸载时移除启动入口、卸载信息和固定安装目录。

## 数据与显示

- 使用本地 Codex 的 `app-server` 标准输入/输出连接，调用 `account/rateLimits/read`。
- 优先查找桌面客户端的本地 CLI 缓存，其次查找 PATH，再尝试 Windows 应用包内的 `codex.exe`。
- 额度通常每 60 秒刷新一次；Codex 窗口不可用时停止额度客户端，等待窗口恢复。
- `Segoe UI`、13 个逻辑像素、常规字重；使用 GDI+ 灰度抗锯齿与 `UpdateLayeredWindow` 逐像素透明度。
- 位置保存为 96 DPI 逻辑坐标；通过 Codex 窗口的位置和尺寸计算显示位置。
- 主题颜色读取 Windows 应用主题，无法独立识别 Codex 自定义主题。

## 已验证范围

首版在 Windows x64、125% 系统缩放环境中完成实际安装、启动、真实额度读取、重复启动、重装保留设置及卸载验证。文字渲染另验证了 100%、125%、150%、200% DPI。

Windows 10、不同客户端版本与不同屏幕布局仍可能有差异。固定位置不是控件识别，Codex 界面布局变化后应重新定位。
