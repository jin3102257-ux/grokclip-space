import Foundation
import UniformTypeIdentifiers

// MARK: - Payload (embedded as JSON inside the offline HTML)

struct SpacePayload: Codable {
    let version: Int
    let title: String
    let createdAt: String
    let createdAtDisplay: String
    let generator: String
    let clips: [SpaceClip]
}

struct SpaceClip: Codable {
    let index: Int
    let timestamp: String
    let timeDisplay: String
    let sourceApp: String
    let rawContent: String
    let attachments: [SpaceAttachment]
}

struct SpaceAttachment: Codable {
    let name: String
    let ext: String
    let mimeType: String
    let size: Int64
    let kind: String          // "image" | "pdf" | "text" | "file"
    let embedded: Bool
    let base64: String?
    let textPreview: String?
    let skipReason: String?
}

struct SpaceExportResult: Equatable {
    let url: URL
    let clipCount: Int
    let attachmentCount: Int
    let skippedAttachmentCount: Int
    let byteSize: Int
}

enum SpaceGeneratorError: LocalizedError {
    case noClips
    case cannotCreateDirectory(String)

    var errorDescription: String? {
        switch self {
        case .noClips: return "没有勾选任何条目"
        case .cannotCreateDirectory(let p): return "无法创建导出目录：\(p)"
        }
    }
}

// MARK: - Generator

enum SpaceHTMLGenerator {
    static let perFileEmbedLimit: Int64 = 25 * 1024 * 1024
    static let totalEmbedLimit: Int64 = 80 * 1024 * 1024
    static let textPreviewLimit: Int64 = 512 * 1024

    static let imageExtensions: Set<String> = ["png", "jpg", "jpeg", "gif", "webp", "svg", "bmp", "ico", "avif"]
    static let textExtensions: Set<String> = [
        "md", "markdown", "txt", "text", "csv", "tsv", "json", "jsonl", "yaml", "yml", "xml", "toml", "ini",
        "log", "html", "htm", "css", "js", "mjs", "ts", "tsx", "jsx", "py", "swift", "sh", "zsh", "bash",
        "rb", "go", "rs", "java", "kt", "c", "h", "cpp", "hpp", "m", "sql", "r", "php", "vue", "env", "conf",
    ]

    static let fallbackMimeTypes: [String: String] = [
        "png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg", "gif": "image/gif",
        "webp": "image/webp", "svg": "image/svg+xml", "bmp": "image/bmp", "ico": "image/x-icon",
        "avif": "image/avif", "pdf": "application/pdf",
        "md": "text/markdown", "markdown": "text/markdown", "txt": "text/plain", "csv": "text/csv",
        "tsv": "text/tab-separated-values", "json": "application/json", "html": "text/html",
        "xml": "application/xml", "yaml": "text/yaml", "yml": "text/yaml",
        "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        "xls": "application/vnd.ms-excel", "doc": "application/msword", "ppt": "application/vnd.ms-powerpoint",
        "zip": "application/zip", "mp4": "video/mp4", "mov": "video/quicktime", "mp3": "audio/mpeg",
        "wav": "audio/wav",
    ]

    // MARK: Public entry points

    /// 生成空间文件并写入导出目录，返回结果信息。
    static func writeSpace(clips: [ClipItem], title: String, exportDirectory: URL, now: Date = Date()) throws -> SpaceExportResult {
        guard !clips.isEmpty else { throw SpaceGeneratorError.noClips }
        do {
            try FileManager.default.createDirectory(at: exportDirectory, withIntermediateDirectories: true)
        } catch {
            throw SpaceGeneratorError.cannotCreateDirectory(exportDirectory.path)
        }
        let built = buildHTML(clips: clips, title: title, now: now)
        let url = uniqueURL(in: exportDirectory, baseName: fileBaseName(title: title, now: now), ext: "html")
        let data = Data(built.html.utf8)
        try data.write(to: url, options: .atomic)
        return SpaceExportResult(url: url,
                                 clipCount: clips.count,
                                 attachmentCount: built.attachmentCount,
                                 skippedAttachmentCount: built.skippedCount,
                                 byteSize: data.count)
    }

    /// 纯函数：生成完整 HTML 字符串。
    static func buildHTML(clips: [ClipItem], title: String, now: Date = Date()) -> (html: String, attachmentCount: Int, skippedCount: Int) {
        let trimmedTitle = title.trimmingCharacters(in: .whitespacesAndNewlines)
        let displayTitle = trimmedTitle.isEmpty ? "GrokClip 空间 · \(Formatters.dateTime.string(from: now))" : trimmedTitle

        var remainingBudget = totalEmbedLimit
        var attachmentCount = 0
        var skipped = 0
        let iso = ISO8601DateFormatter()

        var spaceClips: [SpaceClip] = []
        for (i, clip) in clips.enumerated() {
            var atts: [SpaceAttachment] = []
            for att in clip.attachments {
                let built = buildAttachment(path: att.path, budget: &remainingBudget)
                attachmentCount += 1
                if !built.embedded { skipped += 1 }
                atts.append(built)
            }
            spaceClips.append(SpaceClip(index: i + 1,
                                        timestamp: iso.string(from: clip.timestamp),
                                        timeDisplay: Formatters.dateTime.string(from: clip.timestamp),
                                        sourceApp: clip.sourceApp,
                                        rawContent: clip.rawContent,
                                        attachments: atts))
        }

        let payload = SpacePayload(version: 1,
                                   title: displayTitle,
                                   createdAt: iso.string(from: now),
                                   createdAtDisplay: Formatters.dateTime.string(from: now),
                                   generator: "GrokClip Space 1.0",
                                   clips: spaceClips)
        let json = encodePayloadForScriptTag(payload)

        // 先在模板切分点两侧替换标题，再拼接 JSON，保证 JSON 内容不会被二次替换。
        let parts = htmlTemplate.components(separatedBy: "__GROKCLIP_PAYLOAD__")
        let head = (parts.first ?? "").replacingOccurrences(of: "__GROKCLIP_TITLE__", with: escapeHTML(displayTitle))
        let tail = parts.dropFirst().joined(separator: "").replacingOccurrences(of: "__GROKCLIP_TITLE__", with: escapeHTML(displayTitle))
        return (head + json + tail, attachmentCount, skipped)
    }

    // MARK: JSON embedding

