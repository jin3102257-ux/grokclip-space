#!/usr/bin/env bash
# GrokClip Space · Linux 安装（仅当前用户，不需要 sudo，不联网）
#   bash install_linux.sh               检查依赖 + 创建应用菜单入口 + 启动
#   bash install_linux.sh --autostart   额外设置登录后自动启动
#   bash install_linux.sh --uninstall   移除菜单入口与自启动（不删除收集的数据）
set -euo pipefail
cd "$(dirname "$0")"
HERE="$(pwd)"
APPS="${XDG_DATA_HOME:-$HOME/.local/share}/applications"
ICONS="${XDG_DATA_HOME:-$HOME/.local/share}/icons/hicolor/scalable/apps"
AUTOSTART="${XDG_CONFIG_HOME:-$HOME/.config}/autostart"
DESKTOP_NAME="grokclip-space.desktop"

if [[ "${1:-}" == "--uninstall" ]]; then
  rm -f "$APPS/$DESKTOP_NAME" "$AUTOSTART/$DESKTOP_NAME" "$ICONS/grokclip-space.svg"
  pkill -f "python3 -m grokclip" >/dev/null 2>&1 || true
  echo "✓ 已移除菜单入口与自启动。收集的数据仍在 ${XDG_DATA_HOME:-$HOME/.local/share}/GrokClipSpace（需要时可手动删除）。"
  exit 0
fi

echo "▸ 检查依赖"
bash ./run_linux.sh --check || echo "  （请按上面的提示安装缺少的组件，本脚本不会代你执行 sudo）"
chmod +x ./run_linux.sh 2>/dev/null || true

echo "▸ 自检"
python3 -m grokclip --self-test "${TMPDIR:-/tmp}/GrokSpace_selftest.html" || { echo "✗ 自检失败"; exit 1; }

mkdir -p "$APPS" "$ICONS"
cat > "$ICONS/grokclip-space.svg" <<'SVG'
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 128 128"><defs><linearGradient id="g" x1="0" y1="1" x2="1" y2="0"><stop offset="0" stop-color="#4f46e5"/><stop offset="1" stop-color="#9b66fa"/></linearGradient></defs><rect x="10" y="10" width="108" height="108" rx="26" fill="url(#g)"/><rect x="34" y="50" width="52" height="38" rx="7" fill="#fff" opacity=".45"/><rect x="40" y="42" width="52" height="38" rx="7" fill="#fff" opacity=".7"/><rect x="46" y="34" width="52" height="38" rx="7" fill="#fff"/><path d="M92 18l3 9 9 3-9 3-3 9-3-9-9-3 9-3z" fill="#fff"/></svg>
SVG

write_desktop() {
  cat > "$1" <<DESK
[Desktop Entry]
Type=Application
Name=GrokClip Space
Name[zh_CN]=GrokClip Space 收集球
Comment=Collect Grok Bot replies and files, share as one offline HTML Space
Comment[zh_CN]=收集 Grok Bot 对话原文与文件，一键生成离线 HTML 空间
Exec=/bin/bash "$HERE/run_linux.sh"
Icon=grokclip-space
Terminal=false
Categories=Utility;
StartupNotify=false
DESK
}
write_desktop "$APPS/$DESKTOP_NAME"
echo "✓ 已创建应用菜单入口：$APPS/$DESKTOP_NAME"

if [[ "${1:-}" == "--autostart" ]]; then
  mkdir -p "$AUTOSTART"
  write_desktop "$AUTOSTART/$DESKTOP_NAME"
  echo "✓ 已设置登录后自动启动：$AUTOSTART/$DESKTOP_NAME"
fi

if [[ -n "${DISPLAY:-}${WAYLAND_DISPLAY:-}" ]]; then
  nohup /bin/bash "$HERE/run_linux.sh" >/dev/null 2>&1 &
  echo "✓ 已启动。屏幕右侧会出现紫色悬浮球：单击打开收集篮，右键打开菜单。"
fi
