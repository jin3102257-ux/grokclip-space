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

// MARK: - Clip

struct ClipItem: Identifiable, Codable, Equatable {
    var id: UUID
    var timestamp: Date
    var sourceApp: String
    var rawContent: String
    var contentHash: String
    var attachments: [ClipAttachment]
    var isSelected: Bool = true

    init(id: UUID = UUID(),
         timestamp: Date = Date(),
         sourceApp: String,
         rawContent: String,
         attachments: [ClipAttachment] = [],
         isSelected: Bool = true) {
        self.id = id
        self.timestamp = timestamp
        self.sourceApp = sourceApp
        self.rawContent = rawContent
        self.contentHash = ClipItem.sha256(rawContent)
        self.attachments = attachments
        self.isSelected = isSelected
    }

    private enum CodingKeys: String, CodingKey {
        case id, timestamp, sourceApp, rawContent, contentHash, attachments, isSelected
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
         isListening: Bool, autoAttachWindowSeconds: Double) {
        self.captureScope = captureScope
        self.watchedDirectories = watchedDirectories
        self.exportDirectory = exportDirectory
        self.isListening = isListening
        self.autoAttachWindowSeconds = autoAttachWindowSeconds
    }

    private enum CodingKeys: String, CodingKey {
        case captureScope, watchedDirectories, exportDirectory, isListening, autoAttachWindowSeconds
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
