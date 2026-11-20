#!/usr/bin/env bash
# GrokClip Space 本地构建脚本：零依赖、零付费，只需 Xcode Command Line Tools（xcode-select --install）。
# 用法：./build.sh            按本机架构构建
#       UNIVERSAL=1 ./build.sh  同时构建 arm64 + x86_64 通用版本
set -euo pipefail

cd "$(dirname "$0")"
ROOT="$(pwd)"
APP_NAME="GrokClip Space"
EXEC_NAME="GrokClipSpace"
BUNDLE_ID="local.grokclip.space"
VERSION="0.2.0"
BUILD_DIR="$ROOT/build"
APP_DIR="$BUILD_DIR/$APP_NAME.app"
MIN_MACOS="13.0"

if [[ "$(uname -s)" != "Darwin" ]]; then
  echo "✗ 只能在 macOS 上构建。" >&2
  exit 1
fi
if ! command -v swiftc >/dev/null 2>&1; then
  echo "✗ 未找到 swiftc。请先安装命令行工具：xcode-select --install" >&2
  exit 1
fi

HOST_ARCH="$(uname -m)"
case "$HOST_ARCH" in
  arm64|aarch64) HOST_ARCH="arm64" ;;
  x86_64) HOST_ARCH="x86_64" ;;
  *) echo "✗ 不支持的架构：$HOST_ARCH" >&2; exit 1 ;;
esac

if [[ "${UNIVERSAL:-0}" == "1" ]]; then
  ARCHS=(arm64 x86_64)
else
  ARCHS=("$HOST_ARCH")
fi

echo "▸ Swift: $(swiftc --version 2>&1 | head -n 1)"
echo "▸ 目标架构: ${ARCHS[*]}  最低系统: macOS $MIN_MACOS"

rm -rf "$APP_DIR"
mkdir -p "$APP_DIR/Contents/MacOS" "$APP_DIR/Contents/Resources" "$BUILD_DIR/obj"

