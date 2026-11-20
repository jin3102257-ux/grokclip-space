import AppKit
import SwiftUI
import Combine
import ServiceManagement

final class AppDelegate: NSObject, NSApplicationDelegate, NSMenuDelegate {
    let store = ClipStore()

    private var orbPanel: NSPanel!
    private var orbView: OrbHostView!
    private var drawerPanel: KeyablePanel!
    private var toastPanel: NSPanel!
    private var toastHost: NSHostingView<ToastView>!
    private let toastModel = ToastModel()
    private var statusItem: NSStatusItem!
    private var cancellables = Set<AnyCancellable>()
    private var toastGeneration = 0

    private let orbSize = NSSize(width: 72, height: 72)
    private let drawerSize = NSSize(width: 376, height: 540)

    private enum DefaultsKey {
        static let orbX = "orbOriginX"
        static let orbY = "orbOriginY"
        static let orbHidden = "orbHidden"
    }

    private var isOrbVisible: Bool {
        get { !UserDefaults.standard.bool(forKey: DefaultsKey.orbHidden) }
        set { UserDefaults.standard.set(!newValue, forKey: DefaultsKey.orbHidden) }
    }

    // MARK: Lifecycle

    func applicationDidFinishLaunching(_ notification: Notification) {
        NSApp.setActivationPolicy(.accessory)
        setupOrbPanel()
        setupDrawerPanel()
        setupToastPanel()
        setupStatusItem()
        bindStore()
        store.start()

        NotificationCenter.default.addObserver(self, selector: #selector(screensChanged),
                                               name: NSApplication.didChangeScreenParametersNotification, object: nil)
        if isOrbVisible { orbPanel.orderFrontRegardless() }
        if store.items.isEmpty {
            showToast("GrokClip 已就绪 · 在 Grok Bot 点「复制」即可收录")
        }
    }

    func applicationWillTerminate(_ notification: Notification) {
        store.shutdown()
    }

    func applicationShouldHandleReopen(_ sender: NSApplication, hasVisibleWindows flag: Bool) -> Bool {
        setOrbVisible(true)
        showDrawer()
        return false
    }

    // MARK: Orb

    private func setupOrbPanel() {
        let panel = NSPanel(contentRect: NSRect(origin: initialOrbOrigin(), size: orbSize),
                            styleMask: [.borderless, .nonactivatingPanel],
                            backing: .buffered, defer: false)
        PanelFactory.configure(panel)
        let view = OrbHostView(frame: NSRect(origin: .zero, size: orbSize))
        view.onClick = { [weak self] in self?.toggleDrawer() }
        view.onMoved = { [weak self] in self?.orbDidMove() }
        view.onDragEnded = { [weak self] in self?.persistOrbPosition() }
        view.onRightClick = { [weak self] event in self?.showContextMenu(event: event) }
        view.onFilesDropped = { [weak self] urls in self?.store.attachFilesToLatestOrCreate(urls) }
        panel.contentView = view
        orbPanel = panel
        orbView = view
    }

    private func initialOrbOrigin() -> NSPoint {
        let defaults = UserDefaults.standard
        let vf = (NSScreen.main ?? NSScreen.screens.first)?.visibleFrame ?? NSRect(x: 0, y: 0, width: 1440, height: 900)
        if defaults.object(forKey: DefaultsKey.orbX) != nil {
            let saved = NSPoint(x: defaults.double(forKey: DefaultsKey.orbX), y: defaults.double(forKey: DefaultsKey.orbY))
            let rect = NSRect(origin: saved, size: orbSize)
            if NSScreen.screens.contains(where: { $0.visibleFrame.intersects(rect) }) { return saved }
        }
        return NSPoint(x: vf.maxX - orbSize.width - 24, y: vf.minY + vf.height * 0.32)
    }

    private func orbDidMove() {
        if drawerPanel.isVisible { positionDrawer() }
        if toastPanel.isVisible { positionToast() }
    }

    private func persistOrbPosition() {
        let o = orbPanel.frame.origin
        UserDefaults.standard.set(Double(o.x), forKey: DefaultsKey.orbX)
        UserDefaults.standard.set(Double(o.y), forKey: DefaultsKey.orbY)
    }

