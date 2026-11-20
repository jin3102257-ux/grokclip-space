import AppKit
import SwiftUI
import UniformTypeIdentifiers

// MARK: - Panels

/// 可成为 key（让空间标题输入框能打字），但不会成为 main，也不会激活应用抢焦点。
final class KeyablePanel: NSPanel {
    var onCancel: (() -> Void)?
    override var canBecomeKey: Bool { true }
    override var canBecomeMain: Bool { false }
    override func cancelOperation(_ sender: Any?) { onCancel?() }
}

enum PanelFactory {
    static func configure(_ panel: NSPanel) {
        panel.isFloatingPanel = true
        panel.level = .floating
        panel.collectionBehavior = [.canJoinAllSpaces, .fullScreenAuxiliary]
        panel.backgroundColor = .clear
        panel.isOpaque = false
        panel.hasShadow = false
        panel.hidesOnDeactivate = false
        panel.isReleasedWhenClosed = false
        panel.isMovableByWindowBackground = false
        panel.titleVisibility = .hidden
        panel.titlebarAppearsTransparent = true
        panel.animationBehavior = .utilityWindow
    }
}

// MARK: - Floating orb (pure AppKit + Core Animation for precise mouse / drag-and-drop control)

final class OrbHostView: NSView {
    var onClick: (() -> Void)?
    var onRightClick: ((NSEvent) -> Void)?
    var onMoved: (() -> Void)?
    var onDragEnded: (() -> Void)?
    var onFilesDropped: (([URL]) -> Void)?

    private let orbDiameter: CGFloat = 54
    private let orbContainer = CALayer()
    private let glowLayer = CALayer()
    private let gradientLayer = CAGradientLayer()
    private let ringLayer = CAShapeLayer()
    private let badgeLayer = CALayer()
    private let badgeText = CATextLayer()
    private let dotLayer = CALayer()
    private let iconView = NSImageView()

    private var mouseDownScreenPoint: NSPoint = .zero
    private var windowOriginAtMouseDown: NSPoint = .zero
    private var isDragging = false
    private var isHoveringFiles = false
    private var trackingArea: NSTrackingArea?

    override init(frame frameRect: NSRect) {
        super.init(frame: frameRect)
        setup()
    }

    required init?(coder: NSCoder) {
        super.init(coder: coder)
        setup()
    }

    override var isFlipped: Bool { false }
    override func acceptsFirstMouse(for event: NSEvent?) -> Bool { true }
    override var mouseDownCanMoveWindow: Bool { false }

    /// 整个视图作为唯一的命中目标，子视图（图标）不拦截鼠标与拖拽。
    override func hitTest(_ point: NSPoint) -> NSView? {
        let local = convert(point, from: superview)
        return bounds.contains(local) ? self : nil
    }