SOURCES=("$ROOT"/Sources/*.swift)

BINARIES=()
for ARCH in "${ARCHS[@]}"; do
  OUT="$BUILD_DIR/obj/$EXEC_NAME-$ARCH"
  echo "▸ 编译 $ARCH …"
  swiftc -swift-version 5 -O \
    -target "${ARCH}-apple-macosx${MIN_MACOS}" \
    -module-name "$EXEC_NAME" \
    -framework AppKit -framework SwiftUI -framework UniformTypeIdentifiers -framework CryptoKit \
    -framework ServiceManagement -framework Combine \
    "${SOURCES[@]}" \
    -o "$OUT"
  BINARIES+=("$OUT")
done

if [[ ${#BINARIES[@]} -gt 1 ]]; then
  lipo -create "${BINARIES[@]}" -output "$APP_DIR/Contents/MacOS/$EXEC_NAME"
else
  cp "${BINARIES[0]}" "$APP_DIR/Contents/MacOS/$EXEC_NAME"
fi
chmod +x "$APP_DIR/Contents/MacOS/$EXEC_NAME"

cat > "$APP_DIR/Contents/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleDevelopmentRegion</key><string>zh_CN</string>
  <key>CFBundleDisplayName</key><string>$APP_NAME</string>
  <key>CFBundleName</key><string>$APP_NAME</string>
  <key>CFBundleExecutable</key><string>$EXEC_NAME</string>
  <key>CFBundleIdentifier</key><string>$BUNDLE_ID</string>
  <key>CFBundleIconFile</key><string>AppIcon</string>
  <key>CFBundleInfoDictionaryVersion</key><string>6.0</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleShortVersionString</key><string>$VERSION</string>
  <key>CFBundleVersion</key><string>1</string>
  <key>LSMinimumSystemVersion</key><string>$MIN_MACOS</string>
  <key>LSUIElement</key><true/>
  <key>LSApplicationCategoryType</key><string>public.app-category.productivity</string>
  <key>NSHighResolutionCapable</key><true/>
  <key>NSSupportsAutomaticGraphicsSwitching</key><true/>
  <key>NSHumanReadableCopyright</key><string>GrokClip Space · 本地构建 · 数据不出本机</string>
</dict>
</plist>
PLIST
printf 'APPL????' > "$APP_DIR/Contents/PkgInfo"

# ---------- 程序化生成应用图标（.icns） ----------
echo "▸ 生成应用图标 …"
ICON_WORK="$BUILD_DIR/icon"
rm -rf "$ICON_WORK"; mkdir -p "$ICON_WORK/AppIcon.iconset"
cat > "$ICON_WORK/make_icon.swift" <<'SWIFT'
import AppKit

let outDir = CommandLine.arguments[1]
let specs: [(String, Int)] = [
    ("icon_16x16", 16), ("icon_16x16@2x", 32), ("icon_32x32", 32), ("icon_32x32@2x", 64),
    ("icon_128x128", 128), ("icon_128x128@2x", 256), ("icon_256x256", 256), ("icon_256x256@2x", 512),
    ("icon_512x512", 512), ("icon_512x512@2x", 1024),
]

func render(_ px: Int) -> Data? {
    guard let rep = NSBitmapImageRep(bitmapDataPlanes: nil, pixelsWide: px, pixelsHigh: px, bitsPerSample: 8,
                                     samplesPerPixel: 4, hasAlpha: true, isPlanar: false, colorSpaceName: .deviceRGB,
                                     bytesPerRow: 0, bitsPerPixel: 0),
          let ctx = NSGraphicsContext(bitmapImageRep: rep) else { return nil }
    NSGraphicsContext.saveGraphicsState()
    NSGraphicsContext.current = ctx
    let s = CGFloat(px)
    let inset = s * 0.09
    let rect = NSRect(x: inset, y: inset, width: s - inset * 2, height: s - inset * 2)
    let radius = rect.width * 0.225

    // 阴影 + 渐变圆角底板（indigo → violet）
    let shadow = NSShadow()
    shadow.shadowColor = NSColor.black.withAlphaComponent(0.28)
    shadow.shadowBlurRadius = s * 0.035
    shadow.shadowOffset = NSSize(width: 0, height: -s * 0.012)
    NSGraphicsContext.saveGraphicsState()
    shadow.set()
    let base = NSBezierPath(roundedRect: rect, xRadius: radius, yRadius: radius)
    NSColor(calibratedRed: 0.31, green: 0.27, blue: 0.90, alpha: 1).setFill()
    base.fill()
    NSGraphicsContext.restoreGraphicsState()

    let gradient = NSGradient(colors: [
        NSColor(calibratedRed: 0.29, green: 0.25, blue: 0.88, alpha: 1),
        NSColor(calibratedRed: 0.47, green: 0.33, blue: 0.95, alpha: 1),
        NSColor(calibratedRed: 0.62, green: 0.40, blue: 0.98, alpha: 1),
    ])
    gradient?.draw(in: base, angle: 45)

    // 顶部高光
    let gloss = NSGradient(colors: [NSColor.white.withAlphaComponent(0.22), NSColor.white.withAlphaComponent(0.0)])
    NSGraphicsContext.saveGraphicsState()
    base.addClip()
    gloss?.draw(in: NSRect(x: rect.minX, y: rect.midY, width: rect.width, height: rect.height / 2), angle: -90)
    NSGraphicsContext.restoreGraphicsState()

    // 叠放卡片（代表“收集的多段原文”）
    let cardW = rect.width * 0.46, cardH = rect.height * 0.34
    for i in 0..<3 {
        let k = CGFloat(i)
        let r = NSRect(x: rect.midX - cardW / 2 - rect.width * 0.06 + k * rect.width * 0.06,
                       y: rect.midY - cardH / 2 - rect.height * 0.08 + k * rect.height * 0.07,
                       width: cardW, height: cardH)
        let p = NSBezierPath(roundedRect: r, xRadius: cardW * 0.12, yRadius: cardW * 0.12)
        NSColor.white.withAlphaComponent(0.35 + 0.3 * k).setFill()
        p.fill()
        if i == 2 {
            NSColor(calibratedRed: 0.42, green: 0.32, blue: 0.93, alpha: 0.55).setFill()
            for line in 0..<3 {
                let lw = cardW * (line == 2 ? 0.45 : 0.7)
                let lr = NSRect(x: r.minX + cardW * 0.12, y: r.maxY - cardH * (0.30 + CGFloat(line) * 0.22),
                                width: lw, height: max(1, cardH * 0.08))
                NSBezierPath(roundedRect: lr, xRadius: lr.height / 2, yRadius: lr.height / 2).fill()
            }
        }
    }

    // 四角星（sparkle）
    func sparkle(center c: NSPoint, size r: CGFloat) {
        let path = NSBezierPath()
        let pts = 4
        for j in 0..<(pts * 2) {
            let angle = CGFloat(j) * .pi / CGFloat(pts) + .pi / 2
            let rr = j % 2 == 0 ? r : r * 0.28
            let pt = NSPoint(x: c.x + cos(angle) * rr, y: c.y + sin(angle) * rr)
            if j == 0 { path.move(to: pt) } else { path.line(to: pt) }
        }
        path.close()
        NSColor.white.setFill()
        path.fill()
    }
    sparkle(center: NSPoint(x: rect.maxX - rect.width * 0.22, y: rect.maxY - rect.height * 0.21), size: rect.width * 0.12)
    sparkle(center: NSPoint(x: rect.maxX - rect.width * 0.36, y: rect.maxY - rect.height * 0.12), size: rect.width * 0.055)

    NSGraphicsContext.restoreGraphicsState()
    return rep.representation(using: .png, properties: [:])
}

for (name, px) in specs {
    guard let data = render(px) else { fatalError("render failed \(px)") }
    try! data.write(to: URL(fileURLWithPath: outDir).appendingPathComponent("\(name).png"))
}
print("icons ok")
SWIFT

if swiftc -swift-version 5 -target "${HOST_ARCH}-apple-macosx${MIN_MACOS}" "$ICON_WORK/make_icon.swift" -o "$ICON_WORK/make_icon" >/dev/null 2>&1 \
   && "$ICON_WORK/make_icon" "$ICON_WORK/AppIcon.iconset" >/dev/null \
   && iconutil -c icns "$ICON_WORK/AppIcon.iconset" -o "$APP_DIR/Contents/Resources/AppIcon.icns"; then
  echo "  ✓ AppIcon.icns"
else
  echo "  ⚠ 图标生成失败（不影响使用，将显示默认图标）"
fi

# ---------- 冒烟测试：生成示例空间并自检 ----------
echo "▸ 运行自检 …"
SELFTEST_OUT="$BUILD_DIR/selftest/GrokSpace_selftest.html"
if "$APP_DIR/Contents/MacOS/$EXEC_NAME" --self-test "$SELFTEST_OUT"; then
  echo "  ✓ 自检通过（可双击 build/selftest/GrokSpace_selftest.html 预览空间效果）"
else
  echo "  ⚠ 自检未通过，请把上面的输出反馈给作者" >&2
fi

# ---------- 本地 ad-hoc 签名（无需开发者账号） ----------
echo "▸ ad-hoc 签名 …"
xattr -cr "$APP_DIR" 2>/dev/null || true
codesign --force --deep --sign - "$APP_DIR"

echo "✓ 构建完成：$APP_DIR"
