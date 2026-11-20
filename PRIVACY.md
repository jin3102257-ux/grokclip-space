# 隐私说明

GrokClip Space 是一个纯本地工具，设计原则是「数据不出本机，除非你自己发出去」。macOS 版（Swift）和 Windows / Linux 版（Python）遵循同一套规则。

## 它不做什么

- **不联网**：源码中没有任何网络请求代码，也不导入任何网络库，不上传、不统计、不埋点、不检查更新。
- **不依赖第三方**：
  - macOS 版只用 Apple 系统自带框架（AppKit、SwiftUI、Combine、CryptoKit、UniformTypeIdentifiers、ServiceManagement）。
  - Windows / Linux 版只用 Python 标准库和 Tkinter。Windows 通过 `ctypes` 直接调用系统 API，Linux 调用本机的 `xclip` / `wl-clipboard` / `xdotool` 命令。
  - 没有任何 pip 包、包管理依赖或云服务。如果你自己装了可选的 `tkinterdnd2`，程序会用它支持拖放，不装也不影响使用。
- **不收集账号信息**：不读取、不保存任何账号、密码、令牌或 Cookie。
- **不记录剪贴板内容到日志**：Windows / Linux 版的 `error.log` 只记录程序错误，不写任何原文。
- **默认只收录 Grok**：默认只有前台应用是 Grok Bot 时才收录剪贴板。密码管理器写入的敏感内容会被自动跳过：
  - macOS：`org.nspasteboard.ConcealedType` 等标记
  - Windows：`ExcludeClipboardContentFromMonitorProcessing`、`Clipboard Viewer Ignore`、`CanIncludeInClipboardHistory = 0`
  - Linux：`x-kde-passwordManagerHint = secret`（KeePassXC 等）

## 它在本机保存什么

| 内容 | macOS | Windows | Linux |
| --- | --- | --- | --- |
| 收录的原文与附件路径 | `~/Library/Application Support/GrokClipSpace/clips_store.json` | `%APPDATA%\GrokClipSpace\clips_store.json` | `~/.local/share/GrokClipSpace/clips_store.json`（遵循 `$XDG_DATA_HOME`） |
| 设置（监听目录、导出目录等） | 同目录 `settings.json` | 同目录 `settings.json` | 同目录 `settings.json` |
| 悬浮球位置 | 系统 `UserDefaults` | 同目录 `ui_state.json` | 同目录 `ui_state.json` |
| 程序错误日志（不含原文） | 系统日志 | 同目录 `error.log` | 同目录 `error.log` |
| 生成的空间文件 | 你设置的导出目录 | 你设置的导出目录 | 你设置的导出目录 |

三个平台的 `clips_store.json` / `settings.json` 结构相同，都是原子写入（先写临时文件再替换），其他脚本或 AI Skill 可以直接读取。随时可以在收集篮里「清空」，或直接删除上面的目录。

## 需要你留意的一点

**生成的 `.html` 空间文件会完整内嵌你勾选的原文和附件。** 收到这个文件的人能看到里面的全部内容，也能再转发。发送前请确认勾选的条目和附件里没有不该外发的信息（例如内部资料、客户数据、个人信息）。

## 权限

- **macOS**：首次监听「下载」等目录时，系统可能请求「文件与文件夹」访问权限，仅用于发现新文件并挂载到条目上。不需要辅助功能、屏幕录制、麦克风、通讯录等任何其他权限。
- **Windows**：不需要管理员权限，所有文件都装在当前用户目录下。程序只读取前台窗口的进程名和标题（用于判断是不是 Grok），不注入、不挂钩其他程序，也不安装服务或驱动。
- **Linux**：不需要 sudo（安装脚本只打印依赖的安装命令，不会代你执行）。X11 下用 `xdotool` 读取前台窗口的进程名和标题；Wayland 出于安全设计不允许读取其他应用的窗口，这时会自动按「全部应用」收录，并在收集篮顶部显示提醒。复制密码前，可以在收集篮里暂停监听。