    @objc private func screensChanged() {
        // 显示器变化后把悬浮球拉回可见区域
        let frame = orbPanel.frame
        let screen = screenFor(frame)
        let vf = screen.visibleFrame
        var o = frame.origin
        o.x = min(max(o.x, vf.minX), vf.maxX - frame.width)
        o.y = min(max(o.y, vf.minY), vf.maxY - frame.height)
        orbPanel.setFrameOrigin(o)
        persistOrbPosition()
        orbDidMove()
    }

    private func screenFor(_ rect: NSRect) -> NSScreen {
        let center = NSPoint(x: rect.midX, y: rect.midY)
        return NSScreen.screens.first(where: { $0.frame.contains(center) })
            ?? NSScreen.screens.max(by: { $0.frame.intersection(rect).width * $0.frame.intersection(rect).height
                                          < $1.frame.intersection(rect).width * $1.frame.intersection(rect).height })
            ?? NSScreen.main
            ?? NSScreen.screens[0]
    }

    private func setOrbVisible(_ visible: Bool) {
        isOrbVisible = visible
        if visible {
            orbPanel.orderFrontRegardless()
        } else {
            orbPanel.orderOut(nil)
        }
    }

    // MARK: Drawer

    private func setupDrawerPanel() {
        let panel = KeyablePanel(contentRect: NSRect(origin: .zero, size: drawerSize),
                                 styleMask: [.borderless, .nonactivatingPanel],
                                 backing: .buffered, defer: false)
        PanelFactory.configure(panel)
        panel.becomesKeyOnlyIfNeeded = true   // 点按钮不抢焦点，点输入框才成为 key
        panel.onCancel = { [weak self] in self?.hideDrawer() }
        let root = DrawerView(store: store, actions: DrawerActions(close: { [weak self] in self?.hideDrawer() }))
        let host = NSHostingView(rootView: root)
        host.frame = NSRect(origin: .zero, size: drawerSize)
        host.autoresizingMask = [.width, .height]
        panel.contentView = host
        drawerPanel = panel
    }

    @objc func toggleDrawer() {
        if drawerPanel.isVisible { hideDrawer() } else { showDrawer() }
    }

    private func showDrawer() {
        positionDrawer()
        drawerPanel.alphaValue = 0
        drawerPanel.orderFrontRegardless()
        NSAnimationContext.runAnimationGroup { ctx in
            ctx.duration = 0.16
            drawerPanel.animator().alphaValue = 1
        }
    }

    private func hideDrawer() {
        guard drawerPanel.isVisible else { return }
        drawerPanel.orderOut(nil)
    }

    /// 贴着悬浮球展开（优先左右两侧，放不下则上下），始终限制在当前屏幕可见区域内。
    private func positionDrawer() {
        let size = drawerPanel.frame.size
        let anchor: NSRect
        if orbPanel.isVisible {
            anchor = orbPanel.frame
        } else if let button = statusItem.button, let w = button.window {
            anchor = w.frame
        } else {
            anchor = NSRect(origin: initialOrbOrigin(), size: orbSize)
        }
        let vf = screenFor(anchor).visibleFrame
        let gap: CGFloat = 4
        var x: CGFloat
        var y: CGFloat

        let roomLeft = anchor.minX - vf.minX
        let roomRight = vf.maxX - anchor.maxX
        if roomLeft >= size.width + gap || roomRight >= size.width + gap {
            x = anchor.midX > vf.midX && roomLeft >= size.width + gap
                ? anchor.minX - size.width - gap
                : (roomRight >= size.width + gap ? anchor.maxX + gap : anchor.minX - size.width - gap)
            y = anchor.maxY - size.height
        } else {
            x = anchor.midX - size.width / 2
            y = (vf.maxY - anchor.maxY >= size.height + gap) ? anchor.maxY + gap : anchor.minY - size.height - gap
        }
        x = min(max(x, vf.minX + 4), vf.maxX - size.width - 4)
        y = min(max(y, vf.minY + 4), vf.maxY - size.height - 4)
        drawerPanel.setFrameOrigin(NSPoint(x: x, y: y))
    }

    // MARK: Toast

