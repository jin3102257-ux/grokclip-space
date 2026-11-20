"""剪贴板监听的跨平台公共部分：来源白名单、SHA-256 去重、自写不回环、后台线程。

平台后端（clipboard_win / clipboard_linux）只需实现：
    poll_change() -> None | Snapshot          # 剪贴板没变返回 None（在后台线程调用）
    read_manual() -> ("text", str) | ("files", [path]) | ("sensitive",) | ("empty",)
    write_text(text) -> (ok, err)              # 在 UI 主线程调用
    write_files(paths) -> (ok, err)            # 在 UI 主线程调用
    foreground() -> Foreground | None
    can_detect_foreground: bool
    warnings: [str]
"""
import os
import queue
import struct
import threading

from . import models


class Snapshot(object):
    __slots__ = ("text", "sensitive")

    def __init__(self, text, sensitive=False):
        self.text = text
        self.sensitive = sensitive


class Foreground(object):
    __slots__ = ("process", "title", "pid")

    def __init__(self, process="", title="", pid=0):
        self.process = process or ""
        self.title = title or ""
        self.pid = pid or 0


# 进程名关键字（Windows 去掉 .exe 后、Linux /proc/<pid>/comm，统一小写）
BROWSER_PROCESSES = {
    "chrome", "chromium", "chromium-browser", "chromium-browse", "google-chrome", "google-chrome-stable",
    "msedge", "microsoft-edge", "microsoft-edge-stable", "firefox", "firefox-esr", "firefox-bin",
    "brave", "brave-browser", "opera", "vivaldi", "vivaldi-bin", "arc", "librewolf", "waterfox",
    "zen", "zen-bin", "floorp", "thorium", "yandex", "iexplore", "epiphany", "falkon",
    "360se", "360chrome", "qqbrowser", "sogouexplorer", "liebao", "2345explorer", "ucbrowser",
}
FRIENDLY_NAMES = {
    "chrome": "Google Chrome", "google-chrome": "Google Chrome", "msedge": "Microsoft Edge",
    "firefox": "Firefox", "firefox-esr": "Firefox", "brave": "Brave", "opera": "Opera",
    "vivaldi": "Vivaldi", "vivaldi-bin": "Vivaldi", "chromium": "Chromium", "arc": "Arc",
}


def _extra_keywords():
    raw = os.environ.get("GROKCLIP_EXTRA_APPS", "")
    return [k.strip().lower() for k in raw.split(",") if k.strip()]


def _basename(path):
    """同时识别 / 与 \\ 分隔符（Windows 路径在任何平台上都能正确取文件名）。"""
    return (path or "").replace("\\", "/").rsplit("/", 1)[-1]


def process_stem(name):
    n = _basename(name).lower()
    if n.endswith(".exe"):
        n = n[:-4]
    return n


def is_grok(fg):
    """进程名或窗口标题包含 grok 即视为 Grok（桌面端 / 网页版标签页标题都能命中）。"""
    p = process_stem(fg.process)
    t = (fg.title or "").lower()
    if "grok" in p or "grok" in t:
        return True
    return any(k in p or k in t for k in _extra_keywords())


def is_browser(fg):
    p = process_stem(fg.process)
    if p in BROWSER_PROCESSES:
        return True
    return any(p.startswith(b + "-") or p.startswith(b + " ") for b in BROWSER_PROCESSES)


def is_allowed(fg, scope, own_pid=None):
    own_pid = os.getpid() if own_pid is None else own_pid
    if fg is not None and fg.pid and fg.pid == own_pid:
        return False
    if scope == models.ALL_APPS:
        return True
    if fg is None:
        return False
    if scope == models.GROK_ONLY:
        return is_grok(fg)
    if scope == models.GROK_AND_BROWSERS:
        return is_grok(fg) or is_browser(fg)
    return False


def display_name(fg):
    if fg is None:
        return "未知来源"
    p = process_stem(fg.process)
    if "grok" in p or (not is_browser(fg) and "grok" in (fg.title or "").lower()):
        return "Grok Bot"
    if p in FRIENDLY_NAMES:
        return FRIENDLY_NAMES[p]
    if p:
        return os.path.splitext(_basename(fg.process))[0] or p
    return fg.title or "未知来源"