    /// 将 JSON 安全嵌入 `<script type="application/json">`：
    /// `</` → `<\/`，并把所有 `<`、`>`、`&`、U+2028、U+2029 转为 \uXXXX（都是合法 JSON 转义），
    /// 因此原文里的 `</script>`、`<!--` 永远不会提前闭合脚本标签。
    static func encodePayloadForScriptTag<T: Encodable>(_ value: T) -> String {
        let encoder = JSONEncoder()
        encoder.outputFormatting = [.sortedKeys]
        guard let data = try? encoder.encode(value), let raw = String(data: data, encoding: .utf8) else {
            return "{\"version\":1,\"title\":\"GrokClip\",\"clips\":[]}"
        }
        return escapeJSONForScript(raw)
    }

    static func escapeJSONForScript(_ json: String) -> String {
        var out = json.replacingOccurrences(of: "</", with: "<\\/")
        out = out.replacingOccurrences(of: "<", with: "\\u003c")
        out = out.replacingOccurrences(of: ">", with: "\\u003e")
        out = out.replacingOccurrences(of: "&", with: "\\u0026")
        out = out.replacingOccurrences(of: "\u{2028}", with: "\\u2028")
        out = out.replacingOccurrences(of: "\u{2029}", with: "\\u2029")
        return out
    }

    static func escapeHTML(_ s: String) -> String {
        var o = s.replacingOccurrences(of: "&", with: "&amp;")
        o = o.replacingOccurrences(of: "<", with: "&lt;")
        o = o.replacingOccurrences(of: ">", with: "&gt;")
        o = o.replacingOccurrences(of: "\"", with: "&quot;")
        o = o.replacingOccurrences(of: "'", with: "&#39;")
        return o
    }

    // MARK: Attachments

    static func mimeType(forExtension ext: String) -> String {
        let lower = ext.lowercased()
        if let known = fallbackMimeTypes[lower] { return known }
        if let type = UTType(filenameExtension: lower), let mime = type.preferredMIMEType { return mime }
        return "application/octet-stream"
    }

    static func kind(forExtension ext: String) -> String {
        let lower = ext.lowercased()
        if imageExtensions.contains(lower) { return "image" }
        if lower == "pdf" { return "pdf" }
        if textExtensions.contains(lower) { return "text" }
        if let type = UTType(filenameExtension: lower) {
            if type.conforms(to: .pdf) { return "pdf" }
            if type.conforms(to: .sourceCode) || type.conforms(to: .plainText) || type.conforms(to: .json) { return "text" }
        }
        return "file"
    }

    static func buildAttachment(path: String, budget: inout Int64) -> SpaceAttachment {
        let url = URL(fileURLWithPath: path)
        let name = url.lastPathComponent
        let ext = url.pathExtension.lowercased()
        let mime = Self.mimeType(forExtension: ext)
        let fileKind = Self.kind(forExtension: ext)
        let size = ((try? FileManager.default.attributesOfItem(atPath: path))?[.size] as? NSNumber)?.int64Value ?? -1

        func skipped(_ reason: String, preview: String? = nil) -> SpaceAttachment {
            SpaceAttachment(name: name, ext: ext, mimeType: mime, size: max(size, 0), kind: fileKind,
                            embedded: false, base64: nil, textPreview: preview, skipReason: reason)
        }

        guard size >= 0 else { return skipped("原文件已不存在或无法读取，请单独发送原文件") }
        guard size <= perFileEmbedLimit else { return skipped("文件超过单文件内嵌上限，请单独发送原文件") }

        guard let data = try? Data(contentsOf: url, options: .mappedIfSafe) else {
            return skipped("原文件无法读取（可能缺少访问权限），请单独发送原文件")
        }

        var preview: String? = nil
        if fileKind == "text" && Int64(data.count) <= textPreviewLimit {
            preview = String(decoding: data, as: UTF8.self)
        }

        guard Int64(data.count) <= budget else {
            return skipped("附件总大小超过 80 MB 内嵌上限，请单独发送原文件", preview: preview)
        }
        budget -= Int64(data.count)

        return SpaceAttachment(name: name, ext: ext, mimeType: mime, size: Int64(data.count), kind: fileKind,
                               embedded: true, base64: data.base64EncodedString(), textPreview: preview,
                               skipReason: nil)
    }

    // MARK: File naming

    static func sanitizeTitle(_ title: String) -> String {
        let illegal = CharacterSet(charactersIn: "/\\:*?\"<>|\n\r\t").union(.controlCharacters)
        var cleaned = title.unicodeScalars.map { illegal.contains($0) ? "_" : String($0) }.joined()
        cleaned = cleaned.trimmingCharacters(in: CharacterSet.whitespaces.union(CharacterSet(charactersIn: "._")))
        while cleaned.contains("__") { cleaned = cleaned.replacingOccurrences(of: "__", with: "_") }
        if cleaned.count > 60 { cleaned = String(cleaned.prefix(60)) }
        return cleaned
    }

    static func fileBaseName(title: String, now: Date) -> String {
        let stamp = Formatters.fileStamp.string(from: now)
        let clean = sanitizeTitle(title)
        return clean.isEmpty ? "GrokSpace_\(stamp)" : "\(clean)_\(stamp)"
    }

    static func uniqueURL(in dir: URL, baseName: String, ext: String) -> URL {
        var candidate = dir.appendingPathComponent("\(baseName).\(ext)")
        var n = 2
        while FileManager.default.fileExists(atPath: candidate.path) {
            candidate = dir.appendingPathComponent("\(baseName)_\(n).\(ext)")
            n += 1
        }
        return candidate
    }

    // MARK: - HTML template (fully offline, zero CDN)
    // 模板中不包含任何外部资源；JS 只从内嵌 JSON 读取数据，并对所有文本做 HTML 转义。

