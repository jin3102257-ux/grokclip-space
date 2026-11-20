import AppKit
import Combine
import UniformTypeIdentifiers

/// 收集篮事件（用于驱动悬浮球动效与 Toast 提示）
enum StoreEvent {
    case captured(index: Int, autoAttached: Int)
    case attached(fileName: String, count: Int)
    case pendingFile(fileName: String)
    case spaceCopied(SpaceExportResult)
    case rawCopied(count: Int)
    case info(String)
    case error(String)
}

/// 核心状态容器。所有方法都应在主线程调用。
final class ClipStore: ObservableObject {
    /// 严格按收集先后排列：#01, #02, #03…（与导出空间中的顺序一致）
    @Published private(set) var items: [ClipItem] = []
    @Published private(set) var settings: AppSettings
    @Published private(set) var recentPendingFiles: [PendingFile] = []
    @Published var spaceTitle: String = ""
    @Published private(set) var lastExport: SpaceExportResult?
    @Published private(set) var isExporting: Bool = false

    var onEvent: ((StoreEvent) -> Void)?

    let clipboard = ClipboardMonitor()
    let watcher = DownloadWatcher()

    private var exportedPaths: Set<String> = []

    init() {
        settings = Persistence.load(AppSettings.self, from: AppPaths.settingsURL) ?? AppSettings.makeDefault()
        items = Persistence.load([ClipItem].self, from: AppPaths.clipsStoreURL) ?? []
    }

    // MARK: Lifecycle

    func start() {
        clipboard.onNewText = { [weak self] text, source in
            self?.addClip(text: text, sourceApp: source)
        }
        watcher.onNewFileLanded = { [weak self] url in
            self?.handleLandedFile(url)
        }
        applySettings(reloadWatcher: true)
        clipboard.start()
    }

    func shutdown() {
        clipboard.stop()
        watcher.stopAll()
        saveItems()
        saveSettings()
    }

    // MARK: Derived

    var selectedItems: [ClipItem] { items.filter { $0.isSelected } }
    var selectedCount: Int { items.reduce(0) { $0 + ($1.isSelected ? 1 : 0) } }
    var allSelected: Bool { !items.isEmpty && items.allSatisfy { $0.isSelected } }

    func index(of id: UUID) -> Int? { items.firstIndex { $0.id == id } }

    /// 最近“新增”的条目（按收录时间，而非列表位置）
    private var latestItemIndex: Int? {
        guard !items.isEmpty else { return nil }
        var best = 0
        for i in items.indices where items[i].timestamp >= items[best].timestamp { best = i }
        return best
    }

    // MARK: Capture

    func addClip(text: String, sourceApp: String) {
        let hash = ClipItem.sha256(text)
        if let li = latestItemIndex, items[li].contentHash == hash {
            return // 与最近一条完全相同：去重
        }
        var item = ClipItem(sourceApp: sourceApp, rawContent: text)
        // 反向认领：文本到来时，把最近 N 秒内落地的文件挂到这条原文下
        prunePending()
        let window = settings.autoAttachWindowSeconds
        let now = Date()
        let claim = recentPendingFiles.filter { abs(now.timeIntervalSince($0.detectedAt)) <= window }
        for file in claim where FileManager.default.fileExists(atPath: file.path) {
            item.attachments.append(ClipAttachment(path: file.path))
        }
        let claimedIDs = Set(claim.map { $0.id })
        recentPendingFiles.removeAll { claimedIDs.contains($0.id) }

        items.append(item)
        saveItems()
        onEvent?(.captured(index: items.count, autoAttached: item.attachments.count))
    }

    func captureCurrentPasteboardManually() {
        switch clipboard.captureCurrentPasteboardManually() {
        case .text(let text, let source):
            let before = items.count
            addClip(text: text, sourceApp: source)
            if items.count == before { onEvent?(.info("这段内容已在收集篮中")) }
        case .files(let urls, _):
            attachFilesToLatestOrCreate(urls, sourceApp: "手动收录")
        case .sensitive:
            onEvent?(.error("已跳过：剪贴板内容被标记为敏感（密码等）"))
        case .empty:
            onEvent?(.info("剪贴板里没有可收录的文本或文件"))
        }
    }

    // MARK: Files

    private func isAlreadyAttached(_ path: String) -> Bool {
        items.contains { $0.attachments.contains { $0.path == path } }
    }

    private func prunePending() {
        let window = max(settings.autoAttachWindowSeconds, 120)
        let now = Date()
        recentPendingFiles.removeAll {
            now.timeIntervalSince($0.detectedAt) > window || !FileManager.default.fileExists(atPath: $0.path)
        }
    }