    private func setupToastPanel() {
        let panel = NSPanel(contentRect: NSRect(x: 0, y: 0, width: 200, height: 52),
                            styleMask: [.borderless, .nonactivatingPanel],
                            backing: .buffered, defer: false)
        PanelFactory.configure(panel)
        panel.level = .statusBar
        panel.ignoresMouseEvents = true
        let host = NSHostingView(rootView: ToastView(model: toastModel))
        panel.contentView = host
        toastPanel = panel
        toastHost = host
    }

    private func showToast(_ message: String, isError: Bool = false) {
        toastModel.message = message
        toastModel.isError = isError
        toastHost.layoutSubtreeIfNeeded()
        let fitting = toastHost.fittingSize
        toastPanel.setContentSize(NSSize(width: max(80, fitting.width), height: max(40, fitting.height)))
        positionToast()
        toastPanel.alphaValue = 1
        toastPanel.orderFrontRegardless()

        toastGeneration += 1
        let generation = toastGeneration
        DispatchQueue.main.asyncAfter(deadline: .now() + 2.2) { [weak self] in
            guard let self = self, self.toastGeneration == generation else { return }
            NSAnimationContext.runAnimationGroup { ctx in
                ctx.duration = 0.25
                self.toastPanel.animator().alphaValue = 0
            }
            DispatchQueue.main.asyncAfter(deadline: .now() + 0.3) { [weak self] in
                guard let self = self, self.toastGeneration == generation else { return }
                self.toastPanel.orderOut(nil)
            }
        }
    }

    private func positionToast() {
        let size = toastPanel.frame.size
        if orbPanel.isVisible {
            let orb = orbPanel.frame
            let vf = screenFor(orb).visibleFrame
            var x = orb.midX > vf.midX ? orb.minX - size.width + 6 : orb.maxX - 6
            var y = orb.midY - size.height / 2
            x = min(max(x, vf.minX), vf.maxX - size.width)
            y = min(max(y, vf.minY), vf.maxY - size.height)
            toastPanel.setFrameOrigin(NSPoint(x: x, y: y))
        } else {
            let vf = (NSScreen.main ?? NSScreen.screens[0]).visibleFrame
            toastPanel.setFrameOrigin(NSPoint(x: vf.maxX - size.width - 12, y: vf.maxY - size.height - 6))
        }
    }

    private func handle(_ event: StoreEvent) {
        switch event {
        case .captured(let index, let autoAttached):
            orbView.pulse()
            let roleLabel = (store.items.last?.role == .user) ? "提问" : "回复"
            let base = "+1 已收录 #\(String(format: "%02d", index)) · \(roleLabel)"
            showToast(autoAttached > 0 ? "\(base) · 📎 \(autoAttached)" : base)
        case .attached(let name, let count):
            orbView.pulse()
            showToast(count > 1 ? "📎 已挂载 \(name) 等 \(count) 个文件" : "📎 已挂载 \(name)")
        case .pendingFile(let name):
            showToast("📥 新文件 \(name) · 可在收集篮挂载")
        case .spaceCopied:
            orbView.pulse()
            showToast("✓ 空间已复制，可直接粘贴")
        case .rawCopied(let count):
            showToast("✓ 已复制 \(count) 段纯原文")
        case .info(let msg):
            showToast(msg)
        case .error(let msg):
            showToast(msg, isError: true)
        }
    }

    // MARK: Store binding

    private func bindStore() {
        store.onEvent = { [weak self] event in self?.handle(event) }

        store.$items
            .map { $0.count }
            .removeDuplicates()
            .receive(on: DispatchQueue.main)
            .sink { [weak self] count in
                self?.orbView.setCount(count)
                self?.updateStatusButton(count: count)
            }
            .store(in: &cancellables)

        store.$settings
            .map { $0.isListening }
            .removeDuplicates()
            .receive(on: DispatchQueue.main)
            .sink { [weak self] on in
                self?.orbView.setListening(on)
                self?.updateStatusButton(count: self?.store.items.count ?? 0)
            }
            .store(in: &cancellables)
    }

    // MARK: Menu bar

