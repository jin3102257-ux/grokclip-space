"""Windows 剪贴板后端（ctypes 直接调用 Win32 API，零第三方依赖）。

- 变化检测：GetClipboardSequenceNumber（不打开剪贴板，几乎零开销）
- 敏感内容：跳过 ExcludeClipboardContentFromMonitorProcessing / Clipboard Viewer Ignore，
  以及 CanIncludeInClipboardHistory = 0（密码管理器的约定格式）
- 前台应用：GetForegroundWindow → GetWindowThreadProcessId → QueryFullProcessImageNameW
- 写文件：CF_HDROP（DROPFILES, fWide=1, UTF-16 路径列表以双 NUL 结尾）+ Preferred DropEffect=COPY，
  在微信 / 飞书 / 资源管理器里 Ctrl+V 粘贴的是 .html 文件本身
所有函数都显式设置 argtypes / restype，HANDLE / HGLOBAL 均为指针宽度，64 位安全。
"""
import ctypes
import struct
import threading
import time
from ctypes import wintypes

from .clipboard_base import Foreground, Snapshot, build_dropfiles

CF_UNICODETEXT = 13
CF_HDROP = 15
GMEM_MOVEABLE = 0x0002
GMEM_ZEROINIT = 0x0040
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
DROPEFFECT_COPY = 1

HGLOBAL = wintypes.HANDLE  # c_void_p：指针宽度
SIZE_T = ctypes.c_size_t

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
shell32 = ctypes.WinDLL("shell32", use_last_error=True)


def _fn(dll, name, restype, argtypes):
    f = getattr(dll, name)
    f.restype = restype
    f.argtypes = argtypes
    return f


GetClipboardSequenceNumber = _fn(user32, "GetClipboardSequenceNumber", wintypes.DWORD, [])
OpenClipboard = _fn(user32, "OpenClipboard", wintypes.BOOL, [wintypes.HWND])
CloseClipboard = _fn(user32, "CloseClipboard", wintypes.BOOL, [])
EmptyClipboard = _fn(user32, "EmptyClipboard", wintypes.BOOL, [])
GetClipboardData = _fn(user32, "GetClipboardData", wintypes.HANDLE, [wintypes.UINT])
SetClipboardData = _fn(user32, "SetClipboardData", wintypes.HANDLE, [wintypes.UINT, wintypes.HANDLE])
IsClipboardFormatAvailable = _fn(user32, "IsClipboardFormatAvailable", wintypes.BOOL, [wintypes.UINT])
RegisterClipboardFormatW = _fn(user32, "RegisterClipboardFormatW", wintypes.UINT, [wintypes.LPCWSTR])
GetForegroundWindow = _fn(user32, "GetForegroundWindow", wintypes.HWND, [])
GetWindowThreadProcessId = _fn(user32, "GetWindowThreadProcessId", wintypes.DWORD,
                               [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)])
GetWindowTextLengthW = _fn(user32, "GetWindowTextLengthW", ctypes.c_int, [wintypes.HWND])
GetWindowTextW = _fn(user32, "GetWindowTextW", ctypes.c_int, [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int])

GlobalAlloc = _fn(kernel32, "GlobalAlloc", HGLOBAL, [wintypes.UINT, SIZE_T])
GlobalLock = _fn(kernel32, "GlobalLock", wintypes.LPVOID, [HGLOBAL])
GlobalUnlock = _fn(kernel32, "GlobalUnlock", wintypes.BOOL, [HGLOBAL])
GlobalFree = _fn(kernel32, "GlobalFree", HGLOBAL, [HGLOBAL])
GlobalSize = _fn(kernel32, "GlobalSize", SIZE_T, [HGLOBAL])
OpenProcess = _fn(kernel32, "OpenProcess", wintypes.HANDLE, [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD])
CloseHandle = _fn(kernel32, "CloseHandle", wintypes.BOOL, [wintypes.HANDLE])
QueryFullProcessImageNameW = _fn(kernel32, "QueryFullProcessImageNameW", wintypes.BOOL,
                                 [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR,
                                  ctypes.POINTER(wintypes.DWORD)])
DragQueryFileW = _fn(shell32, "DragQueryFileW", wintypes.UINT,
                     [wintypes.HANDLE, wintypes.UINT, wintypes.LPWSTR, wintypes.UINT])

CF_EXCLUDE_MONITOR = RegisterClipboardFormatW("ExcludeClipboardContentFromMonitorProcessing")
CF_VIEWER_IGNORE = RegisterClipboardFormatW("Clipboard Viewer Ignore")
CF_CAN_INCLUDE_HISTORY = RegisterClipboardFormatW("CanIncludeInClipboardHistory")
CF_PREFERRED_DROPEFFECT = RegisterClipboardFormatW("Preferred DropEffect")


class _Clipboard(object):
    """with 语句打开剪贴板（带重试：其他程序可能短暂占用）。"""

    def __init__(self, hwnd=None, attempts=12):
        self.hwnd = hwnd
        self.attempts = attempts
        self.ok = False

    def __enter__(self):
        for _ in range(self.attempts):
            if OpenClipboard(self.hwnd):
                self.ok = True
                return self
            time.sleep(0.025)
        return self

    def __exit__(self, *exc):
        if self.ok:
            CloseClipboard()
        return False


def _global_bytes(handle):
    """读取 HGLOBAL 的全部字节；失败返回 None。"""
    if not handle:
        return None
    size = GlobalSize(handle)
    ptr = GlobalLock(handle)
    if not ptr:
        return None
    try:
        return ctypes.string_at(ptr, size)
    finally:
        GlobalUnlock(handle)


