import Foundation

/// 线程安全的多目录文件落地监听。
/// 所有可变状态只在专用串行队列 `queue` 上读写，回调统一派发到主线程。
final class DownloadWatcher {
    /// 新文件写入完成（大小 > 0 且稳定）时在主线程回调
    var onNewFileLanded: ((URL) -> Void)?

    private let queue = DispatchQueue(label: "local.grokclip.filewatcher", qos: .utility)

    // ---- 以下状态仅在 queue 上访问 ----
    private var sources: [String: DispatchSourceFileSystemObject] = [:]
    private var fileDescriptors: [String: Int32] = [:]
    private var knownFiles: [String: Set<String>] = [:]
    private var pendingSizeChecks: [String: (size: Int64, attempts: Int)] = [:]
    private var ignoredPaths: Set<String> = []

    static let stabilityDelay: TimeInterval = 0.55
    static let maxStabilityAttempts = 90 // ≈ 50 秒仍在增长则放弃

    static let ignoredExtensions: Set<String> = [
        "crdownload", "download", "tmp", "part", "opdownload", "ds_store", "partial",
    ]

    deinit {
        for (_, src) in sources { src.cancel() }
    }

    // MARK: Public API (thread-safe)

    func reloadDirectories(_ paths: [String]) {
        queue.async { [weak self] in self?.applyDirectories(paths) }
    }

    func stopAll() {
        queue.async { [weak self] in self?.applyDirectories([]) }
    }

    /// 忽略某个路径（例如我们自己导出的空间文件）
    func ignore(path: String) {
        let p = (path as NSString).standardizingPath
        queue.async { [weak self] in self?.ignoredPaths.insert(p) }
    }

    // MARK: Filtering

    static func shouldIgnore(fileName name: String) -> Bool {
        if name.isEmpty || name.hasPrefix(".") || name.hasPrefix("~$") { return true }
        let ext = (name as NSString).pathExtension.lowercased()
        if ignoredExtensions.contains(ext) { return true }
        if name.hasPrefix("GrokSpace_") && ext == "html" { return true }
        return false
    }

    // MARK: Queue-confined implementation

    private func applyDirectories(_ paths: [String]) {
        let wanted = Set(paths.map { ($0 as NSString).expandingTildeInPath }
                              .map { ($0 as NSString).standardizingPath })

        for (path, source) in sources where !wanted.contains(path) {
            source.cancel()
            sources[path] = nil
            fileDescriptors[path] = nil
            knownFiles[path] = nil
        }

        for path in wanted where sources[path] == nil {
            var isDir: ObjCBool = false
            guard FileManager.default.fileExists(atPath: path, isDirectory: &isDir), isDir.boolValue else { continue }
            let fd = open(path, O_EVTONLY)
            guard fd >= 0 else {
                NSLog("[GrokClip] 无法监听目录（权限不足？）：\(path)")
                continue
            }
            knownFiles[path] = Set(listFileNames(in: path))
            let source = DispatchSource.makeFileSystemObjectSource(
                fileDescriptor: fd,
                eventMask: [.write, .extend, .attrib, .rename, .delete, .link],
                queue: queue)
            source.setEventHandler { [weak self] in
                guard let self = self, let src = self.sources[path] else { return }
                let flags = src.data
                if flags.contains(.delete) || flags.contains(.rename) {
                    // 目录本身被删除/改名：停止监听，避免持有失效描述符
                    if !FileManager.default.fileExists(atPath: path) {
                        src.cancel()
                        self.sources[path] = nil
                        self.fileDescriptors[path] = nil
                        self.knownFiles[path] = nil
                        return
                    }
                }
                self.scan(directory: path)
            }
            source.setCancelHandler { close(fd) }
            sources[path] = source
            fileDescriptors[path] = fd
            source.resume()
        }
    }

    private func listFileNames(in directory: String) -> [String] {
        (try? FileManager.default.contentsOfDirectory(atPath: directory)) ?? []
    }

    private func scan(directory: String) {
        let current = Set(listFileNames(in: directory))
        let previous = knownFiles[directory] ?? []
        knownFiles[directory] = current
        let added = current.subtracting(previous)
        for name in added where !Self.shouldIgnore(fileName: name) {
            let full = (directory as NSString).appendingPathComponent(name)
            if ignoredPaths.contains(full) { continue }
            if pendingSizeChecks[full] != nil { continue }
            pendingSizeChecks[full] = (size: -1, attempts: 0)
            checkStability(path: full)
        }
    }

    private func checkStability(path: String) {
        guard var state = pendingSizeChecks[path] else { return }

        var isDir: ObjCBool = false
        guard FileManager.default.fileExists(atPath: path, isDirectory: &isDir), !isDir.boolValue else {
            pendingSizeChecks[path] = nil // 已消失或是文件夹：放弃
            return
        }
        let size = ((try? FileManager.default.attributesOfItem(atPath: path))?[.size] as? NSNumber)?.int64Value ?? 0

        if size > 0 && size == state.size {
            pendingSizeChecks[path] = nil
            if ignoredPaths.contains(path) { return }
            let url = URL(fileURLWithPath: path)
            DispatchQueue.main.async { [weak self] in self?.onNewFileLanded?(url) }
            return
        }

        state.size = size
        state.attempts += 1
        if state.attempts > Self.maxStabilityAttempts {
            pendingSizeChecks[path] = nil
            return
        }
        pendingSizeChecks[path] = state
        queue.asyncAfter(deadline: .now() + Self.stabilityDelay) { [weak self] in
            self?.checkStability(path: path)
        }
    }
}