def build_dropfiles(paths):
    """Windows CF_HDROP 数据：DROPFILES 头（20 字节：pFiles=20, pt=(0,0), fNC=0, fWide=1）
    + UTF-16LE 路径列表，每个路径以 NUL 结尾，整体再以一个额外 NUL 结尾（双 NUL）。纯函数，便于跨平台测试。"""
    header = struct.pack("<IiiII", 20, 0, 0, 0, 1)
    body = ("".join(str(p) + "\0" for p in paths) + "\0").encode("utf-16-le")
    return header + body


def is_blank(text):
    return text is None or text.strip() == ""


class ClipboardMonitor(object):
    """低功耗轮询 + 敏感类型过滤 + 来源白名单 + SHA-256 去重。事件放进 self.events 队列，由 UI 线程取走：
        ("text", text, source)   新收录
        ("manual", result)        手动收录结果
    """

    def __init__(self, backend, interval=0.4):
        self.backend = backend
        self.interval = interval
        self.capture_scope = models.GROK_ONLY
        self.is_listening = True
        self.last_captured_hash = ""
        self.events = queue.Queue()
        self._manual_requests = queue.Queue()
        self._stop = threading.Event()
        self._thread = None
        self._hash_lock = threading.Lock()

    @property
    def effective_scope(self):
        if not self.backend.can_detect_foreground and self.capture_scope != models.ALL_APPS:
            return models.ALL_APPS
        return self.capture_scope

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        try:
            self.backend.poll_change()  # 建立基线：启动前已在剪贴板里的内容不收录
        except Exception:
            pass
        self._thread = threading.Thread(target=self._run, name="grokclip-clipboard", daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()

    def reset_dedup(self):
        with self._hash_lock:
            self.last_captured_hash = ""

    def request_manual_capture(self):
        self._manual_requests.put(True)

    # ---- silent writes (UI thread) ----

    def write_text(self, text):
        # 先更新去重基准再写入：即使监听线程恰好读到这次写入，也会被哈希去重拦下
        with self._hash_lock:
            self.last_captured_hash = models.sha256(text)
        return self.backend.write_text(text)

    def write_files(self, paths):
        return self.backend.write_files(paths)

    # ---- worker ----

    def _run(self):
        while not self._stop.is_set():
            try:
                while True:
                    try:
                        self._manual_requests.get_nowait()
                    except queue.Empty:
                        break
                    self.events.put(("manual", self._manual()))
                self.poll_once()
            except Exception:
                pass
            self._stop.wait(self.interval)

    def poll_once(self):
        snap = self.backend.poll_change()
        if snap is None:
            return None
        if not self.is_listening or snap.sensitive or is_blank(snap.text):
            return None
        fg = self.backend.foreground() if self.backend.can_detect_foreground else None
        if not is_allowed(fg, self.effective_scope):
            return None
        h = models.sha256(snap.text)
        with self._hash_lock:
            if h == self.last_captured_hash:
                return None
            self.last_captured_hash = h
        src = display_name(fg) if fg is not None else "未知来源"
        ev = ("text", snap.text, src)
        self.events.put(ev)
        return ev

    def _manual(self):
        try:
            res = self.backend.read_manual()
        except Exception as e:  # pragma: no cover
            return ("error", str(e))
        if res and res[0] == "text":
            with self._hash_lock:
                self.last_captured_hash = models.sha256(res[1])
        return res


class NullBackend(object):
    """无可用剪贴板工具时的占位后端：不监听，只能通过 Tk 写入纯文本。"""
    can_detect_foreground = False

    def __init__(self, reason=""):
        self.warnings = [reason] if reason else []
        self.tk_root = None
        self.name = "none"

    def attach_tk(self, root):
        self.tk_root = root

    def poll_change(self):
        return None

    def read_manual(self):
        return ("empty",)

    def foreground(self):
        return None

    def write_text(self, text):
        if self.tk_root is None:
            return False, "剪贴板不可用"
        try:
            self.tk_root.clipboard_clear()
            self.tk_root.clipboard_append(text)
            return True, ""
        except Exception as e:
            return False, str(e)

    def write_files(self, paths):
        return False, "当前环境无法以文件形式写入剪贴板，请用「打开所在文件夹」手动拖拽发送"
