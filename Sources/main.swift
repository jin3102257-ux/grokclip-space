import AppKit

// 显式 AppKit 入口（多文件 swiftc 编译时避免 @main 冲突）。
// 用法：
//   GrokClipSpace                         启动悬浮图标 + 菜单栏
//   GrokClipSpace --self-test <out.html>  不启动 UI，生成示例空间并自检（用于 build.sh 冒烟测试）

enum SelfTest {
    static func fail(_ message: String) -> Int32 {
        FileHandle.standardError.write(Data("SELF_TEST_FAIL \(message)\n".utf8))
        return 1
    }

    /// 生成一张 64×64 渐变 PNG（不依赖窗口服务器）
    static func makeSamplePNG() -> Data? {
        let w = 64, h = 64
        guard let rep = NSBitmapImageRep(bitmapDataPlanes: nil, pixelsWide: w, pixelsHigh: h,
                                         bitsPerSample: 8, samplesPerPixel: 4, hasAlpha: true, isPlanar: false,
                                         colorSpaceName: .deviceRGB, bytesPerRow: w * 4, bitsPerPixel: 32),
              let buffer = rep.bitmapData else { return nil }
        for y in 0..<h {
            for x in 0..<w {
                let o = (y * w + x) * 4
                buffer[o] = UInt8(79 + (139 - 79) * x / (w - 1))      // R: indigo → violet
                buffer[o + 1] = UInt8(70 + (92 - 70) * y / (h - 1))   // G
                buffer[o + 2] = UInt8(229 + (246 - 229) * x / (w - 1)) // B
                buffer[o + 3] = 255
            }
        }
        return rep.representation(using: .png, properties: [:])
    }

    static func run(outputPath: String) -> Int32 {
        let fm = FileManager.default
        let tmp = fm.temporaryDirectory.appendingPathComponent("GrokClipSelfTest-\(UUID().uuidString)", isDirectory: true)
        defer { try? fm.removeItem(at: tmp) }
        do {
            try fm.createDirectory(at: tmp, withIntermediateDirectories: true)
            guard let png = makeSamplePNG() else { return fail("无法生成示例 PNG") }
            let pngURL = tmp.appendingPathComponent("示例图片.png")
            try png.write(to: pngURL)
            let txtURL = tmp.appendingPathComponent("notes.md")
            try Data("# 附件笔记\n\n- 中文内容\n- </script><script>alert('x')</script>\n".utf8).write(to: txtURL)

            let clip1 = """
            # GrokClip 自检 · 标题

            这是一段**中文**原文，含 `inline code`、*斜体*、~~删除线~~ 和 [安全链接](https://example.com)，以及 [恶意链接](javascript:alert(1))。

            | 功能 | 状态 |
            | :--- | ---: |
            | 收录 | ✅ |
            | 导出 | ✅ |

            - [x] 已完成
            - [ ] 待办
            """
            let clip2 = """
            ## 代码块里包含 </script>

            ```html
            <script>alert("xss")</script>
            </script><!-- 不应提前闭合 -->
            ```

            > 引用：<img src=x onerror=alert(1)>
            \u{2028}行分隔符\u{2029}段分隔符
            """

            let items = [
                ClipItem(sourceApp: "Grok Bot", rawContent: clip1, attachments: [ClipAttachment(path: pngURL.path)]),
                ClipItem(sourceApp: "Grok Bot", rawContent: clip2, attachments: [ClipAttachment(path: txtURL.path)]),
            ]
            let built = SpaceHTMLGenerator.buildHTML(clips: items, title: "GrokClip 自检空间")
            let html = built.html

            // 1) 只允许出现 2 个真正的 </script>（JSON 数据块 + 应用脚本）
            let closeCount = html.components(separatedBy: "</script>").count - 1
            guard closeCount == 2 else { return fail("</script> 数量异常：\(closeCount)") }

            // 2) 取回内嵌 JSON 并逐字比对原文
            guard let start = html.range(of: "<script type=\"application/json\" id=\"grokclip-payload\">"),
                  let end = html.range(of: "</script>", range: start.upperBound..<html.endIndex) else {
                return fail("找不到内嵌 JSON")
            }
            let json = String(html[start.upperBound..<end.lowerBound])
            let payload = try JSONDecoder().decode(SpacePayload.self, from: Data(json.utf8))
            guard payload.clips.count == 2,
                  payload.clips[0].rawContent == clip1,
                  payload.clips[1].rawContent == clip2 else { return fail("原文往返不一致") }
            guard payload.clips[0].attachments.first?.kind == "image",
                  payload.clips[0].attachments.first?.embedded == true,
                  let b64 = payload.clips[0].attachments.first?.base64,
                  Data(base64Encoded: b64) == png else { return fail("图片附件内嵌失败") }
            guard payload.clips[1].attachments.first?.kind == "text",
                  payload.clips[1].attachments.first?.textPreview?.contains("</script>") == true else {
                return fail("文本附件预览失败")
            }

            // 3) 写出
            let outURL = URL(fileURLWithPath: (outputPath as NSString).expandingTildeInPath)
            try fm.createDirectory(at: outURL.deletingLastPathComponent(), withIntermediateDirectories: true)
            let data = Data(html.utf8)
            try data.write(to: outURL, options: .atomic)
            print("SELF_TEST_OK \(outURL.path) \(data.count)")
            return 0
        } catch {
            return fail(error.localizedDescription)
        }
    }
}

let arguments = CommandLine.arguments
if let flagIndex = arguments.firstIndex(of: "--self-test") {
    let output = flagIndex + 1 < arguments.count
        ? arguments[flagIndex + 1]
        : FileManager.default.temporaryDirectory.appendingPathComponent("GrokSpace_selftest.html").path
    exit(SelfTest.run(outputPath: output))
}

// 单实例：已在运行则直接退出
if let bundleID = Bundle.main.bundleIdentifier {
    let others = NSRunningApplication.runningApplications(withBundleIdentifier: bundleID)
        .filter { $0.processIdentifier != ProcessInfo.processInfo.processIdentifier }
    if !others.isEmpty {
        others.first?.activate(options: [])
        exit(0)
    }
}

let app = NSApplication.shared
let delegate = AppDelegate()
app.delegate = delegate
app.setActivationPolicy(.accessory)
app.run()
