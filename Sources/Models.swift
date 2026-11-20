import Foundation
import CryptoKit

// MARK: - Paths

/// 所有路径都在运行时动态解析，源码中不含任何个人用户名。
enum AppPaths {
    static var home: URL { FileManager.default.homeDirectoryForCurrentUser }

    /// `~/grok bot`（仅当该目录真实存在时返回）
    static var grokBotDirectory: URL? {
        let url = home.appendingPathComponent("grok bot", isDirectory: true)
        var isDir: ObjCBool = false
        if FileManager.default.fileExists(atPath: url.path, isDirectory: &isDir), isDir.boolValue {
            return url
        }
        return nil
    }

    static var downloadsDirectory: URL {
        FileManager.default.urls(for: .downloadsDirectory, in: .userDomainMask).first
            ?? home.appendingPathComponent("Downloads", isDirectory: true)
    }

    static var desktopDirectory: URL {
        FileManager.default.urls(for: .desktopDirectory, in: .userDomainMask).first
            ?? home.appendingPathComponent("Desktop", isDirectory: true)
    }

    static var supportDirectory: URL {
        let base = FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask).first
            ?? home.appendingPathComponent("Library/Application Support", isDirectory: true)
        let dir = base.appendingPathComponent("GrokClipSpace", isDirectory: true)
        try? FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        return dir
    }

    static var clipsStoreURL: URL { supportDirectory.appendingPathComponent("clips_store.json") }
    static var settingsURL: URL { supportDirectory.appendingPathComponent("settings.json") }
}

// MARK: - Attachment

struct ClipAttachment: Identifiable, Codable, Equatable {
    var id: UUID
    var path: String
    var addedAt: Date

    init(id: UUID = UUID(), path: String, addedAt: Date = Date()) {
        self.id = id
        self.path = path
        self.addedAt = addedAt
    }

    var url: URL { URL(fileURLWithPath: path) }
    var fileName: String { url.lastPathComponent }
    var fileExists: Bool { FileManager.default.fileExists(atPath: path) }

    var fileSize: Int64 {
        guard let attrs = try? FileManager.default.attributesOfItem(atPath: path),
              let size = attrs[.size] as? NSNumber else { return 0 }
        return size.int64Value
    }

    var formattedSize: String { Formatters.byteString(fileSize) }
}

// MARK: - Speaker role

/// 一段原文是谁说的：用户提问（user）还是 Grok Bot 回复（assistant）。
/// 剪贴板本身不带说话人信息，所以收录时用 `detect` 做保守猜测，用户可在抽屉里一键切换。
enum ClipRole: String, Codable, CaseIterable {
    case user
    case assistant

    var toggled: ClipRole { self == .user ? .assistant : .user }

    /// 保守启发式：拿不准一律判为 assistant。
    /// - 含代码块 / 标题 / 列表 / 表格 / 引用等 Markdown 结构 → assistant
    /// - 超过 200 字或超过 3 个非空行 → assistant
    /// - 剩下的短文本：以 ？/? 结尾、以 吗/呢/么 结尾，或以常见请求句式开头 → user
    /// - 其余 → assistant
    static func detect(_ text: String) -> ClipRole {
        let trimmed = text.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty else { return .assistant }
        if trimmed.contains("```") || trimmed.contains("~~~") { return .assistant }
        let lines = trimmed.components(separatedBy: .newlines)
            .map { $0.trimmingCharacters(in: .whitespaces) }
            .filter { !$0.isEmpty }
        for line in lines where isMarkdownStructure(line) { return .assistant }
        if trimmed.count > 200 || lines.count > 3 { return .assistant }

        let lower = trimmed.lowercased()
        if let last = trimmed.last, "?？".contains(last) { return .user }
        let stripped = trimmed.trimmingCharacters(in: CharacterSet(charactersIn: "。.!！~～… "))
        if let last = stripped.last, "吗呢么嘛".contains(last) { return .user }
        if userPrefixes.contains(where: { lower.hasPrefix($0) }) { return .user }
        return .assistant
    }

    static let userPrefixes: [String] = [
        "帮我", "帮忙", "请你", "请帮", "请问", "麻烦", "能不能", "能否", "可不可以", "可以帮", "你能", "你可以",
        "你帮", "给我", "我想", "我要", "我需要", "我希望", "怎么", "如何", "为什么", "为啥", "是不是", "有没有",
        "什么", "哪个", "哪些", "哪里", "多少",
        "please", "can you", "could you", "would you", "help me", "how ", "what ", "why ", "where ", "when ",
        "which ", "who ", "is it", "are there", "do you",
    ]