    private func setup() {
        wantsLayer = true
        layer?.masksToBounds = false
        registerForDraggedTypes([.fileURL])

        let center = CGPoint(x: bounds.midX, y: bounds.midY)
        let orbRect = CGRect(x: 0, y: 0, width: orbDiameter, height: orbDiameter)

        orbContainer.bounds = orbRect
        orbContainer.position = center
        orbContainer.masksToBounds = false

        glowLayer.frame = orbRect
        glowLayer.cornerRadius = orbDiameter / 2
        glowLayer.backgroundColor = NSColor(calibratedRed: 0.36, green: 0.29, blue: 0.86, alpha: 1).cgColor
        glowLayer.shadowColor = NSColor(calibratedRed: 0.49, green: 0.36, blue: 0.96, alpha: 1).cgColor
        glowLayer.shadowOpacity = 0.55
        glowLayer.shadowRadius = 9
        glowLayer.shadowOffset = CGSize(width: 0, height: -2)
        glowLayer.shadowPath = CGPath(ellipseIn: orbRect, transform: nil)

        gradientLayer.frame = orbRect
        gradientLayer.cornerRadius = orbDiameter / 2
        gradientLayer.masksToBounds = true
        gradientLayer.colors = [
            NSColor(calibratedRed: 0.31, green: 0.27, blue: 0.90, alpha: 1).cgColor, // indigo
            NSColor(calibratedRed: 0.55, green: 0.36, blue: 0.96, alpha: 1).cgColor, // violet
        ]
        gradientLayer.startPoint = CGPoint(x: 0, y: 0)
        gradientLayer.endPoint = CGPoint(x: 1, y: 1)
        gradientLayer.borderWidth = 1
        gradientLayer.borderColor = NSColor.white.withAlphaComponent(0.28).cgColor

        let ringRect = orbRect.insetBy(dx: -5, dy: -5)
        ringLayer.frame = orbRect
        ringLayer.path = CGPath(ellipseIn: ringRect, transform: nil)
        ringLayer.fillColor = NSColor.clear.cgColor
        ringLayer.strokeColor = NSColor(calibratedRed: 0.40, green: 0.85, blue: 1.0, alpha: 1).cgColor
        ringLayer.lineWidth = 3
        ringLayer.opacity = 0
        ringLayer.shadowColor = ringLayer.strokeColor
        ringLayer.shadowOpacity = 0.9
        ringLayer.shadowRadius = 6
        ringLayer.shadowOffset = .zero

        orbContainer.addSublayer(ringLayer)
        orbContainer.addSublayer(glowLayer)
        orbContainer.addSublayer(gradientLayer)
        layer?.addSublayer(orbContainer)

        // 图标（SF Symbol，白色）
        let symbolNames = ["sparkles.rectangle.stack", "square.stack.3d.up.fill", "sparkles"]
        var image: NSImage?
        for name in symbolNames {
            if let img = NSImage(systemSymbolName: name, accessibilityDescription: "GrokClip") { image = img; break }
        }
        iconView.image = image
        iconView.symbolConfiguration = NSImage.SymbolConfiguration(pointSize: 21, weight: .semibold)
        iconView.contentTintColor = .white
        iconView.imageScaling = .scaleProportionallyUpOrDown
        let iconSize: CGFloat = 26
        iconView.frame = NSRect(x: center.x - iconSize / 2, y: center.y - iconSize / 2, width: iconSize, height: iconSize)
        addSubview(iconView)

        // 监听状态指示点（右下）
        let dotSize: CGFloat = 12
        dotLayer.frame = CGRect(x: center.x + orbDiameter / 2 - dotSize + 1, y: center.y - orbDiameter / 2 + 1, width: dotSize, height: dotSize)
        dotLayer.cornerRadius = dotSize / 2
        dotLayer.borderWidth = 2
        dotLayer.borderColor = NSColor.white.cgColor
        dotLayer.backgroundColor = NSColor.systemGreen.cgColor
        layer?.addSublayer(dotLayer)

        // 数量角标（右上）
        badgeLayer.cornerRadius = 9
        badgeLayer.backgroundColor = NSColor(calibratedRed: 1.0, green: 0.36, blue: 0.36, alpha: 1).cgColor
        badgeLayer.borderWidth = 1.5
        badgeLayer.borderColor = NSColor.white.cgColor
        badgeLayer.isHidden = true
        badgeText.fontSize = 10.5
        badgeText.font = NSFont.systemFont(ofSize: 10.5, weight: .bold)
        badgeText.foregroundColor = NSColor.white.cgColor
        badgeText.alignmentMode = .center
        badgeText.contentsScale = NSScreen.main?.backingScaleFactor ?? 2
        badgeLayer.addSublayer(badgeText)
        layer?.addSublayer(badgeLayer)

        toolTip = "GrokClip 空间：单击展开收集篮，拖动可移动，把文件拖到这里即可挂载"
    }

    override func viewDidChangeBackingProperties() {
        super.viewDidChangeBackingProperties()
        let scale = window?.backingScaleFactor ?? 2
        badgeText.contentsScale = scale
        ringLayer.contentsScale = scale
    }

    // MARK: State updates

    func setCount(_ count: Int) {
        CATransaction.begin()
        CATransaction.setDisableActions(true)
        if count <= 0 {
            badgeLayer.isHidden = true
        } else {
            let text = count > 99 ? "99+" : "\(count)"
            badgeText.string = text
            let width = max(18, CGFloat(text.count) * 7 + 9)
            let center = CGPoint(x: bounds.midX, y: bounds.midY)
            let x = min(bounds.maxX - width, center.x + orbDiameter / 2 - width / 2 - 1)
            badgeLayer.frame = CGRect(x: x, y: center.y + orbDiameter / 2 - 13, width: width, height: 18)
            badgeText.frame = CGRect(x: 0, y: 2.5, width: width, height: 14)
            badgeLayer.isHidden = false
        }
        CATransaction.commit()
    }

    func setListening(_ on: Bool) {
        dotLayer.backgroundColor = (on ? NSColor.systemGreen : NSColor.systemGray).cgColor
        gradientLayer.opacity = on ? 1 : 0.72
    }