    /// 正向关联：文件落地时，若最近一条原文在窗口内则直接挂载，否则进入缓冲池等待下一条原文认领。
    func handleLandedFile(_ url: URL) {
        let path = url.standardizedFileURL.path
        if exportedPaths.contains(path) || isAlreadyAttached(path) { return }
        if recentPendingFiles.contains(where: { $0.path == path }) { return }
        prunePending()
        if let li = latestItemIndex,
           abs(Date().timeIntervalSince(items[li].timestamp)) <= settings.autoAttachWindowSeconds {
            items[li].attachments.append(ClipAttachment(path: path))
            saveItems()
            onEvent?(.attached(fileName: url.lastPathComponent, count: 1))
        } else {
            recentPendingFiles.append(PendingFile(path: path, detectedAt: Date()))
            onEvent?(.pendingFile(fileName: url.lastPathComponent))
        }
    }

    func attachFiles(_ urls: [URL], to id: UUID) {
        guard let idx = index(of: id) else { return }
        var added: [String] = []
        for url in urls {
            let path = url.standardizedFileURL.path
            var isDir: ObjCBool = false
            guard FileManager.default.fileExists(atPath: path, isDirectory: &isDir), !isDir.boolValue else { continue }
            if items[idx].attachments.contains(where: { $0.path == path }) { continue }
            items[idx].attachments.append(ClipAttachment(path: path))
            recentPendingFiles.removeAll { $0.path == path }
            added.append(url.lastPathComponent)
        }
        guard !added.isEmpty else {
            onEvent?(.info("没有可挂载的新文件（文件夹不支持）"))
            return
        }
        saveItems()
        onEvent?(.attached(fileName: added[0], count: added.count))
    }

    /// 拖到悬浮球：挂到最近一条；收集篮为空时新建一个“仅附件”条目。
    func attachFilesToLatestOrCreate(_ urls: [URL], sourceApp: String = "拖拽文件") {
        if let li = latestItemIndex {
            attachFiles(urls, to: items[li].id)
            return
        }
        let files = urls.filter {
            var isDir: ObjCBool = false
            return FileManager.default.fileExists(atPath: $0.path, isDirectory: &isDir) && !isDir.boolValue
        }
        guard !files.isEmpty else {
            onEvent?(.info("没有可挂载的文件（文件夹不支持）"))
            return
        }
        let atts = files.map { ClipAttachment(path: $0.standardizedFileURL.path) }
        items.append(ClipItem(sourceApp: sourceApp, rawContent: "", attachments: atts))
        saveItems()
        onEvent?(.attached(fileName: files[0].lastPathComponent, count: files.count))
    }

    func attachPendingToLatest(_ file: PendingFile) {
        guard let li = latestItemIndex else {
            attachFilesToLatestOrCreate([file.url])
            recentPendingFiles.removeAll { $0.id == file.id }
            return
        }
        attachFiles([file.url], to: items[li].id)
        recentPendingFiles.removeAll { $0.id == file.id }
    }

    func dismissPending(_ file: PendingFile) {
        recentPendingFiles.removeAll { $0.id == file.id }
    }

    func dismissAllPending() { recentPendingFiles.removeAll() }

    func removeAttachment(clipID: UUID, attachmentID: UUID) {
        guard let idx = index(of: clipID) else { return }
        items[idx].attachments.removeAll { $0.id == attachmentID }
        if items[idx].rawContent.isEmpty && items[idx].attachments.isEmpty {
            items.remove(at: idx)
        }
        saveItems()
    }

    func pickFiles(for id: UUID) {
        let panel = NSOpenPanel()
        panel.title = "选择要挂载的文件"
        panel.prompt = "挂载"
        panel.canChooseFiles = true
        panel.canChooseDirectories = false
        panel.allowsMultipleSelection = true
        NSApp.activate(ignoringOtherApps: true)
        if panel.runModal() == .OK { attachFiles(panel.urls, to: id) }
    }

    // MARK: Editing

    func setSelected(_ id: UUID, _ value: Bool) {
        guard let idx = index(of: id) else { return }
        items[idx].isSelected = value
        saveItems()
    }

    func setAllSelected(_ value: Bool) {
        for i in items.indices { items[i].isSelected = value }
        saveItems()
    }

    func moveUp(_ id: UUID) {
        guard let idx = index(of: id), idx > 0 else { return }
        items.swapAt(idx, idx - 1)
        saveItems()
    }

    func moveDown(_ id: UUID) {
        guard let idx = index(of: id), idx < items.count - 1 else { return }
        items.swapAt(idx, idx + 1)
        saveItems()
    }

    func delete(_ id: UUID) {
        items.removeAll { $0.id == id }
        clipboard.resetDedup()
        saveItems()
    }

    func clearAll() {
        items.removeAll()
        recentPendingFiles.removeAll()
        lastExport = nil
        clipboard.resetDedup()
        saveItems()
    }

    // MARK: Output

    func joinedRawText(of clips: [ClipItem], separator: String = "\n\n") -> String {
        clips.map { $0.rawContent }.filter { !$0.isEmpty }.joined(separator: separator)
    }