    private static func isMarkdownStructure(_ line: String) -> Bool {
        let patterns = [
            #"^#{1,6}\s"#,            // 标题
            #"^[-*+]\s+\S"#,          // 无序列表
            #"^\d{1,3}[.)]\s+\S"#,    // 有序列表
            #"^\|.*\|"#,              // 表格
            #"^>\s?"#,                // 引用
            #"^(-{3,}|\*{3,}|_{3,})$"#, // 分隔线
        ]
        return patterns.contains { line.range(of: $0, options: .regularExpression) != nil }
    }
}

/// 空间里显示的说话人名称（只保存在本机 settings.json）。
enum SpeakerNames {
    static let defaultUser = "我"
    static let defaultAssistant = "Grok Bot"

    static func clean(_ name: String, fallback: String) -> String {
        let filtered = String(String.UnicodeScalarView(name.unicodeScalars.filter {
            !CharacterSet.controlCharacters.contains($0)
        })).trimmingCharacters(in: .whitespacesAndNewlines)
        return filtered.isEmpty ? fallback : String(filtered.prefix(24))
    }
}

// MARK: - Clip

struct ClipItem: Identifiable, Codable, Equatable {
    var id: UUID
    var timestamp: Date
    var sourceApp: String
    var rawContent: String
    var contentHash: String
    var attachments: [ClipAttachment]
    var isSelected: Bool = true
    var role: ClipRole = .assistant

    init(id: UUID = UUID(),
         timestamp: Date = Date(),
         sourceApp: String,
         rawContent: String,
         attachments: [ClipAttachment] = [],
         isSelected: Bool = true,
         role: ClipRole = .assistant) {
        self.id = id
        self.timestamp = timestamp
        self.sourceApp = sourceApp
        self.rawContent = rawContent
        self.contentHash = ClipItem.sha256(rawContent)
        self.attachments = attachments
        self.isSelected = isSelected
        self.role = role
    }

    private enum CodingKeys: String, CodingKey {
        case id, timestamp, sourceApp, rawContent, contentHash, attachments, isSelected, role
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        id = try c.decodeIfPresent(UUID.self, forKey: .id) ?? UUID()
        timestamp = try c.decodeIfPresent(Date.self, forKey: .timestamp) ?? Date()
        sourceApp = try c.decodeIfPresent(String.self, forKey: .sourceApp) ?? "未知来源"
        rawContent = try c.decodeIfPresent(String.self, forKey: .rawContent) ?? ""
        contentHash = try c.decodeIfPresent(String.self, forKey: .contentHash) ?? ClipItem.sha256(rawContent)
        attachments = try c.decodeIfPresent([ClipAttachment].self, forKey: .attachments) ?? []
        isSelected = try c.decodeIfPresent(Bool.self, forKey: .isSelected) ?? true
        // 旧版 clips_store.json 没有 role 字段（或值无法识别）时，一律视为 Grok Bot 回复
        role = (try? c.decodeIfPresent(ClipRole.self, forKey: .role)) ?? .assistant
    }

    var isFileOnly: Bool { rawContent.isEmpty && !attachments.isEmpty }

    /// 用于列表预览：首个非空行
    var firstLine: String {
        for line in rawContent.split(separator: "\n", omittingEmptySubsequences: true) {
            let t = line.trimmingCharacters(in: .whitespaces)
            if !t.isEmpty { return t }
        }
        return ""
    }

    static func sha256(_ string: String) -> String {
        let digest = SHA256.hash(data: Data(string.utf8))
        return digest.map { String(format: "%02x", $0) }.joined()
    }
}

/// 新落地但尚未挂载的文件（双向 120 秒关联的缓冲池）
struct PendingFile: Identifiable, Equatable {
    let id = UUID()
    let path: String
    let detectedAt: Date
    var url: URL { URL(fileURLWithPath: path) }
    var fileName: String { url.lastPathComponent }
}

// MARK: - Settings

enum CaptureScope: String, Codable, CaseIterable, Identifiable {
    case grokOnly
    case grokAndBrowsers
    case allApps

    var id: String { rawValue }

    var displayName: String {
        switch self {
        case .grokOnly: return "仅 Grok Bot"
        case .grokAndBrowsers: return "Grok + 浏览器"
        case .allApps: return "全部应用"
        }
    }
}

struct AppSettings: Codable, Equatable {
    var captureScope: CaptureScope = .grokOnly
    var watchedDirectories: [String]
    var exportDirectory: String
    var isListening: Bool = true
    var autoAttachWindowSeconds: Double = 120
    var userDisplayName: String = SpeakerNames.defaultUser
    var assistantDisplayName: String = SpeakerNames.defaultAssistant

    static func defaultWatchedDirectories() -> [String] {
        var dirs = [AppPaths.downloadsDirectory.path]
        if let grok = AppPaths.grokBotDirectory { dirs.append(grok.path) }
        return dirs
    }