    private func setupStatusItem() {
        statusItem = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)
        if let button = statusItem.button {
            let image = NSImage(systemSymbolName: "sparkles.rectangle.stack", accessibilityDescription: "GrokClip")
                ?? NSImage(systemSymbolName: "sparkles", accessibilityDescription: "GrokClip")
            image?.isTemplate = true
            button.image = image
            button.imagePosition = .imageLeading
            button.toolTip = "GrokClip 空间"
        }
        let menu = NSMenu()
        menu.delegate = self
        menu.autoenablesItems = false
        statusItem.menu = menu
    }

    private func updateStatusButton(count: Int) {
        guard let button = statusItem?.button else { return }
        button.title = count > 0 ? " \(count)" : ""
        button.appearsDisabled = !store.settings.isListening
    }

    func menuNeedsUpdate(_ menu: NSMenu) {
        rebuild(menu: menu)
    }

    private func rebuild(menu: NSMenu) {
        menu.removeAllItems()
        let count = store.items.count
        let header = NSMenuItem(title: "GrokClip 空间 · \(count) 条 · \(store.settings.captureScope.displayName)", action: nil, keyEquivalent: "")
        header.isEnabled = false
        menu.addItem(header)
        menu.addItem(.separator())

        addItem(menu, drawerPanel.isVisible ? "收起收集篮" : "打开收集篮", #selector(toggleDrawer))
        addItem(menu, store.settings.isListening ? "暂停自动监听" : "恢复自动监听", #selector(menuToggleListening))
        addItem(menu, isOrbVisible ? "隐藏悬浮图标" : "显示悬浮图标", #selector(menuToggleOrb))
        menu.addItem(.separator())
        let gen = addItem(menu, "一键生成空间并复制", #selector(menuGenerateSpace), key: "g")
        gen.isEnabled = store.selectedCount > 0 && !store.isExporting
        addItem(menu, "收录当前剪贴板", #selector(menuCaptureClipboard))
        addItem(menu, "打开导出目录", #selector(menuOpenExportDirectory))
        let clear = addItem(menu, "清空收集篮", #selector(menuClearAll))
        clear.isEnabled = count > 0
        menu.addItem(.separator())
        let login = addItem(menu, "开机自动启动", #selector(menuToggleLaunchAtLogin))
        login.state = SMAppService.mainApp.status == .enabled ? .on : .off
        menu.addItem(.separator())
        addItem(menu, "退出 GrokClip 空间", #selector(menuQuit), key: "q")
    }

    @discardableResult
    private func addItem(_ menu: NSMenu, _ title: String, _ action: Selector, key: String = "") -> NSMenuItem {
        let item = NSMenuItem(title: title, action: action, keyEquivalent: key)
        item.target = self
        item.isEnabled = true
        menu.addItem(item)
        return item
    }

    private func showContextMenu(event: NSEvent) {
        let menu = NSMenu()
        menu.autoenablesItems = false
        rebuild(menu: menu)
        NSMenu.popUpContextMenu(menu, with: event, for: orbView)
    }

    @objc private func menuToggleListening() { store.toggleListening() }
    @objc private func menuToggleOrb() { setOrbVisible(!isOrbVisible) }
    @objc private func menuGenerateSpace() { store.generateSpaceAndCopy() }
    @objc private func menuCaptureClipboard() { store.captureCurrentPasteboardManually() }
    @objc private func menuOpenExportDirectory() { store.openExportDirectory() }

    @objc private func menuClearAll() {
        let alert = NSAlert()
        alert.messageText = "清空收集篮？"
        alert.informativeText = "将移除全部 \(store.items.count) 条收录（不会删除磁盘上的原文件与已导出的空间）。"
        alert.addButton(withTitle: "清空")
        alert.addButton(withTitle: "取消")
        NSApp.activate(ignoringOtherApps: true)
        if alert.runModal() == .alertFirstButtonReturn { store.clearAll() }
    }

    @objc private func menuToggleLaunchAtLogin() {
        do {
            if SMAppService.mainApp.status == .enabled {
                try SMAppService.mainApp.unregister()
                showToast("已关闭开机自动启动")
            } else {
                try SMAppService.mainApp.register()
                showToast("已开启开机自动启动")
            }
        } catch {
            showToast("设置开机启动失败：\(error.localizedDescription)", isError: true)
        }
    }

    @objc private func menuQuit() { NSApp.terminate(nil) }
}