    func copySelectedRawText() {
        let selected = selectedItems.filter { !$0.rawContent.isEmpty }
        guard !selected.isEmpty else {
            onEvent?(.info("没有勾选包含文本的条目"))
            return
        }
        clipboard.writeSilentlyToPasteboard(text: joinedRawText(of: selected))
        onEvent?(.rawCopied(count: selected.count))
    }

    /// 生成离线 HTML 空间 → 写入导出目录 → 以文件形式写入剪贴板（微信 / 飞书直接 ⌘V）。
    func generateSpaceAndCopy() {
        let selected = selectedItems
        guard !selected.isEmpty else {
            onEvent?(.info("请先勾选要放进空间的条目"))
            return
        }
        guard !isExporting else { return }
        isExporting = true
        let title = spaceTitle
        let dir = URL(fileURLWithPath: settings.exportDirectory, isDirectory: true)
        DispatchQueue.global(qos: .userInitiated).async { [weak self] in
            let result = Result { try SpaceHTMLGenerator.writeSpace(clips: selected, title: title, exportDirectory: dir) }
            DispatchQueue.main.async {
                guard let self = self else { return }
                self.isExporting = false
                switch result {
                case .success(let export):
                    let path = export.url.standardizedFileURL.path
                    self.exportedPaths.insert(path)
                    self.watcher.ignore(path: path)
                    self.clipboard.writeSilentlyToPasteboard(fileURLs: [export.url])
                    self.lastExport = export
                    self.onEvent?(.spaceCopied(export))
                case .failure(let error):
                    self.onEvent?(.error("生成失败：\(error.localizedDescription)"))
                }
            }
        }
    }

    func dismissLastExport() { lastExport = nil }

    func openLastExportInBrowser() {
        guard let url = lastExport?.url else { return }
        NSWorkspace.shared.open(url)
    }

    func revealLastExportInFinder() {
        guard let url = lastExport?.url else { return }
        NSWorkspace.shared.activateFileViewerSelecting([url])
    }

    func shareLastExport(from view: NSView) {
        guard let url = lastExport?.url else { return }
        let picker = NSSharingServicePicker(items: [url])
        picker.show(relativeTo: view.bounds, of: view, preferredEdge: .minY)
    }

    func openExportDirectory() {
        let url = URL(fileURLWithPath: settings.exportDirectory, isDirectory: true)
        try? FileManager.default.createDirectory(at: url, withIntermediateDirectories: true)
        NSWorkspace.shared.open(url)
    }

    // MARK: Settings

    func setCaptureScope(_ scope: CaptureScope) {
        guard settings.captureScope != scope else { return }
        settings.captureScope = scope
        applySettings(reloadWatcher: false)
    }

    func setListening(_ on: Bool) {
        guard settings.isListening != on else { return }
        settings.isListening = on
        applySettings(reloadWatcher: true)
    }

    func toggleListening() { setListening(!settings.isListening) }

    func setAutoAttachWindow(_ seconds: Double) {
        settings.autoAttachWindowSeconds = min(max(seconds, 10), 3600)
        applySettings(reloadWatcher: false)
    }

    func addWatchedDirectory() {
        guard let url = chooseDirectory(title: "选择要监听的文件目录") else { return }
        let path = url.standardizedFileURL.path
        guard !settings.watchedDirectories.contains(path) else { return }
        settings.watchedDirectories.append(path)
        applySettings(reloadWatcher: true)
    }

    func removeWatchedDirectory(_ path: String) {
        settings.watchedDirectories.removeAll { $0 == path }
        applySettings(reloadWatcher: true)
    }

    func chooseExportDirectory() {
        guard let url = chooseDirectory(title: "选择空间文件的导出目录") else { return }
        settings.exportDirectory = url.standardizedFileURL.path
        applySettings(reloadWatcher: false)
    }

    func resetDirectoriesToDefault() {
        settings.watchedDirectories = AppSettings.defaultWatchedDirectories()
        settings.exportDirectory = AppSettings.defaultExportDirectory()
        applySettings(reloadWatcher: true)
    }

    private func chooseDirectory(title: String) -> URL? {
        let panel = NSOpenPanel()
        panel.title = title
        panel.prompt = "选择"
        panel.canChooseFiles = false
        panel.canChooseDirectories = true
        panel.canCreateDirectories = true
        panel.allowsMultipleSelection = false
        NSApp.activate(ignoringOtherApps: true)
        return panel.runModal() == .OK ? panel.url : nil
    }

    private func applySettings(reloadWatcher: Bool) {
        clipboard.captureScope = settings.captureScope
        clipboard.isListening = settings.isListening
        if reloadWatcher {
            watcher.reloadDirectories(settings.isListening ? settings.watchedDirectories : [])
        }
        saveSettings()
    }

    // MARK: Persistence

    func saveItems() { Persistence.save(items, to: AppPaths.clipsStoreURL) }
    func saveSettings() { Persistence.save(settings, to: AppPaths.settingsURL) }
}
