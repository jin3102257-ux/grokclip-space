# 更新日志

本项目遵循 [语义化版本](https://semver.org/lang/zh-CN/)，格式参考 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)。

## [0.1.0] - 2026-11-20

首个公开版本。

### 新增
- **macOS 原生收集端**（Swift + AppKit/SwiftUI，macOS 13+）：悬浮球 + 菜单栏图标、剪贴板监听、多目录文件监听、收集篮、一键生成空间并以文件写入剪贴板、系统分享、开机自启；`install.sh` 本机编译 + ad-hoc 签名，无需开发者账号。
- **Windows / Linux 收集端**（Python 3.9+ 标准库 + Tkinter）：功能与 macOS 版对齐。
  - Windows：`ctypes` 直接调用 Win32 剪贴板 API，空间以 CF_HDROP 文件写入剪贴板；`run_windows.bat` 一键启动，`install_windows.ps1` 创建「开始」菜单 / 开机自启快捷方式（无需管理员）。
  - Linux：X11（xclip / xdotool）与 Wayland（wl-clipboard）；空间以 `text/uri-list` + `x-special/gnome-copied-files` 写入剪贴板；`install_linux.sh` 创建应用菜单 / 自启动入口（无需 sudo）。
- **离线 HTML 空间**（格式版本 1）：单文件、零外链；Markdown 渲染、原文切换、代码复制、图片灯箱、PDF 预览、附件下载、深浅色；单文件 25 MB / 合计 80 MB 内嵌上限。三个平台共用同一份模板，输出逐字节一致（时间字段除外）。
- 来源白名单（仅 Grok / Grok + 浏览器 / 全部应用）、密码管理器敏感标记过滤、SHA-256 去重、自写不回环。
- 双向 120 秒文件关联（先复制后下载、先下载后复制都能自动挂载）与「新落地的文件」缓冲池。
- 数据原子写入：`clips_store.json` / `settings.json` 三个平台结构一致。
- 文档：README、试用说明、隐私说明、贡献指南、安全策略；Issue / PR 模板。
- 测试：Python 单元测试 64 项、Linux Xvfb 端到端 27 项、离线空间 jsdom 检查 51 项。

### 已知限制
- Windows 版尚未在真机上运行验证（Win32 调用已用模拟 API 测试）。
- Wayland 下无法识别前台应用，自动改为「全部应用」收录。
- Windows / Linux 的文件拖放需要自行安装可选的 `tkinterdnd2`，否则用「附件」按钮。
- Linux 悬浮球在无合成器的桌面上显示为方形底色。