    static let htmlTemplate: String = #"""
<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="referrer" content="no-referrer">
<meta http-equiv="Content-Security-Policy" content="base-uri 'none'; form-action 'none'">
<meta name="generator" content="GrokClip Space">
<title>__GROKCLIP_TITLE__</title>
<style>
:root{
  --bg:#f7f5f0;--panel:#ffffff;--panel-2:#fbfaf7;--text:#1f1d1a;--muted:#7a756c;--line:#e8e3d9;
  --accent:#5b4bdb;--accent-2:#8b5cf6;--accent-soft:rgba(91,75,219,.09);--code-bg:#f3f0ea;--code-text:#2b2722;
  --ok:#16a34a;--shadow:0 1px 2px rgba(31,29,26,.04),0 6px 24px rgba(31,29,26,.06);
  --radius:14px;--mono:ui-monospace,SFMono-Regular,Menlo,Consolas,"Liberation Mono",monospace;
  --sans:-apple-system,BlinkMacSystemFont,"PingFang SC","Hiragino Sans GB","Microsoft YaHei","Segoe UI",Roboto,Helvetica,Arial,sans-serif;
  color-scheme:light;
}
:root[data-theme="dark"]{
  --bg:#141416;--panel:#1c1c20;--panel-2:#212126;--text:#ecebe8;--muted:#9a978f;--line:#2e2e34;
  --accent:#8b7cf6;--accent-2:#a78bfa;--accent-soft:rgba(139,124,246,.14);--code-bg:#111114;--code-text:#e6e3dc;
  --shadow:0 1px 2px rgba(0,0,0,.3),0 8px 28px rgba(0,0,0,.35);color-scheme:dark;
}
*{box-sizing:border-box}
html{scroll-behavior:smooth;scroll-padding-top:84px}
body{margin:0;background:var(--bg);color:var(--text);font:15px/1.7 var(--sans);-webkit-font-smoothing:antialiased;transition:background .25s,color .25s}
button{font:inherit;cursor:pointer}
a{color:var(--accent)}
.topbar{position:sticky;top:0;z-index:20;backdrop-filter:saturate(1.6) blur(14px);-webkit-backdrop-filter:saturate(1.6) blur(14px);background:rgba(247,245,240,.84);border-bottom:1px solid var(--line)}
.topbar-inner{max-width:1180px;margin:0 auto;padding:12px 24px;display:flex;align-items:center;gap:16px;flex-wrap:wrap}
.brand{display:flex;align-items:center;gap:12px;min-width:0;flex:1}
.logo{width:34px;height:34px;border-radius:10px;flex:none;background:linear-gradient(135deg,#4f46e5,#8b5cf6);box-shadow:0 4px 14px rgba(91,75,219,.35);display:grid;place-items:center;color:#fff;font-size:17px}
.title-wrap{min-width:0}
.space-title{font-size:17px;font-weight:650;margin:0;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;letter-spacing:-.01em}
.space-meta{font-size:12.5px;color:var(--muted)}
.actions{display:flex;gap:8px;flex-wrap:wrap}
.btn{border:1px solid var(--line);background:var(--panel);color:var(--text);padding:7px 13px;border-radius:9px;font-size:13px;display:inline-flex;align-items:center;gap:6px;transition:all .15s;white-space:nowrap}
.btn:hover{border-color:var(--accent);color:var(--accent)}
.btn.primary{background:linear-gradient(135deg,#4f46e5,#8b5cf6);color:#fff;border-color:transparent;box-shadow:0 3px 12px rgba(91,75,219,.3)}
.btn.primary:hover{filter:brightness(1.08);color:#fff}
.btn-mini{border:1px solid var(--line);background:var(--panel);color:var(--muted);padding:3px 9px;border-radius:7px;font-size:12px;transition:all .15s;white-space:nowrap}
.btn-mini:hover{color:var(--accent);border-color:var(--accent)}
.btn-mini.on{background:var(--accent-soft);color:var(--accent);border-color:transparent}
.layout{max-width:1180px;margin:0 auto;padding:24px;display:grid;grid-template-columns:250px minmax(0,1fr);gap:28px}
.sidebar{position:sticky;top:84px;align-self:start;max-height:calc(100vh - 104px);overflow:auto;padding-right:4px}
.side-label{font-size:11.5px;letter-spacing:.08em;color:var(--muted);text-transform:uppercase;margin:4px 10px 8px}
.toc a{display:flex;gap:9px;align-items:flex-start;padding:7px 10px;border-radius:9px;color:var(--text);text-decoration:none;font-size:13px;line-height:1.45;transition:background .15s}
.toc a:hover{background:var(--accent-soft)}
.toc a.active{background:var(--accent-soft);color:var(--accent)}
.toc .num{font:600 11.5px var(--mono);color:var(--accent);padding-top:2px;flex:none}
.toc .txt{flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;word-break:break-all}
.pill{font-size:11px;padding:0 7px;border-radius:999px;background:var(--panel);border:1px solid var(--line);color:var(--muted);flex:none;line-height:18px}
.stream{display:flex;flex-direction:column;gap:22px;min-width:0}
.card{background:var(--panel);border:1px solid var(--line);border-radius:var(--radius);box-shadow:var(--shadow);overflow:hidden;scroll-margin-top:90px}
.card-head{display:flex;align-items:center;gap:10px;padding:12px 18px;border-bottom:1px solid var(--line);background:var(--panel-2);flex-wrap:wrap}
.badge{font:700 12px var(--mono);color:#fff;background:linear-gradient(135deg,#4f46e5,#8b5cf6);padding:2px 9px;border-radius:7px}
.meta{font-size:12.5px;color:var(--muted);flex:1;min-width:120px}
.card-tools{display:flex;gap:6px}
.card-body{padding:18px 22px 20px}
.raw{display:none;white-space:pre-wrap;word-break:break-word;font:13px/1.65 var(--mono);background:var(--code-bg);color:var(--code-text);padding:14px 16px;border-radius:10px;margin:0}
.card.show-raw .raw{display:block}
.card.show-raw .md{display:none}
.empty-note{color:var(--muted);font-style:italic;font-size:13.5px}
.md{word-break:break-word}
.md>*:first-child{margin-top:0}.md>*:last-child{margin-bottom:0}
.md h1,.md h2,.md h3,.md h4,.md h5,.md h6{line-height:1.35;margin:1.3em 0 .55em;letter-spacing:-.01em}
.md h1{font-size:1.55em}.md h2{font-size:1.32em}.md h3{font-size:1.15em}.md h4{font-size:1.02em}.md h5,.md h6{font-size:.95em;color:var(--muted)}
.md p{margin:.6em 0}
.md ul,.md ol{padding-left:1.5em;margin:.5em 0}
.md li{margin:.22em 0}
.md li.task{list-style:none;margin-left:-1.3em}
.md li.task input{margin-right:.45em;vertical-align:-1px}
.md blockquote{margin:.8em 0;padding:.25em 1em;border-left:3px solid var(--accent);background:var(--accent-soft);border-radius:0 8px 8px 0;color:var(--text)}
.md hr{border:none;border-top:1px solid var(--line);margin:1.4em 0}
.md code{font:.88em var(--mono);background:var(--code-bg);color:var(--code-text);padding:.12em .4em;border-radius:5px}
.md del{opacity:.65}
.md .table-wrap{overflow-x:auto;margin:.9em 0;border:1px solid var(--line);border-radius:10px}
.md table{border-collapse:collapse;width:100%;font-size:.93em}
.md th,.md td{border-bottom:1px solid var(--line);padding:8px 12px;text-align:left;vertical-align:top}
.md th{background:var(--panel-2);font-weight:600}
.md tr:last-child td{border-bottom:none}
.code-block{margin:.9em 0;border-radius:10px;overflow:hidden;border:1px solid var(--line);background:var(--code-bg)}
.code-head{display:flex;justify-content:space-between;align-items:center;padding:5px 8px 5px 14px;border-bottom:1px solid var(--line);font:12px var(--mono);color:var(--muted)}
.code-block pre{margin:0;padding:14px 16px;overflow:auto;font:13px/1.6 var(--mono);color:var(--code-text)}
.code-block pre code{background:none;padding:0;font:inherit;color:inherit}
.atts{display:grid;grid-template-columns:repeat(auto-fill,minmax(220px,1fr));gap:12px;margin-top:18px;padding-top:16px;border-top:1px dashed var(--line)}
.att{border:1px solid var(--line);border-radius:12px;background:var(--panel-2);overflow:hidden;display:flex;flex-direction:column;min-width:0}
.att.wide{grid-column:1/-1}
.att-thumb{display:block;width:100%;height:150px;object-fit:cover;background:var(--code-bg);cursor:zoom-in;border:none;padding:0}
.att-thumb img{width:100%;height:100%;object-fit:cover;display:block}
.att-info{display:flex;gap:10px;align-items:center;padding:10px 12px}
.att-icon{font-size:24px;flex:none;width:34px;text-align:center}
.att-text{min-width:0;flex:1}
.att-name{font-size:13px;font-weight:600;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.att-sub{font-size:11.5px;color:var(--muted)}
.att-actions{display:flex;gap:6px;flex-wrap:wrap;padding:0 12px 12px}
.att-warn{font-size:12px;color:#b45309;padding:0 12px 12px}
.att details{padding:0 12px 12px}
.att summary{cursor:pointer;font-size:12.5px;color:var(--accent);margin-bottom:6px}
.att pre{margin:0;max-height:360px;overflow:auto;white-space:pre-wrap;word-break:break-word;font:12.5px/1.6 var(--mono);background:var(--code-bg);color:var(--code-text);padding:12px;border-radius:8px}
.lightbox{position:fixed;inset:0;z-index:60;background:rgba(10,10,12,.86);display:none;flex-direction:column;align-items:center;justify-content:center;gap:14px;padding:28px}
.lightbox.open{display:flex}
.lightbox img{max-width:94vw;max-height:80vh;border-radius:10px;box-shadow:0 20px 60px rgba(0,0,0,.5);background:#fff}
.lightbox .lb-bar{display:flex;gap:10px;align-items:center;color:#eee;font-size:13px}
.lightbox .btn{background:rgba(255,255,255,.12);color:#fff;border-color:rgba(255,255,255,.2)}
.modal{position:fixed;inset:0;z-index:70;background:rgba(10,10,12,.55);display:none;align-items:center;justify-content:center;padding:24px}
.modal.open{display:flex}
.modal-card{background:var(--panel);border-radius:14px;padding:18px;width:min(720px,100%);box-shadow:var(--shadow);border:1px solid var(--line)}
.modal-card textarea{width:100%;height:320px;font:12.5px/1.6 var(--mono);background:var(--code-bg);color:var(--code-text);border:1px solid var(--line);border-radius:8px;padding:10px;resize:vertical}
.modal-card .row{display:flex;justify-content:space-between;align-items:center;margin-bottom:10px;gap:10px}
.toast{position:fixed;left:50%;bottom:28px;transform:translateX(-50%) translateY(20px);background:#1f1d1a;color:#fff;padding:9px 18px;border-radius:999px;font-size:13.5px;opacity:0;pointer-events:none;transition:all .25s;z-index:80;box-shadow:0 8px 24px rgba(0,0,0,.25)}
:root[data-theme="dark"] .topbar{background:rgba(20,20,22,.84)}
:root[data-theme="dark"] .toast{background:#ecebe8;color:#141416}
.toast.show{opacity:1;transform:translateX(-50%) translateY(0)}
.footer{max-width:1180px;margin:0 auto;padding:6px 24px 40px;color:var(--muted);font-size:12px;text-align:center}
@media (max-width:860px){
  .layout{grid-template-columns:1fr;padding:16px;gap:16px}
  .sidebar{position:static;max-height:none}
  .toc{display:flex;gap:6px;overflow-x:auto;padding-bottom:4px}
  .toc a{flex:none;max-width:220px}
  .topbar-inner{padding:10px 16px}
  .card-body{padding:14px 16px}
  .side-label{display:none}
}
@media print{.topbar,.sidebar,.card-tools,.att-actions{display:none}.layout{display:block}.card{break-inside:avoid;box-shadow:none}}
</style>
</head>
<body>
<header class="topbar"><div class="topbar-inner">
  <div class="brand"><div class="logo">✦</div><div class="title-wrap"><h1 class="space-title" id="space-title">__GROKCLIP_TITLE__</h1><div class="space-meta" id="space-meta"></div></div></div>
  <div class="actions">
    <button class="btn primary" data-action="copy-all">复制全部纯原文</button>
    <button class="btn" data-action="download-md">下载原文 .md</button>
    <button class="btn" data-action="toggle-theme" id="theme-btn">深色</button>
  </div>
</div></header>
<div class="layout">
  <aside class="sidebar"><div class="side-label">目录</div><nav class="toc" id="toc"></nav></aside>
  <main class="stream" id="stream"></main>
</div>
<div class="footer">由 GrokClip Space 离线生成 · 单文件、零外链、数据不上传任何服务器</div>
<div class="lightbox" id="lightbox" role="dialog" aria-modal="true">
  <img id="lb-img" alt="">
  <div class="lb-bar"><span id="lb-name"></span><button class="btn" data-action="lb-download">下载原图</button><button class="btn" data-action="lb-close">关闭</button></div>
</div>
<div class="modal" id="copy-modal"><div class="modal-card">
  <div class="row"><strong>浏览器限制了自动复制，请按 ⌘C / Ctrl+C 手动复制</strong><button class="btn-mini" data-action="modal-close">关闭</button></div>
  <textarea id="copy-area" readonly></textarea>
</div></div>
<div class="toast" id="toast"></div>
<script type="application/json" id="grokclip-payload">__GROKCLIP_PAYLOAD__</script>
<script>
(function () {
'use strict';
// @@PURE_START
function escapeHtml(s) {
  return String(s == null ? '' : s)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}
function unescapeBasic(s) {
  return String(s).replace(/&quot;/g, '"').replace(/&#39;/g, "'").replace(/&lt;/g, '<').replace(/&gt;/g, '>').replace(/&amp;/g, '&');
}
// 只允许 http/https/mailto/锚点/相对路径；拦截 javascript:、data:、vbscript:、file: 等
function isSafeUrl(rawUrl) {
  var u = unescapeBasic(rawUrl).replace(/&#x?[0-9a-f]+;?/gi, '').replace(/[\u0000-\u0020\u007f-\u009f]/g, '').toLowerCase();
  if (!u) return false;
  if (/^(https?:|mailto:)/.test(u)) return true;
  if (/^(#|\/|\.\/|\.\.\/|\?)/.test(u)) return true;
  return u.indexOf(':') < 0;
}
function renderInline(src) {
  var tokens = [];
  function stash(html) { tokens.push(html); return '\u0000' + (tokens.length - 1) + '\u0000'; }
  var s = String(src == null ? '' : src).replace(/\u0000/g, '');
  // 1) 行内代码（先抽出，内部不做任何格式化）
  s = s.replace(/(`+)([^`]|[^`][\s\S]*?[^`])\1(?!`)/g, function (m, ticks, code) {
    return stash('<code>' + escapeHtml(code.replace(/^ (.*) $/, '$1')) + '</code>');
  });
  // 2) 全量 HTML 转义
  s = escapeHtml(s);
  // 3) 图片语法 → 安全链接（离线页面不加载远程图片）
  s = s.replace(/!\[([^\]\n]*)\]\(([^)\s]+)(?:\s+&quot;[^\n]*?&quot;)?\)/g, function (m, alt, url) {
    var label = '🖼 ' + (alt || '图片');
    return isSafeUrl(url) ? stash('<a href="' + url + '" target="_blank" rel="noopener noreferrer">' + label + '</a>') : label;
  });
  // 4) 链接
  s = s.replace(/\[([^\]\n]+)\]\(([^)\s]+)(?:\s+&quot;[^\n]*?&quot;)?\)/g, function (m, text, url) {
    var inner = formatEmphasis(text);
    return isSafeUrl(url) ? stash('<a href="' + url + '" target="_blank" rel="noopener noreferrer">' + inner + '</a>') : inner;
  });
  // 5) 裸链接
  s = s.replace(/(^|[\s(（])(https?:\/\/[^\s<>"'）]+[^\s<>"'）.,;:!?。，；：！？)\]])/g, function (m, pre, url) {
    return pre + stash('<a href="' + url + '" target="_blank" rel="noopener noreferrer">' + url + '</a>');
  });
  // 6) 强调
  s = formatEmphasis(s);
  // 7) 还原占位
  for (var guard = 0; guard < 3 && /\u0000\d+\u0000/.test(s); guard++) {
    s = s.replace(/\u0000(\d+)\u0000/g, function (m, i) { return tokens[+i]; });
  }
  return s;
}
function formatEmphasis(s) {
  return s
    .replace(/\*\*(?=\S)([\s\S]*?\S)\*\*/g, '<strong>$1</strong>')
    .replace(/__(?=\S)([\s\S]*?\S)__/g, '<strong>$1</strong>')
    .replace(/~~(?=\S)([\s\S]*?\S)~~/g, '<del>$1</del>')
    .replace(/(^|[^*\w])\*(?=[^\s*])([^*\n]*?[^\s*])\*(?![*\w])/g, '$1<em>$2</em>')
    .replace(/(^|[^_\w])_(?=[^\s_])([^_\n]*?[^\s_])_(?![_\w])/g, '$1<em>$2</em>');
}
var LIST_RE = /^(\s*)([-*+]|\d{1,9}[.)])\s+(.*)$/;
var FENCE_RE = /^ {0,3}(`{3,}|~{3,})\s*([^\s`]*)[^`]*$/;
function indentWidth(ws) { var w = 0; for (var i = 0; i < ws.length; i++) w += ws[i] === '\t' ? 4 : 1; return w; }
function leadingWs(line) { return (line.match(/^[ \t]*/) || [''])[0]; }
function isBlank(line) { return /^\s*$/.test(line); }
function splitRow(line) {
  var s = line.trim();
  if (s.charAt(0) === '|') s = s.slice(1);
  if (s.length && s.charAt(s.length - 1) === '|' && s.charAt(s.length - 2) !== '\\') s = s.slice(0, -1);
  var cells = [], cur = '', inCode = false;
  for (var k = 0; k < s.length; k++) {
    var c = s.charAt(k);
    if (c === '\\' && s.charAt(k + 1) === '|') { cur += '|'; k++; continue; }
    if (c === '`') inCode = !inCode;
    if (c === '|' && !inCode) { cells.push(cur.trim()); cur = ''; continue; }
    cur += c;
  }
  cells.push(cur.trim());
  return cells;
}
function isTableSep(line) {
  if (line == null || line.indexOf('-') < 0) return false;
  var cells = splitRow(line);
  if (cells.length < 2 && line.indexOf('|') < 0) return false;
  return cells.length > 0 && cells.every(function (c) { return /^:?-+:?$/.test(c); });
}
function isTableStart(lines, i) { return lines[i].indexOf('|') >= 0 && isTableSep(lines[i + 1]); }
function isHr(line) { return /^ {0,3}([-*_])(?:[ \t]*\1){2,}[ \t]*$/.test(line); }
function isBlockStart(lines, i) {
  var l = lines[i];
  return FENCE_RE.test(l) || /^ {0,3}#{1,6}\s/.test(l) || isHr(l) || /^ {0,3}>/.test(l) || LIST_RE.test(l) || isTableStart(lines, i);
}
function codeBlock(code, lang) {
  return '<div class="code-block"><div class="code-head"><span>' + escapeHtml(lang || 'text') +
    '</span><button class="btn-mini" data-action="copy-code">复制代码</button></div><pre><code>' +
    escapeHtml(code) + '</code></pre></div>';
}
function dedent(lines) {
  var min = Infinity;
  lines.forEach(function (l) { if (!isBlank(l)) min = Math.min(min, indentWidth(leadingWs(l))); });
  if (!isFinite(min) || min === 0) return lines;
  return lines.map(function (l) {
    var w = 0, k = 0;
    while (k < l.length && w < min && (l[k] === ' ' || l[k] === '\t')) { w += l[k] === '\t' ? 4 : 1; k++; }
    return l.slice(k);
  });
}
function renderListItem(item) {
  var text = item.text, cls = '', box = '';
  var tm = text.match(/^\[([ xX])\]\s+([\s\S]*)$/);
  if (tm) { cls = ' class="task"'; box = '<input type="checkbox" disabled' + (tm[1] === ' ' ? '' : ' checked') + '> '; text = tm[2]; }
  var html = box + text.split('\n').map(renderInline).join('<br>');
  if (item.sub.length) html += renderBlocks(dedent(item.sub), item.depth + 1);
  return '<li' + cls + '>' + html + '</li>';
}
function parseList(lines, start, depth) {
  var m0 = lines[start].match(LIST_RE);
  var base = indentWidth(m0[1]);
  var ordered = /\d/.test(m0[2]);
  var startNum = ordered ? parseInt(m0[2], 10) : 1;
  var items = [], cur = null, i = start;
  while (i < lines.length) {
    var line = lines[i];
    if (isBlank(line)) {
      var j = i + 1;
      while (j < lines.length && isBlank(lines[j])) j++;
      if (j < lines.length) {
        var nm = lines[j].match(LIST_RE), nind = indentWidth(leadingWs(lines[j]));
        if ((nind > base && cur) || (nm && nind === base && /\d/.test(nm[2]) === ordered)) {
          if (cur && nind > base) cur.sub.push('');
          i = j; continue;
        }
      }
      break;
    }
    var m = line.match(LIST_RE), ind = indentWidth(leadingWs(line));
    if (m && ind === base) {
      if (/\d/.test(m[2]) !== ordered) break;
      cur = { text: m[3], sub: [], depth: depth };
      items.push(cur); i++; continue;
    }
    if (ind > base && cur) { cur.sub.push(line); i++; continue; }
    if (ind <= base && cur && !m && !isBlockStart(lines, i)) { cur.text += '\n' + line.trim(); i++; continue; }
    break;
  }
  var tag = ordered ? 'ol' : 'ul';
  var attr = ordered && startNum !== 1 ? ' start="' + startNum + '"' : '';
  return { html: '<' + tag + attr + '>' + items.map(renderListItem).join('') + '</' + tag + '>', next: i };
}
function renderTable(lines, i) {
  var header = splitRow(lines[i]);
  var aligns = splitRow(lines[i + 1]).map(function (c) {
    var l = c.charAt(0) === ':', r = c.charAt(c.length - 1) === ':';
    return l && r ? 'center' : r ? 'right' : l ? 'left' : '';
  });
  function cell(tag, text, idx) {
    var a = aligns[idx] ? ' style="text-align:' + aligns[idx] + '"' : '';
    return '<' + tag + a + '>' + renderInline(text) + '</' + tag + '>';
  }
  var html = '<div class="table-wrap"><table><thead><tr>' + header.map(function (h, k) { return cell('th', h, k); }).join('') + '</tr></thead><tbody>';
  i += 2;
  while (i < lines.length && !isBlank(lines[i]) && lines[i].indexOf('|') >= 0) {
    var cells = splitRow(lines[i]);
    var row = [];
    for (var k = 0; k < header.length; k++) row.push(cell('td', cells[k] == null ? '' : cells[k], k));
    html += '<tr>' + row.join('') + '</tr>';
    i++;
  }
  return { html: html + '</tbody></table></div>', next: i };
}
function renderBlocks(lines, depth) {
  if (depth > 24) return '<p>' + escapeHtml(lines.join('\n')) + '</p>';
  var out = [], i = 0;
  while (i < lines.length) {
    var line = lines[i];
    if (isBlank(line)) { i++; continue; }
    var fm = line.match(FENCE_RE);
    if (fm) {
      var fence = fm[1], lang = fm[2] || '', buf = [];
      var closeRe = new RegExp('^ {0,3}' + (fence.charAt(0) === '`' ? '`' : '~') + '{' + fence.length + ',}\\s*$');
      i++;
      while (i < lines.length && !closeRe.test(lines[i])) { buf.push(lines[i]); i++; }
      i++;
      out.push(codeBlock(buf.join('\n'), lang));
      continue;
    }
    var hm = line.match(/^ {0,3}(#{1,6})\s+(.*?)(?:\s+#+)?\s*$/);
    if (hm) { var lv = hm[1].length; out.push('<h' + lv + '>' + renderInline(hm[2]) + '</h' + lv + '>'); i++; continue; }
    if (isHr(line)) { out.push('<hr>'); i++; continue; }
    if (isTableStart(lines, i)) { var t = renderTable(lines, i); out.push(t.html); i = t.next; continue; }
    if (/^ {0,3}>/.test(line)) {
      var q = [];
      while (i < lines.length && /^ {0,3}>/.test(lines[i])) { q.push(lines[i].replace(/^ {0,3}> ?/, '')); i++; }
      out.push('<blockquote>' + renderBlocks(q, depth + 1) + '</blockquote>');
      continue;
    }
    if (LIST_RE.test(line)) { var l = parseList(lines, i, depth); out.push(l.html); i = l.next; continue; }
    var p = [line.trim()];
    i++;
    while (i < lines.length && !isBlank(lines[i]) && !isBlockStart(lines, i)) { p.push(lines[i].trim()); i++; }
    out.push('<p>' + p.map(renderInline).join('<br>') + '</p>');
  }
  return out.join('\n');
}
function renderMarkdown(md) {
  return renderBlocks(String(md == null ? '' : md).replace(/\r\n?/g, '\n').split('\n'), 0);
}
function base64ToBytes(b64) {
  var bin = atob(String(b64 || '').replace(/\s+/g, ''));
  var len = bin.length, bytes = new Uint8Array(len);
  for (var i = 0; i < len; i++) bytes[i] = bin.charCodeAt(i);
  return bytes;
}
function humanSize(n) {
  n = Number(n) || 0;
  if (n < 1024) return n + ' B';
  var u = ['KB', 'MB', 'GB'], i = -1;
  do { n /= 1024; i++; } while (n >= 1024 && i < u.length - 1);
  return (n >= 10 ? n.toFixed(0) : n.toFixed(1)) + ' ' + u[i];
}
function previewLine(raw) {
  var lines = String(raw || '').split('\n');
  for (var i = 0; i < lines.length; i++) {
    var t = lines[i].replace(/^\s*(#{1,6}\s+|>\s*|[-*+]\s+(\[[ xX]\]\s+)?|\d+[.)]\s+|`{3,}.*$|\|)/, '')
      .replace(/[*_`~|]/g, '').trim();
    if (t) return t.length > 40 ? t.slice(0, 40) + '…' : t;
  }
  return '';
}
function joinRaw(clips, sep) {
  return clips.map(function (c) { return c.rawContent || ''; }).filter(function (t) { return t.length > 0; }).join(sep);
}
// @@PURE_END

var GC = { escapeHtml: escapeHtml, renderMarkdown: renderMarkdown, renderInline: renderInline, isSafeUrl: isSafeUrl,
  base64ToBytes: base64ToBytes, humanSize: humanSize, previewLine: previewLine, joinRaw: joinRaw };
if (typeof window !== 'undefined') window.__GrokClip = GC;
if (typeof document === 'undefined') return;

var payload;
try { payload = JSON.parse(document.getElementById('grokclip-payload').textContent || '{}'); }
catch (e) { payload = { title: '空间数据损坏', clips: [] }; }
var clips = Array.isArray(payload.clips) ? payload.clips : [];
var blobCache = {};
var lbCurrent = null;
var $ = function (id) { return document.getElementById(id); };
function pad2(n) { return (n < 10 ? '0' : '') + n; }
function attAt(ci, ai) { var c = clips[ci]; return c && c.attachments ? c.attachments[ai] : null; }

function iconFor(att) {
  var e = (att.ext || '').toLowerCase();
  if (att.kind === 'image') return '🖼️';
  if (att.kind === 'pdf') return '📕';
  if (/^(doc|docx|pages|rtf)$/.test(e)) return '📝';
  if (/^(xls|xlsx|numbers|csv|tsv)$/.test(e)) return '📊';
  if (/^(ppt|pptx|key)$/.test(e)) return '📽️';
  if (/^(zip|rar|7z|tar|gz|tgz|bz2|xz)$/.test(e)) return '🗜️';
  if (/^(mp4|mov|avi|mkv|webm)$/.test(e)) return '🎬';
  if (/^(mp3|wav|m4a|aac|flac)$/.test(e)) return '🎵';
  if (att.kind === 'text') return '📄';
  return '📎';
}
function getBlobUrl(att) {
  var key = att.__key;
  if (blobCache[key]) return blobCache[key];
  var blob = new Blob([base64ToBytes(att.base64)], { type: att.mimeType || 'application/octet-stream' });
  blobCache[key] = URL.createObjectURL(blob);
  return blobCache[key];
}
function triggerDownload(url, name) {
  var a = document.createElement('a');
  a.href = url; a.download = name || 'download'; a.rel = 'noopener'; a.style.display = 'none';
  document.body.appendChild(a); a.click();
  setTimeout(function () { a.remove(); }, 1000);
}
function downloadAtt(att) {
  if (!att || !att.embedded || !att.base64) { toast(att && att.skipReason ? att.skipReason : '该附件未内嵌'); return; }
  triggerDownload(getBlobUrl(att), att.name);
  toast('已开始下载 ' + att.name);
}
function downloadText(text, name, mime) {
  var url = URL.createObjectURL(new Blob([text], { type: mime || 'text/plain;charset=utf-8' }));
  triggerDownload(url, name);
  setTimeout(function () { URL.revokeObjectURL(url); }, 60000);
}

var toastTimer = null;
function toast(msg) {
  var t = $('toast'); t.textContent = msg; t.classList.add('show');
  clearTimeout(toastTimer); toastTimer = setTimeout(function () { t.classList.remove('show'); }, 1900);
}
function legacyCopy(text) {
  var ta = document.createElement('textarea');
  ta.value = text; ta.setAttribute('readonly', '');
  ta.style.position = 'fixed'; ta.style.top = '-9999px'; ta.style.opacity = '0';
  document.body.appendChild(ta); ta.focus(); ta.select();
  try { ta.setSelectionRange(0, ta.value.length); } catch (e) {}
  var ok = false;
  try { ok = document.execCommand('copy'); } catch (e) { ok = false; }
  ta.remove();
  return ok;
}
function showManualCopy(text) {
  var area = $('copy-area'); area.value = text; $('copy-modal').classList.add('open');
  setTimeout(function () { area.focus(); area.select(); }, 30);
}
function copyText(text, okMsg) {
  var done = function () { toast(okMsg || '已复制'); };
  var fallback = function () { if (legacyCopy(text)) done(); else showManualCopy(text); };
  try {
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(text).then(done, fallback);
      return;
    }
  } catch (e) {}
  fallback();
}

function renderAttachment(att, ci, ai) {
  var sub = escapeHtml((att.ext ? att.ext.toUpperCase() + ' · ' : '') + humanSize(att.size));
  var ids = ' data-ci="' + ci + '" data-ai="' + ai + '"';
  var info = '<div class="att-info"><div class="att-icon">' + iconFor(att) + '</div><div class="att-text"><div class="att-name" title="' +
    escapeHtml(att.name) + '">' + escapeHtml(att.name) + '</div><div class="att-sub">' + sub + '</div></div></div>';
  var html = '', wide = false;
  if (att.kind === 'image' && att.embedded) {
    html += '<button class="att-thumb" data-action="lightbox"' + ids + ' title="点击查看大图"><img alt="' + escapeHtml(att.name) + '" src="' + getBlobUrl(att) + '"></button>';
  }
  html += info;
  if (!att.embedded && att.skipReason) html += '<div class="att-warn">⚠️ ' + escapeHtml(att.skipReason) + '</div>';
  var acts = [];
  if (att.kind === 'image' && att.embedded) acts.push('<button class="btn-mini" data-action="lightbox"' + ids + '>查看大图</button>');
  if (att.kind === 'pdf' && att.embedded) acts.push('<button class="btn-mini" data-action="open-tab"' + ids + '>新标签页预览</button>');
  if (att.kind === 'text' && att.textPreview != null) acts.push('<button class="btn-mini" data-action="copy-att-text"' + ids + '>复制文本</button>');
  if (att.embedded) acts.push('<button class="btn-mini" data-action="download"' + ids + '>一键下载</button>');
  if (acts.length) html += '<div class="att-actions">' + acts.join('') + '</div>';
  if (att.kind === 'text' && att.textPreview != null) {
    wide = true;
    html += '<details><summary>展开查看内容</summary><pre>' + escapeHtml(att.textPreview) + '</pre></details>';
  }
  return '<div class="att' + (wide ? ' wide' : '') + '">' + html + '</div>';
}

function render() {
  var title = payload.title || 'GrokClip 空间';
  document.title = title;
  $('space-title').textContent = title;
  var attTotal = 0;
  clips.forEach(function (c, ci) {
    (c.attachments || []).forEach(function (a, ai) { a.__key = ci + '_' + ai; attTotal++; });
  });
  $('space-meta').textContent = (payload.createdAtDisplay ? '创建于 ' + payload.createdAtDisplay + ' · ' : '') +
    clips.length + ' 段对话原文 · ' + attTotal + ' 个附件';

  var toc = [], cards = [];
  clips.forEach(function (c, ci) {
    var num = '#' + pad2(c.index || ci + 1);
    var anchor = 'clip-' + pad2(c.index || ci + 1);
    var atts = c.attachments || [];
    var label = previewLine(c.rawContent) || (atts.length ? '📎 ' + atts[0].name : '（空）');
    toc.push('<a href="#' + anchor + '" data-anchor="' + anchor + '"><span class="num">' + num + '</span><span class="txt">' + escapeHtml(label) +
      '</span>' + (atts.length ? '<span class="pill">📎 ' + atts.length + '</span>' : '') + '</a>');
    var raw = c.rawContent || '';
    var body = raw.length
      ? '<div class="md">' + renderMarkdown(raw) + '</div><pre class="raw">' + escapeHtml(raw) + '</pre>'
      : '<div class="empty-note">（仅附件，无文本原文）</div>';
    var attHtml = atts.length ? '<div class="atts">' + atts.map(function (a, ai) { return renderAttachment(a, ci, ai); }).join('') + '</div>' : '';
    cards.push('<article class="card" id="' + anchor + '" data-ci="' + ci + '"><div class="card-head"><span class="badge">' + num +
      '</span><span class="meta">' + escapeHtml(c.timeDisplay || '') + (c.sourceApp ? ' · ' + escapeHtml(c.sourceApp) : '') +
      (raw.length ? ' · ' + raw.length + ' 字' : '') + '</span><span class="card-tools">' +
      (raw.length ? '<button class="btn-mini" data-action="toggle-raw" data-ci="' + ci + '">原文</button><button class="btn-mini" data-action="copy-raw" data-ci="' + ci + '">复制原文</button>' : '') +
      '</span></div><div class="card-body">' + body + attHtml + '</div></article>');
  });
  $('toc').innerHTML = toc.join('') || '<div class="empty-note" style="padding:8px 10px">空</div>';
  $('stream').innerHTML = cards.join('') || '<div class="card"><div class="card-body empty-note">这个空间里还没有内容。</div></div>';
  observeActive();
}

function observeActive() {
  if (!('IntersectionObserver' in window)) return;
  var links = {};
  Array.prototype.forEach.call(document.querySelectorAll('.toc a'), function (a) { links[a.getAttribute('data-anchor')] = a; });
  var io = new IntersectionObserver(function (entries) {
    entries.forEach(function (en) {
      if (en.isIntersecting) {
        Object.keys(links).forEach(function (k) { links[k].classList.remove('active'); });
        if (links[en.target.id]) links[en.target.id].classList.add('active');
      }
    });
  }, { rootMargin: '-90px 0px -65% 0px' });
  Array.prototype.forEach.call(document.querySelectorAll('.card[id]'), function (c) { io.observe(c); });
}

function applyTheme(theme) {
  document.documentElement.setAttribute('data-theme', theme);
  $('theme-btn').textContent = theme === 'dark' ? '浅色' : '深色';
}
function initTheme() {
  var saved = null;
  try { saved = localStorage.getItem('grokclip-theme'); } catch (e) {}
  var prefersDark = false;
  try { prefersDark = window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches; } catch (e) {}
  applyTheme(saved === 'dark' || saved === 'light' ? saved : (prefersDark ? 'dark' : 'light'));
}
function fileSafe(name) { return String(name || 'GrokClip').replace(/[\\\/:*?"<>|\u0000-\u001f]/g, '_').slice(0, 60); }

function openLightbox(att) {
  lbCurrent = att;
  $('lb-img').src = getBlobUrl(att);
  $('lb-img').alt = att.name;
  $('lb-name').textContent = att.name + ' · ' + humanSize(att.size);
  $('lightbox').classList.add('open');
}
function closeLightbox() { $('lightbox').classList.remove('open'); lbCurrent = null; }

document.addEventListener('click', function (ev) {
  var el = ev.target.closest ? ev.target.closest('[data-action]') : null;
  if (!el) { if (ev.target === $('lightbox')) closeLightbox(); if (ev.target === $('copy-modal')) $('copy-modal').classList.remove('open'); return; }
  var action = el.getAttribute('data-action');
  var ci = parseInt(el.getAttribute('data-ci'), 10), ai = parseInt(el.getAttribute('data-ai'), 10);
  var att = isNaN(ai) ? null : attAt(ci, ai);
  switch (action) {
    case 'copy-all': copyText(joinRaw(clips, '\n\n'), '已复制全部纯原文'); break;
    case 'download-md': downloadText(joinRaw(clips, '\n\n---\n\n'), fileSafe(payload.title) + '.md', 'text/markdown;charset=utf-8'); toast('已开始下载 .md'); break;
    case 'toggle-theme':
      var next = document.documentElement.getAttribute('data-theme') === 'dark' ? 'light' : 'dark';
      applyTheme(next); try { localStorage.setItem('grokclip-theme', next); } catch (e) {}
      break;
    case 'toggle-raw':
      var card = el.closest('.card'); var on = card.classList.toggle('show-raw');
      el.textContent = on ? '排版' : '原文'; el.classList.toggle('on', on); break;
    case 'copy-raw': if (clips[ci]) copyText(clips[ci].rawContent || '', '已复制 #' + pad2(clips[ci].index || ci + 1) + ' 原文'); break;
    case 'copy-code':
      var code = el.closest('.code-block').querySelector('code');
      copyText(code ? code.textContent : '', '代码已复制'); break;
    case 'lightbox': if (att) openLightbox(att); break;
    case 'lb-close': closeLightbox(); break;
    case 'lb-download': if (lbCurrent) downloadAtt(lbCurrent); break;
    case 'open-tab':
      if (att) { var w = window.open(getBlobUrl(att), '_blank'); if (!w) { downloadAtt(att); } }
      break;
    case 'copy-att-text': if (att) copyText(att.textPreview || '', '已复制 ' + att.name); break;
    case 'download': downloadAtt(att); break;
    case 'modal-close': $('copy-modal').classList.remove('open'); break;
  }
});
document.addEventListener('keydown', function (ev) {
  if (ev.key === 'Escape') { closeLightbox(); $('copy-modal').classList.remove('open'); }
});

initTheme();
render();
})();
</script>
</body>
</html>
"""#
}