    /// 收录成功时的弹簧脉冲
    func pulse() {
        let spring = CASpringAnimation(keyPath: "transform.scale")
        spring.fromValue = 0.82
        spring.toValue = 1.0
        spring.damping = 9
        spring.stiffness = 220
        spring.mass = 0.8
        spring.initialVelocity = 6
        spring.duration = spring.settlingDuration
        orbContainer.add(spring, forKey: "pulse")

        let flash = CABasicAnimation(keyPath: "shadowRadius")
        flash.fromValue = 20
        flash.toValue = 9
        flash.duration = 0.6
        glowLayer.add(flash, forKey: "flash")
    }

    private func setHoveringFiles(_ hovering: Bool) {
        guard hovering != isHoveringFiles else { return }
        isHoveringFiles = hovering
        if hovering {
            ringLayer.opacity = 1
            let breathe = CABasicAnimation(keyPath: "transform.scale")
            breathe.fromValue = 0.96
            breathe.toValue = 1.08
            breathe.duration = 0.55
            breathe.autoreverses = true
            breathe.repeatCount = .infinity
            breathe.timingFunction = CAMediaTimingFunction(name: .easeInEaseOut)
            ringLayer.add(breathe, forKey: "breathe")
            orbContainer.setAffineTransform(CGAffineTransform(scaleX: 1.06, y: 1.06))
        } else {
            ringLayer.removeAnimation(forKey: "breathe")
            ringLayer.opacity = 0
            orbContainer.setAffineTransform(.identity)
        }
    }

    // MARK: Mouse: < 5pt = click, >= 5pt = drag

    override func mouseDown(with event: NSEvent) {
        mouseDownScreenPoint = NSEvent.mouseLocation
        windowOriginAtMouseDown = window?.frame.origin ?? .zero
        isDragging = false
    }

    override func mouseDragged(with event: NSEvent) {
        guard let window = window else { return }
        let now = NSEvent.mouseLocation
        let dx = now.x - mouseDownScreenPoint.x
        let dy = now.y - mouseDownScreenPoint.y
        if !isDragging && (dx * dx + dy * dy).squareRoot() >= 5 { isDragging = true }
        guard isDragging else { return }
        var origin = NSPoint(x: windowOriginAtMouseDown.x + dx, y: windowOriginAtMouseDown.y + dy)
        let screen = NSScreen.screens.first { $0.frame.contains(now) } ?? window.screen ?? NSScreen.main
        if let vf = screen?.visibleFrame {
            let size = window.frame.size
            origin.x = min(max(origin.x, vf.minX), vf.maxX - size.width)
            origin.y = min(max(origin.y, vf.minY), vf.maxY - size.height)
        }
        window.setFrameOrigin(origin)
        onMoved?()
    }

    override func mouseUp(with event: NSEvent) {
        if isDragging {
            onDragEnded?()
        } else {
            onClick?()
        }
        isDragging = false
    }

    override func rightMouseDown(with event: NSEvent) {
        onRightClick?(event)
    }

    // MARK: Drag & drop files

    private func fileURLs(from info: NSDraggingInfo) -> [URL] {
        let options: [NSPasteboard.ReadingOptionKey: Any] = [.urlReadingFileURLsOnly: true]
        let objects = info.draggingPasteboard.readObjects(forClasses: [NSURL.self], options: options) ?? []
        return objects.compactMap { $0 as? URL }
    }

    override func draggingEntered(_ sender: NSDraggingInfo) -> NSDragOperation {
        guard !fileURLs(from: sender).isEmpty else { return [] }
        setHoveringFiles(true)
        return .copy
    }

    override func draggingUpdated(_ sender: NSDraggingInfo) -> NSDragOperation {
        isHoveringFiles ? NSDragOperation.copy : NSDragOperation()
    }

    override func draggingExited(_ sender: NSDraggingInfo?) {
        setHoveringFiles(false)
    }

    override func prepareForDragOperation(_ sender: NSDraggingInfo) -> Bool { true }

    override func performDragOperation(_ sender: NSDraggingInfo) -> Bool {
        let urls = fileURLs(from: sender)
        setHoveringFiles(false)
        guard !urls.isEmpty else { return false }
        onFilesDropped?(urls)
        pulse()
        return true
    }
}

// MARK: - Toast

final class ToastModel: ObservableObject {
    @Published var message: String = ""
    @Published var isError: Bool = false
}

struct ToastView: View {
    @ObservedObject var model: ToastModel

