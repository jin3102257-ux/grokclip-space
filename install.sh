#!/usr/bin/env bash
# 一键安装：构建 → 复制到「应用程序」→ 启动。零费用、无需 Apple 开发者账号。
set -euo pipefail

cd "$(dirname "$0")"
APP_NAME="GrokClip Space"

bash ./build.sh

if [[ -w "/Applications" ]]; then
  DEST_DIR="/Applications"
else
  DEST_DIR="$HOME/Applications"
  mkdir -p "$DEST_DIR"
fi
DEST="$DEST_DIR/$APP_NAME.app"

# 若旧版本正在运行，先退出
if pgrep -x "GrokClipSpace" >/dev/null 2>&1; then
  osascript -e 'tell application id "local.grokclip.space" to quit' >/dev/null 2>&1 || true
  sleep 0.8
  pkill -x "GrokClipSpace" >/dev/null 2>&1 || true
fi

rm -rf "$DEST"
ditto "build/$APP_NAME.app" "$DEST"
xattr -cr "$DEST" 2>/dev/null || true

echo "✓ 已安装到：$DEST"
open "$DEST"
echo "✓ 已启动。屏幕右侧会出现紫色悬浮球，顶部菜单栏也有图标。"