    static func defaultExportDirectory() -> String {
        if let grok = AppPaths.grokBotDirectory { return grok.path }
        return AppPaths.desktopDirectory.appendingPathComponent("GrokSpaces", isDirectory: true).path
    }

    static func makeDefault() -> AppSettings {
        AppSettings(captureScope: .grokOnly,
                    watchedDirectories: defaultWatchedDirectories(),
                    exportDirectory: defaultExportDirectory(),
                    isListening: true,
                    autoAttachWindowSeconds: 120)
    }

    init(captureScope: CaptureScope, watchedDirectories: [String], exportDirectory: String,
         isListening: Bool, autoAttachWindowSeconds: Double,
         userDisplayName: String = SpeakerNames.defaultUser,
         assistantDisplayName: String = SpeakerNames.defaultAssistant) {
        self.captureScope = captureScope
        self.watchedDirectories = watchedDirectories
        self.exportDirectory = exportDirectory
        self.isListening = isListening
        self.autoAttachWindowSeconds = autoAttachWindowSeconds
        self.userDisplayName = userDisplayName
        self.assistantDisplayName = assistantDisplayName
    }

    private enum CodingKeys: String, CodingKey {
        case captureScope, watchedDirectories, exportDirectory, isListening, autoAttachWindowSeconds
        case userDisplayName, assistantDisplayName
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        captureScope = (try? c.decodeIfPresent(CaptureScope.self, forKey: .captureScope)) ?? .grokOnly
        watchedDirectories = (try? c.decodeIfPresent([String].self, forKey: .watchedDirectories))
            ?? AppSettings.defaultWatchedDirectories()
        exportDirectory = (try? c.decodeIfPresent(String.self, forKey: .exportDirectory))
            ?? AppSettings.defaultExportDirectory()
        isListening = (try? c.decodeIfPresent(Bool.self, forKey: .isListening)) ?? true
        let window = (try? c.decodeIfPresent(Double.self, forKey: .autoAttachWindowSeconds)) ?? 120
        autoAttachWindowSeconds = min(max(window, 10), 3600)
        userDisplayName = SpeakerNames.clean((try? c.decodeIfPresent(String.self, forKey: .userDisplayName)) ?? "",
                                             fallback: SpeakerNames.defaultUser)
        assistantDisplayName = SpeakerNames.clean((try? c.decodeIfPresent(String.self, forKey: .assistantDisplayName)) ?? "",
                                                  fallback: SpeakerNames.defaultAssistant)
    }
}

// MARK: - Persistence & formatting helpers

enum Persistence {
    static func makeEncoder() -> JSONEncoder {
        let e = JSONEncoder()
        e.outputFormatting = [.prettyPrinted, .sortedKeys]
        e.dateEncodingStrategy = .iso8601
        return e
    }

    static func makeDecoder() -> JSONDecoder {
        let d = JSONDecoder()
        d.dateDecodingStrategy = .iso8601
        return d
    }

    /// 原子写入，避免外部脚本 / AI Skill 读到写了一半的 JSON。
    static func save<T: Encodable>(_ value: T, to url: URL) {
        do {
            let data = try makeEncoder().encode(value)
            try data.write(to: url, options: .atomic)
        } catch {
            NSLog("[GrokClip] 保存失败 \(url.lastPathComponent): \(error.localizedDescription)")
        }
    }

    static func load<T: Decodable>(_ type: T.Type, from url: URL) -> T? {
        guard let data = try? Data(contentsOf: url) else { return nil }
        do {
            return try makeDecoder().decode(type, from: data)
        } catch {
            NSLog("[GrokClip] 读取失败 \(url.lastPathComponent): \(error.localizedDescription)")
            return nil
        }
    }
}

enum Formatters {
    static let time: DateFormatter = {
        let f = DateFormatter()
        f.locale = Locale(identifier: "zh_CN")
        f.dateFormat = "HH:mm:ss"
        return f
    }()

    static let dateTime: DateFormatter = {
        let f = DateFormatter()
        f.locale = Locale(identifier: "zh_CN")
        f.dateFormat = "yyyy-MM-dd HH:mm:ss"
        return f
    }()

    static let fileStamp: DateFormatter = {
        let f = DateFormatter()
        f.locale = Locale(identifier: "en_US_POSIX")
        f.dateFormat = "yyyyMMdd_HHmmss"
        return f
    }()

    static func byteString(_ bytes: Int64) -> String {
        let f = ByteCountFormatter()
        f.countStyle = .file
        f.allowedUnits = [.useBytes, .useKB, .useMB, .useGB]
        return f.string(fromByteCount: bytes)
    }
}