    var body: some View {
        Text(model.message)
            .font(.system(size: 13, weight: .semibold))
            .foregroundColor(.white)
            .lineLimit(1)
            .padding(.horizontal, 14)
            .padding(.vertical, 8)
            .background(
                Capsule().fill(model.isError
                               ? Color(red: 0.86, green: 0.30, blue: 0.30)
                               : Color(red: 0.20, green: 0.18, blue: 0.32).opacity(0.94))
            )
            .overlay(Capsule().stroke(Color.white.opacity(0.18), lineWidth: 1))
            .shadow(color: Color.black.opacity(0.22), radius: 8, x: 0, y: 3)
            .padding(10)
            .fixedSize()
    }
}

// MARK: - Drawer

struct DrawerActions {
    var close: () -> Void
}

struct DrawerView: View {
    @ObservedObject var store: ClipStore
    let actions: DrawerActions

    @State private var showSettings = false

    var body: some View {
        VStack(spacing: 0) {
            header
            Divider().opacity(0.6)
            titleBar
            if !store.recentPendingFiles.isEmpty { pendingStrip }
            clipList
            if let export = store.lastExport { exportBanner(export) }
            Divider().opacity(0.6)
            footer
        }
        .background(.ultraThickMaterial, in: RoundedRectangle(cornerRadius: 16, style: .continuous))
        .overlay(RoundedRectangle(cornerRadius: 16, style: .continuous).stroke(Color.primary.opacity(0.10), lineWidth: 1))
        .clipShape(RoundedRectangle(cornerRadius: 16, style: .continuous))
        .shadow(color: Color.black.opacity(0.22), radius: 14, x: 0, y: 6)
        .padding(10)
        .frame(width: 376, height: 540)
    }

    // MARK: Header

    private var header: some View {
        HStack(spacing: 8) {
            Image(systemName: "sparkles")
                .font(.system(size: 13, weight: .bold))
                .foregroundStyle(LinearGradient(colors: [Color(red: 0.31, green: 0.27, blue: 0.90),
                                                         Color(red: 0.55, green: 0.36, blue: 0.96)],
                                                startPoint: .topLeading, endPoint: .bottomTrailing))
            Text("GrokClip 空间")
                .font(.system(size: 14, weight: .semibold))
            Spacer(minLength: 4)
            Picker("", selection: Binding(get: { store.settings.captureScope },
                                          set: { store.setCaptureScope($0) })) {
                ForEach(CaptureScope.allCases) { scope in
                    Text(scope.displayName).tag(scope)
                }
            }
            .labelsHidden()
            .pickerStyle(.menu)
            .controlSize(.small)
            .frame(width: 104)
            .help("自动收录的来源范围")

            Toggle("", isOn: Binding(get: { store.settings.isListening },
                                     set: { store.setListening($0) }))
                .labelsHidden()
                .toggleStyle(.switch)
                .controlSize(.mini)
                .help(store.settings.isListening ? "自动监听中（点击暂停）" : "已暂停自动监听")

            Button { showSettings.toggle() } label: {
                Image(systemName: "gearshape").font(.system(size: 12, weight: .medium))
            }
            .buttonStyle(.borderless)
            .help("监听目录与导出目录")
            .popover(isPresented: $showSettings, arrowEdge: .bottom) {
                SettingsPopover(store: store)
            }

            Button(action: actions.close) {
                Image(systemName: "xmark").font(.system(size: 11, weight: .bold))
            }
            .buttonStyle(.borderless)
            .help("收起")
        }
        .padding(.horizontal, 12)
        .padding(.vertical, 9)
    }

    // MARK: Title bar

    private var titleBar: some View {
        HStack(spacing: 6) {
            TextField("空间标题（选填，默认按时间命名）", text: $store.spaceTitle)
                .textFieldStyle(.roundedBorder)
                .controlSize(.small)
            Button { store.captureCurrentPasteboardManually() } label: {
                Label("粘贴收录", systemImage: "plus")
            }
            .controlSize(.small)
            .help("把当前剪贴板内容（文本或文件）直接收进来，不受收录范围限制")
            Button("清空") { store.clearAll() }
                .controlSize(.small)
                .disabled(store.items.isEmpty)
        }
        .padding(.horizontal, 12)
        .padding(.vertical, 8)
    }

    // MARK: Pending files