def _set_global(fmt, data):
    """分配可移动全局内存并交给剪贴板；SetClipboardData 成功后内存归系统所有，失败则自行释放。"""
    h = GlobalAlloc(GMEM_MOVEABLE | GMEM_ZEROINIT, len(data))
    if not h:
        return False
    ptr = GlobalLock(h)
    if not ptr:
        GlobalFree(h)
        return False
    try:
        ctypes.memmove(ptr, data, len(data))
    finally:
        GlobalUnlock(h)
    if not SetClipboardData(fmt, h):
        GlobalFree(h)
        return False
    return True


class WindowsClipboardBackend(object):
    name = "win32"
    can_detect_foreground = True

    def __init__(self):
        self.warnings = []
        self.hwnd = None
        self._last_seq = GetClipboardSequenceNumber()
        self._lock = threading.Lock()

    def attach_tk(self, root):
        try:
            self.hwnd = wintypes.HWND(int(root.winfo_id()))
        except Exception:
            self.hwnd = None

    # ---------------- reading (worker thread) ----------------

    @staticmethod
    def _is_sensitive_locked():
        if CF_EXCLUDE_MONITOR and IsClipboardFormatAvailable(CF_EXCLUDE_MONITOR):
            return True
        if CF_VIEWER_IGNORE and IsClipboardFormatAvailable(CF_VIEWER_IGNORE):
            return True
        if CF_CAN_INCLUDE_HISTORY and IsClipboardFormatAvailable(CF_CAN_INCLUDE_HISTORY):
            raw = _global_bytes(GetClipboardData(CF_CAN_INCLUDE_HISTORY))
            if raw is not None and len(raw) >= 4 and struct.unpack("<I", raw[:4])[0] == 0:
                return True
        return False

    @staticmethod
    def _read_text_locked():
        if not IsClipboardFormatAvailable(CF_UNICODETEXT):
            return None
        raw = _global_bytes(GetClipboardData(CF_UNICODETEXT))
        if raw is None:
            return None
        text = raw.decode("utf-16-le", errors="replace")
        nul = text.find("\0")
        return text[:nul] if nul >= 0 else text

    @staticmethod
    def _read_files_locked():
        if not IsClipboardFormatAvailable(CF_HDROP):
            return []
        h = GetClipboardData(CF_HDROP)
        if not h:
            return []
        count = DragQueryFileW(h, 0xFFFFFFFF, None, 0)
        out = []
        for i in range(count):
            n = DragQueryFileW(h, i, None, 0)
            buf = ctypes.create_unicode_buffer(n + 1)
            if DragQueryFileW(h, i, buf, n + 1):
                out.append(buf.value)
        return out

    def poll_change(self):
        seq = GetClipboardSequenceNumber()
        with self._lock:
            if seq == self._last_seq:
                return None
            self._last_seq = seq
        with _Clipboard(None) as cb:
            if not cb.ok:
                return None
            if self._is_sensitive_locked():
                return Snapshot(None, sensitive=True)
            return Snapshot(self._read_text_locked())

    def read_manual(self):
        with self._lock:
            self._last_seq = GetClipboardSequenceNumber()
        with _Clipboard(None) as cb:
            if not cb.ok:
                return ("error", "剪贴板被其他程序占用，请稍后再试")
            if self._is_sensitive_locked():
                return ("sensitive",)
            files = self._read_files_locked()
            if files:
                return ("files", files)
            text = self._read_text_locked()
            if text is not None and text.strip():
                return ("text", text)
        return ("empty",)

    def foreground(self):
        hwnd = GetForegroundWindow()
        if not hwnd:
            return None
        pid = wintypes.DWORD(0)
        GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        n = GetWindowTextLengthW(hwnd)
        tbuf = ctypes.create_unicode_buffer(max(n, 0) + 1)
        GetWindowTextW(hwnd, tbuf, n + 1)
        exe = ""
        hp = OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid.value)
        if hp:
            try:
                size = wintypes.DWORD(32768)
                buf = ctypes.create_unicode_buffer(size.value)
                if QueryFullProcessImageNameW(hp, 0, buf, ctypes.byref(size)):
                    exe = buf.value
            finally:
                CloseHandle(hp)
        return Foreground(process=exe, title=tbuf.value, pid=pid.value)

    # ---------------- writing (UI thread) ----------------

    def _write(self, entries):
        # 整个写入过程持有 _lock：监听线程在此期间无法比较序号，写完立刻记录新序号，
        # 因此自己写入的变化永远不会被当成新复制。
        with self._lock:
            with _Clipboard(self.hwnd) as cb:
                if not cb.ok:
                    return False, "剪贴板被其他程序占用，请稍后再试"
                if not EmptyClipboard():
                    return False, "无法清空剪贴板（错误码 %d）" % ctypes.get_last_error()
                for fmt, data in entries:
                    if not _set_global(fmt, data):
                        return False, "写入剪贴板失败（错误码 %d）" % ctypes.get_last_error()
            self._last_seq = GetClipboardSequenceNumber()
        return True, ""

    def write_text(self, text):
        return self._write([(CF_UNICODETEXT, (text + "\0").encode("utf-16-le"))])

    def write_files(self, paths):
        entries = [(CF_HDROP, build_dropfiles([str(p) for p in paths]))]
        if CF_PREFERRED_DROPEFFECT:
            entries.append((CF_PREFERRED_DROPEFFECT, struct.pack("<I", DROPEFFECT_COPY)))
        return self._write(entries)
