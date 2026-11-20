#!/usr/bin/env bash
# GrokClip Space · Linux 启动脚本（不需要 sudo，不联网）
#   bash run_linux.sh             启动悬浮球
#   bash run_linux.sh --check     只检查依赖
#   bash run_linux.sh --self-test [out.html]   生成示例空间自检
set -uo pipefail
cd "$(dirname "$0")"

hint() {  # 按发行版打印安装命令（只打印，不执行）
  if command -v apt-get >/dev/null 2>&1; then echo "sudo apt install $*"
  elif command -v dnf >/dev/null 2>&1; then echo "sudo dnf install ${*//python3-tk/python3-tkinter}"
  elif command -v pacman >/dev/null 2>&1; then echo "sudo pacman -S ${*//python3-tk/tk}"
  elif command -v zypper >/dev/null 2>&1; then echo "sudo zypper install ${*//python3-tk/python3-tk}"
  else echo "请用系统包管理器安装：$*"; fi
}

check() {
  local ok=0
  if ! command -v python3 >/dev/null 2>&1; then
    echo "✗ 未找到 python3。安装：$(hint python3 python3-tk)"; return 1
  fi
  if ! python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)'; then
    echo "✗ 需要 Python 3.9 或更新版本（当前 $(python3 -V 2>&1)）"; return 1
  fi
  echo "✓ $(python3 -V 2>&1)"
  if python3 -c 'import tkinter' >/dev/null 2>&1; then echo "✓ Tkinter"
  else echo "✗ 缺少 Tkinter。安装：$(hint python3-tk)"; ok=1; fi

  if [[ -n "${WAYLAND_DISPLAY:-}" || "${XDG_SESSION_TYPE:-}" == "wayland" ]]; then
    echo "• 会话：Wayland"
    if command -v wl-paste >/dev/null 2>&1 && command -v wl-copy >/dev/null 2>&1; then echo "✓ wl-clipboard"
    else echo "✗ 缺少 wl-clipboard（自动收录需要）。安装：$(hint wl-clipboard)"; ok=1; fi
    echo "⚠ Wayland 不允许读取前台应用：将按「全部应用」收录。复制密码前可在收集篮里暂停监听。"
  elif [[ -n "${DISPLAY:-}" ]]; then
    echo "• 会话：X11"
    if command -v xclip >/dev/null 2>&1; then echo "✓ xclip"
    elif command -v xsel >/dev/null 2>&1; then echo "• xsel（建议改装 xclip 以识别密码管理器标记：$(hint xclip)）"
    else echo "✗ 缺少 xclip（自动收录需要）。安装：$(hint xclip)"; ok=1; fi
    if command -v xdotool >/dev/null 2>&1; then echo "✓ xdotool（识别前台应用）"
    else echo "✗ 缺少 xdotool（只收录 Grok 需要它）。安装：$(hint xdotool)"; ok=1; fi
  else
    echo "✗ 未检测到图形会话（DISPLAY / WAYLAND_DISPLAY）。请在桌面环境中运行。"; ok=1
  fi
  return $ok
}

case "${1:-}" in
  --check) check; exit $? ;;
  --self-test) exec python3 -m grokclip "$@" ;;
esac

if ! check >/tmp/grokclip-check.$$ 2>&1; then
  cat /tmp/grokclip-check.$$
  echo "（缺少的组件会影响部分功能，程序仍会启动；安装后重新运行即可）"
fi
rm -f /tmp/grokclip-check.$$
exec python3 -m grokclip "$@"