    private var pendingStrip: some View {
        VStack(alignment: .leading, spacing: 5) {
            HStack {
                Text("新落地的文件").font(.system(size: 11, weight: .semibold)).foregroundColor(.secondary)
                Spacer()
                Button("全部忽略") { store.dismissAllPending() }
                    .buttonStyle(.borderless).font(.system(size: 11))
            }
            ScrollView(.horizontal, showsIndicators: false) {
                HStack(spacing: 6) {
                    ForEach(store.recentPendingFiles) { file in
                        HStack(spacing: 4) {
                            Image(systemName: "doc").font(.system(size: 10))
                            Text(file.fileName).font(.system(size: 11)).lineLimit(1).frame(maxWidth: 130)
                            Button { store.attachPendingToLatest(file) } label: {
                                Text("挂到最新").font(.system(size: 10, weight: .semibold))
                            }
                            .buttonStyle(.borderless)
                            Button { store.dismissPending(file) } label: {
                                Image(systemName: "xmark").font(.system(size: 8, weight: .bold))
                            }
                            .buttonStyle(.borderless)
                        }
                        .padding(.horizontal, 8).padding(.vertical, 4)
                        .background(Color.orange.opacity(0.12), in: Capsule())
                    }
                }
            }
        }
        .padding(.horizontal, 12)
        .padding(.bottom, 6)
    }

    // MARK: Clip list

    private var clipList: some View {
        Group {
            if store.items.isEmpty {
                VStack(spacing: 12) {
                    Spacer()
                    Image(systemName: "tray.and.arrow.down")
                        .font(.system(size: 30, weight: .light))
                        .foregroundColor(.secondary)
                    Text("在 Grok Bot 中点「复制」，原文会自动按顺序收入这里；2 分钟内产生的文件也会自动挂载，或直接把文件拖到悬浮球上。")
                        .font(.system(size: 12))
                        .foregroundColor(.secondary)
                        .multilineTextAlignment(.center)
                        .lineSpacing(3)
                        .padding(.horizontal, 28)
                    Spacer()
                }
                .frame(maxWidth: .infinity, maxHeight: .infinity)
            } else {
                VStack(spacing: 0) {
                    HStack {
                        Button(store.allSelected ? "取消全选" : "全选") { store.setAllSelected(!store.allSelected) }
                            .buttonStyle(.borderless)
                            .font(.system(size: 11))
                        Spacer()
                        Text("已选 \(store.selectedCount) / \(store.items.count)")
                            .font(.system(size: 11))
                            .foregroundColor(.secondary)
                    }
                    .padding(.horizontal, 14)
                    .padding(.bottom, 4)
                    ScrollViewReader { proxy in
                        ScrollView {
                            LazyVStack(spacing: 8) {
                                ForEach(Array(store.items.enumerated()), id: \.element.id) { pair in
                                    ClipCardView(store: store, item: pair.element, index: pair.offset,
                                                 isFirst: pair.offset == 0,
                                                 isLast: pair.offset == store.items.count - 1)
                                        .id(pair.element.id)
                                }
                            }
                            .padding(.horizontal, 12)
                            .padding(.vertical, 4)
                        }
                        .onChange(of: store.items.count) { _ in
                            if let last = store.items.last {
                                withAnimation(.easeOut(duration: 0.25)) { proxy.scrollTo(last.id, anchor: .bottom) }
                            }
                        }
                    }
                }
            }
        }
        .frame(maxHeight: .infinity)
    }

    // MARK: Export banner

    private func exportBanner(_ export: SpaceExportResult) -> some View {
        let sizeText = Formatters.byteString(Int64(export.byteSize))
        let skippedText = export.skippedAttachmentCount > 0 ? " · \(export.skippedAttachmentCount) 个附件过大未内嵌" : ""
        let detail: String = export.url.lastPathComponent + " · " + sizeText + skippedText
        return VStack(alignment: .leading, spacing: 6) {
            HStack(alignment: .top, spacing: 6) {
                Image(systemName: "checkmark.circle.fill").foregroundColor(.green)
                VStack(alignment: .leading, spacing: 2) {
                    Text("空间已生成并写入剪贴板，去微信/飞书 Cmd+V 即可发送")
                        .font(.system(size: 11.5, weight: .semibold))
                        .fixedSize(horizontal: false, vertical: true)
                    Text(detail)
                        .font(.system(size: 10.5))
                        .foregroundColor(.secondary)
                        .lineLimit(1)
                        .truncationMode(.middle)
                }
                Spacer(minLength: 0)
                Button { store.dismissLastExport() } label: {
                    Image(systemName: "xmark").font(.system(size: 9, weight: .bold))
                }
                .buttonStyle(.borderless)
            }
            HStack(spacing: 6) {
                Button("浏览器打开") { store.openLastExportInBrowser() }
                Button("访达定位") { store.revealLastExportInFinder() }
                ShareAnchorButton(title: "系统分享") { view in store.shareLastExport(from: view) }
                    .frame(width: 84, height: 22)
            }
            .controlSize(.small)
        }
        .padding(10)
        .background(Color.green.opacity(0.11), in: RoundedRectangle(cornerRadius: 10, style: .continuous))
        .padding(.horizontal, 12)
        .padding(.vertical, 6)
    }

    // MARK: Footer

    private var footer: some View {
        HStack(spacing: 8) {
            Button { store.copySelectedRawText() } label: {
                Label("复制纯原文", systemImage: "doc.on.doc")
            }
            .controlSize(.large)
            .disabled(store.selectedCount == 0)
            .help("把勾选条目的原文按顺序拼接（\\n\\n 分隔），无任何水印")

            Button { store.generateSpaceAndCopy() } label: {
                HStack(spacing: 6) {
                    if store.isExporting {
                        ProgressView().controlSize(.small)
                    } else {
                        Image(systemName: "sparkles")
                    }
                    Text(store.isExporting ? "正在生成…" : "一键生成空间并复制")
                        .fontWeight(.semibold)
                }
                .frame(maxWidth: .infinity)
            }
            .buttonStyle(.borderedProminent)
            .tint(Color(red: 0.40, green: 0.30, blue: 0.92))
            .controlSize(.large)
            .disabled(store.selectedCount == 0 || store.isExporting)
            .keyboardShortcut(.defaultAction)
        }
        .padding(12)
    }
}

// MARK: - Clip card

struct ClipCardView: View {
    @ObservedObject var store: ClipStore
    let item: ClipItem
    let index: Int
    let isFirst: Bool
    let isLast: Bool

    @State private var isDropTarget = false

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack(spacing: 6) {
                Toggle("", isOn: Binding(get: { item.isSelected }, set: { store.setSelected(item.id, $0) }))
                    .labelsHidden()
                    .toggleStyle(.checkbox)
                Text(String(format: "#%02d", index + 1))
                    .font(.system(size: 10.5, weight: .bold, design: .monospaced))
                    .foregroundColor(.white)
                    .padding(.horizontal, 6).padding(.vertical, 1.5)
                    .background(
                        LinearGradient(colors: [Color(red: 0.31, green: 0.27, blue: 0.90),
                                                Color(red: 0.55, green: 0.36, blue: 0.96)],
                                       startPoint: .leading, endPoint: .trailing),
                        in: RoundedRectangle(cornerRadius: 5, style: .continuous))
                RoleChip(role: item.role, name: store.speakerName(for: item.role)) {
                    store.toggleRole(item.id)
                }
                Text(Formatters.time.string(from: item.timestamp))
                    .font(.system(size: 10.5, design: .monospaced))
                    .foregroundColor(.secondary)
                Text(item.sourceApp)
                    .font(.system(size: 10.5))
                    .foregroundColor(.secondary)
                    .lineLimit(1)
                Spacer(minLength: 2)
                iconButton("arrow.up", help: "上移") { store.moveUp(item.id) }.disabled(isFirst)
                iconButton("arrow.down", help: "下移") { store.moveDown(item.id) }.disabled(isLast)
                iconButton("paperclip", help: "添加附件") { store.pickFiles(for: item.id) }
                iconButton("xmark", help: "删除此条") { store.delete(item.id) }
            }

            if item.rawContent.isEmpty {
                Text("（仅附件）").font(.system(size: 11.5)).foregroundColor(.secondary).italic()
            } else {
                Text(item.rawContent)
                    .font(.system(size: 11.5))
                    .lineLimit(3)
                    .foregroundColor(Color.primary.opacity(0.88))
                    .frame(maxWidth: .infinity, alignment: .leading)
                Text("\(item.rawContent.count) 字")
                    .font(.system(size: 9.5))
                    .foregroundColor(.secondary)
            }

            if !item.attachments.isEmpty {
                VStack(alignment: .leading, spacing: 4) {
                    ForEach(item.attachments) { att in
                        HStack(spacing: 4) {
                            Image(systemName: att.fileExists ? "paperclip" : "exclamationmark.triangle")
                                .font(.system(size: 9.5))
                            Text(att.fileName).font(.system(size: 10.5)).lineLimit(1).truncationMode(.middle)
                            Text(att.fileExists ? att.formattedSize : "文件已移动")
                                .font(.system(size: 9.5)).foregroundColor(.secondary)
                            Spacer(minLength: 0)
                            Button { store.removeAttachment(clipID: item.id, attachmentID: att.id) } label: {
                                Image(systemName: "xmark").font(.system(size: 8, weight: .bold))
                            }
                            .buttonStyle(.borderless)
                            .help("移除附件（不会删除原文件）")
                        }
                        .foregroundColor(att.fileExists ? Color.accentColor : Color.orange)
                        .padding(.horizontal, 7).padding(.vertical, 3)
                        .background(Color.accentColor.opacity(0.09), in: RoundedRectangle(cornerRadius: 6, style: .continuous))
                    }
                }
            }
        }
        .padding(9)
        .background(Color(NSColor.controlBackgroundColor).opacity(item.isSelected ? 0.95 : 0.55),
                    in: RoundedRectangle(cornerRadius: 10, style: .continuous))
        .overlay(
            RoundedRectangle(cornerRadius: 10, style: .continuous)
                .stroke(isDropTarget ? Color.accentColor : Color.primary.opacity(0.07),
                        lineWidth: isDropTarget ? 2 : 1)
        )
        .opacity(item.isSelected ? 1 : 0.7)
        .onDrop(of: [UTType.fileURL], isTargeted: $isDropTarget) { providers in
            loadFileURLs(from: providers) { urls in
                if !urls.isEmpty { store.attachFiles(urls, to: item.id) }
            }
            return true
        }
    }

    private func iconButton(_ symbol: String, help: String, action: @escaping () -> Void) -> some View {
        Button(action: action) {
            Image(systemName: symbol).font(.system(size: 10, weight: .semibold)).frame(width: 16, height: 16)
        }
        .buttonStyle(.borderless)
        .help(help)
    }
}

/// 说话人标记：「我 · 提问」绿色 /「Grok Bot · 回复」紫色，点击切换并立即保存。
struct RoleChip: View {
    let role: ClipRole
    let name: String
    let action: () -> Void

    private var tint: Color {
        role == .user ? Color(red: 0.07, green: 0.63, blue: 0.31) : Color(red: 0.36, green: 0.29, blue: 0.86)
    }

    var body: some View {
        Button(action: action) {
            HStack(spacing: 3) {
                Image(systemName: role == .user ? "person.fill" : "sparkles")
                    .font(.system(size: 8.5, weight: .bold))
                Text(role == .user ? "\(name) · 提问" : "\(name) · 回复")
                    .font(.system(size: 10, weight: .semibold))
                    .lineLimit(1)
            }
            .foregroundColor(tint)
            .padding(.horizontal, 6).padding(.vertical, 1.5)
            .background(tint.opacity(0.13), in: Capsule())
            .overlay(Capsule().stroke(tint.opacity(0.35), lineWidth: 0.8))
            .fixedSize()
        }
        .buttonStyle(.plain)
        .help(role == .user ? "当前标记为「提问」，点击改为 Grok 回复" : "当前标记为「回复」，点击改为我的提问")
    }
}

/// 从拖拽 provider 中解析文件 URL，在主线程回调。
func loadFileURLs(from providers: [NSItemProvider], completion: @escaping ([URL]) -> Void) {
    final class Collector { var urls: [URL] = [] }
    let group = DispatchGroup()
    let lock = NSLock()
    let collector = Collector()
    for provider in providers where provider.hasItemConformingToTypeIdentifier(UTType.fileURL.identifier) {
        group.enter()
        provider.loadItem(forTypeIdentifier: UTType.fileURL.identifier, options: nil) { item, _ in
            var url: URL?
            if let data = item as? Data {
                url = URL(dataRepresentation: data, relativeTo: nil)
            } else if let u = item as? URL {
                url = u
            } else if let s = item as? String {
                url = URL(string: s)
            }
            if let url = url, url.isFileURL {
                lock.lock(); collector.urls.append(url); lock.unlock()
            }
            group.leave()
        }
    }
    group.notify(queue: .main) { completion(collector.urls) }
}

// MARK: - Settings popover

struct SettingsPopover: View {
    @ObservedObject var store: ClipStore
    @State private var userName: String = ""
    @State private var assistantName: String = ""

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            Text("说话人名称（仅保存在本机）").font(.system(size: 12, weight: .semibold))
            HStack(spacing: 6) {
                Text("提问方").font(.system(size: 11)).frame(width: 44, alignment: .leading)
                TextField(SpeakerNames.defaultUser, text: $userName, onCommit: commitNames)
                    .textFieldStyle(.roundedBorder).font(.system(size: 11))
            }
            HStack(spacing: 6) {
                Text("回复方").font(.system(size: 11)).frame(width: 44, alignment: .leading)
                TextField(SpeakerNames.defaultAssistant, text: $assistantName, onCommit: commitNames)
                    .textFieldStyle(.roundedBorder).font(.system(size: 11))
            }
            Text("收录时会自动判断提问/回复，猜错了点条目上的标记即可切换。")
                .font(.system(size: 10.5)).foregroundColor(.secondary)
                .fixedSize(horizontal: false, vertical: true)

            Divider()

            Text("监听目录").font(.system(size: 12, weight: .semibold))
            Text("新文件落地后，会自动挂到前后 \(Int(store.settings.autoAttachWindowSeconds)) 秒内收录的原文下。")
                .font(.system(size: 10.5)).foregroundColor(.secondary)
                .fixedSize(horizontal: false, vertical: true)
            VStack(alignment: .leading, spacing: 4) {
                ForEach(store.settings.watchedDirectories, id: \.self) { path in
                    HStack(spacing: 6) {
                        Image(systemName: "folder").font(.system(size: 10))
                        Text(abbreviate(path)).font(.system(size: 11)).lineLimit(1).truncationMode(.middle)
                        Spacer(minLength: 4)
                        Button { store.removeWatchedDirectory(path) } label: {
                            Image(systemName: "minus.circle").font(.system(size: 11))
                        }
                        .buttonStyle(.borderless)
                    }
                }
                if store.settings.watchedDirectories.isEmpty {
                    Text("（未监听任何目录）").font(.system(size: 11)).foregroundColor(.secondary)
                }
            }
            Button("添加目录…") { store.addWatchedDirectory() }.controlSize(.small)

            Divider()

            Text("空间导出目录").font(.system(size: 12, weight: .semibold))
            HStack(spacing: 6) {
                Image(systemName: "folder.fill").font(.system(size: 10))
                Text(abbreviate(store.settings.exportDirectory)).font(.system(size: 11)).lineLimit(1).truncationMode(.middle)
            }
            HStack(spacing: 6) {
                Button("更改…") { store.chooseExportDirectory() }
                Button("打开") { store.openExportDirectory() }
            }
            .controlSize(.small)

            Divider()

            Stepper(value: Binding(get: { store.settings.autoAttachWindowSeconds },
                                   set: { store.setAutoAttachWindow($0) }),
                    in: 30...600, step: 30) {
                Text("自动挂载窗口：\(Int(store.settings.autoAttachWindowSeconds)) 秒").font(.system(size: 11))
            }

            HStack {
                Button("恢复默认目录") { store.resetDirectoriesToDefault() }
                    .buttonStyle(.borderless).font(.system(size: 11))
                Spacer()
            }
            Text("数据保存在 ~/Library/Application Support/GrokClipSpace/")
                .font(.system(size: 9.5)).foregroundColor(.secondary)
        }
        .padding(14)
        .frame(width: 300)
        .onAppear {
            userName = store.settings.userDisplayName
            assistantName = store.settings.assistantDisplayName
        }
        .onDisappear(perform: commitNames)
    }

    private func commitNames() {
        store.setSpeakerNames(user: userName, assistant: assistantName)
    }

    private func abbreviate(_ path: String) -> String {
        (path as NSString).abbreviatingWithTildeInPath
    }
}

// MARK: - Native share anchor

/// 桥接 NSButton，为 NSSharingServicePicker 提供弹出锚点。
struct ShareAnchorButton: NSViewRepresentable {
    let title: String
    let onTap: (NSView) -> Void

    func makeNSView(context: Context) -> NSButton {
        let button = NSButton(title: title, target: context.coordinator, action: #selector(Coordinator.clicked(_:)))
        if let img = NSImage(systemSymbolName: "square.and.arrow.up", accessibilityDescription: nil) {
            button.image = img
            button.imagePosition = .imageLeading
        }
        button.bezelStyle = .rounded
        button.controlSize = .small
        button.font = NSFont.systemFont(ofSize: NSFont.smallSystemFontSize)
        return button
    }

    func updateNSView(_ nsView: NSButton, context: Context) {
        context.coordinator.onTap = onTap
    }

    func makeCoordinator() -> Coordinator { Coordinator(onTap: onTap) }

    final class Coordinator: NSObject {
        var onTap: (NSView) -> Void
        init(onTap: @escaping (NSView) -> Void) { self.onTap = onTap }
        @objc func clicked(_ sender: NSButton) { onTap(sender) }
    }
}
