import ctypes
import importlib
import struct
import sys
import unittest
from unittest import mock

from helpers import tempdir, write  # noqa: E402

from grokclip import clipboard_base as cb
from grokclip import clipboard_linux as cl
from grokclip import models
from grokclip.clipboard_base import Foreground, Snapshot


class MatchingTests(unittest.TestCase):
    def test_grok_and_browser_matching(self):
        self.assertTrue(cb.is_grok(Foreground(r"C:\Program Files\Grok Bot\Grok Bot.exe", "", 5)))
        self.assertTrue(cb.is_grok(Foreground("electron", "Grok Bot — 对话", 5)))
        self.assertTrue(cb.is_grok(Foreground("chrome.exe", "Grok - Google Chrome", 5)))
        self.assertFalse(cb.is_grok(Foreground("notepad.exe", "无标题 - 记事本", 5)))
        self.assertTrue(cb.is_browser(Foreground("C:\\x\\msedge.exe", "", 1)))
        self.assertTrue(cb.is_browser(Foreground("firefox-esr", "", 1)))
        self.assertTrue(cb.is_browser(Foreground("chromium-browse", "", 1)))  # /proc/comm 截断为 15 字符
        self.assertFalse(cb.is_browser(Foreground("WeChat.exe", "", 1)))

    def test_scopes(self):
        grok = Foreground("Grok Bot.exe", "Grok Bot", 11)
        chrome = Foreground("chrome.exe", "新标签页", 12)
        wechat = Foreground("WeChat.exe", "微信", 13)
        own = Foreground("pythonw.exe", "GrokClip Space · 收集篮", 99)
        for fg, want in ((grok, True), (chrome, False), (wechat, False)):
            self.assertEqual(cb.is_allowed(fg, models.GROK_ONLY, own_pid=99), want)
        self.assertTrue(cb.is_allowed(chrome, models.GROK_AND_BROWSERS, own_pid=99))
        self.assertFalse(cb.is_allowed(wechat, models.GROK_AND_BROWSERS, own_pid=99))
        self.assertTrue(cb.is_allowed(wechat, models.ALL_APPS, own_pid=99))
        self.assertTrue(cb.is_allowed(None, models.ALL_APPS, own_pid=99))
        self.assertFalse(cb.is_allowed(None, models.GROK_ONLY, own_pid=99))
        for scope in models.CAPTURE_SCOPES:  # 自己的窗口永远不收录
            self.assertFalse(cb.is_allowed(own, scope, own_pid=99))

    def test_display_name(self):
        self.assertEqual(cb.display_name(Foreground("Grok Bot.exe", "x", 1)), "Grok Bot")
        self.assertEqual(cb.display_name(Foreground("chrome.exe", "Grok", 1)), "Google Chrome")
        self.assertEqual(cb.display_name(Foreground("C:\\a\\WeChat.exe", "", 1)), "WeChat")
        self.assertEqual(cb.display_name(None), "未知来源")

    def test_extra_keywords_env(self):
        with mock.patch.dict("os.environ", {"GROKCLIP_EXTRA_APPS": "sand, mybot"}):
            self.assertTrue(cb.is_grok(Foreground("Sand.exe", "", 1)))
        self.assertFalse(cb.is_grok(Foreground("Sand.exe", "", 1)))


class FakeBackend(object):
    def __init__(self, fg=None, can_fg=True):
        self.queue = []
        self.fg = fg
        self.can_detect_foreground = can_fg
        self.written = []
        self.warnings = []

    def poll_change(self):
        return self.queue.pop(0) if self.queue else None

    def foreground(self):
        return self.fg

    def write_text(self, text):
        self.written.append(text)
        self.queue.append(Snapshot(text))  # 模拟：自己的写入也会产生一次“变化”
        return True, ""

    def write_files(self, paths):
        return True, ""

    def read_manual(self):
        return ("text", "manual")


class MonitorTests(unittest.TestCase):
    def setUp(self):
        self.b = FakeBackend(Foreground("Grok Bot.exe", "Grok Bot", 4242))
        self.m = cb.ClipboardMonitor(self.b)

    def test_capture_dedup_and_self_write(self):
        self.b.queue.append(Snapshot("hello"))
        self.assertEqual(self.m.poll_once(), ("text", "hello", "Grok Bot"))
        self.b.queue.append(Snapshot("hello"))
        self.assertIsNone(self.m.poll_once())           # SHA-256 去重
        self.m.write_text("joined raw")
        self.assertIsNone(self.m.poll_once())           # 自写不回环
        self.m.reset_dedup()
        self.b.queue.append(Snapshot("hello"))
        self.assertIsNotNone(self.m.poll_once())        # 删除条目后允许再次收录

    def test_filters(self):
        self.b.queue += [Snapshot("pw", sensitive=True), Snapshot("   \n "), Snapshot(None)]
        for _ in range(3):
            self.assertIsNone(self.m.poll_once())
        self.m.is_listening = False
        self.b.queue.append(Snapshot("paused"))
        self.assertIsNone(self.m.poll_once())
        self.m.is_listening = True
        self.b.fg = Foreground("notepad.exe", "记事本", 1)
        self.b.queue.append(Snapshot("elsewhere"))
        self.assertIsNone(self.m.poll_once())
        self.m.capture_scope = models.ALL_APPS
        self.b.queue.append(Snapshot("elsewhere"))
        self.assertEqual(self.m.poll_once()[2], "notepad")

    def test_no_foreground_detection_falls_back_to_all_apps(self):
        b = FakeBackend(None, can_fg=False)
        m = cb.ClipboardMonitor(b)
        self.assertEqual(m.effective_scope, models.ALL_APPS)
        b.queue.append(Snapshot("x"))
        self.assertEqual(m.poll_once(), ("text", "x", "未知来源"))

    def test_threaded_manual_capture(self):
        self.m.interval = 0.02
        self.m.start()
        self.m.request_manual_capture()
        ev = self.m.events.get(timeout=2)
        self.m.stop()
        self.assertEqual(ev, ("manual", ("text", "manual")))
        self.assertEqual(self.m.last_captured_hash, models.sha256("manual"))


class LinuxHelpersTests(unittest.TestCase):
    def test_uri_formats(self):
        paths = ["/tmp/a b/空间.html", "/tmp/x#y.html"]
        ul = cl.uri_list(paths)
        self.assertEqual(ul, "file:///tmp/a%20b/%E7%A9%BA%E9%97%B4.html\r\nfile:///tmp/x%23y.html\r\n")
        self.assertEqual(cl.gnome_copied_files(paths[:1]), "copy\nfile:///tmp/a%20b/%E7%A9%BA%E9%97%B4.html")
        self.assertEqual(cl.parse_uri_list(ul), paths)
        self.assertEqual(cl.parse_uri_list("copy\n" + ul), paths)

    def test_install_hint_never_runs_anything(self):
        with mock.patch("grokclip.clipboard_linux._which", side_effect=lambda n: n == "dnf"):
            self.assertEqual(cl.install_hint(["xclip", "tk"]), "sudo dnf install xclip python3-tkinter")
        with mock.patch("grokclip.clipboard_linux._which", side_effect=lambda n: n == "pacman"):
            self.assertEqual(cl.install_hint(["wl-clipboard"]), "sudo pacman -S wl-clipboard")

    def _backend(self, session="x11"):
        with mock.patch("grokclip.clipboard_linux._which", return_value="/usr/bin/x"):
            return cl.LinuxClipboardBackend(session=session)

    def test_wayland_warns_and_cannot_detect_foreground(self):
        b = self._backend("wayland")
        self.assertEqual(b.reader, "wl")
        self.assertFalse(b.can_detect_foreground)
        self.assertTrue(any("Wayland" in w for w in b.warnings))
        self.assertTrue(self._backend("x11").can_detect_foreground)

    def test_missing_tools_message(self):
        with mock.patch("grokclip.clipboard_linux._which", side_effect=lambda n: "/usr/bin/apt-get" if n == "apt-get" else None):
            b = cl.LinuxClipboardBackend(session="x11")
        self.assertIsNone(b.reader)
        self.assertTrue(any("sudo apt install xclip" in w for w in b.warnings))
        self.assertIsNone(b.poll_change())

    def test_poll_signature_baseline_selfwrite_and_secret(self):
        b = self._backend()
        state = {"targets": ["UTF8_STRING"], "text": "a", "hint": None}
        b.list_targets = lambda: list(state["targets"])
        b.read_text = lambda targets=None: state["text"]
        b.read_type = lambda t: state["hint"]
        self.assertIsNone(b.poll_change())            # 启动基线
        self.assertIsNone(b.poll_change())            # 未变化
        state["text"] = "b"
        self.assertEqual(b.poll_change().text, "b")   # 变化
        b._mark_self_write()
        state["text"] = "ours"
        self.assertIsNone(b.poll_change())            # 自写：只更新基准
        self.assertIsNone(b.poll_change())
        state.update(text="pw", targets=["UTF8_STRING", "x-kde-passwordManagerHint"], hint=b"secret")
        self.assertTrue(b.poll_change().sensitive)
        state.update(text="pw2", hint=b"public")
        self.assertFalse(b.poll_change().sensitive)


class DropFilesTests(unittest.TestCase):
    def test_dropfiles_layout(self):
        data = cb.build_dropfiles([r"C:\a\空间.html", r"D:\b.txt"])
        pFiles, x, y, fNC, fWide = struct.unpack("<IiiII", data[:20])
        self.assertEqual((pFiles, x, y, fNC, fWide), (20, 0, 0, 0, 1))
        body = data[20:].decode("utf-16-le")
        self.assertEqual(body, "C:\\a\\空间.html\0D:\\b.txt\0\0")
        self.assertTrue(data.endswith(b"\0\0\0\0"))


# ---------------------------------------------------------------------------
# Windows 后端在 Linux 上的“模拟运行”：用假的 WinDLL 记录每个 API 的 argtypes/restype，
# 并用真实内存模拟 GlobalAlloc/GlobalLock，验证写入剪贴板的字节布局与内存所有权逻辑。
# ---------------------------------------------------------------------------

_UNSET = object()


class FakeFunc(object):
    def __init__(self, name, impl):
        self.__name__ = name
        self.argtypes = _UNSET
        self.restype = _UNSET
        self.impl = impl
        self.calls = 0

    def __call__(self, *args):
        self.calls += 1
        return self.impl(*args)


class FakeWin(object):
    def __init__(self):
        self.mem = {}           # handle -> ctypes buffer
        self.clip = {}          # fmt -> handle
        self.seq = 100
        self.opened = False
        self.freed = []
        self.formats = {}
        self.funcs = {}

    def dll(self, name, use_last_error=False):
        win = self

        class D(object):
            def __getattr__(self, attr):
                key = name + "." + attr
                if key not in win.funcs:
                    impl = getattr(win, attr, None)
                    if impl is None:
                        raise AttributeError(attr)
                    win.funcs[key] = FakeFunc(attr, impl)
                return win.funcs[key]
        return D()

    # user32
    def GetClipboardSequenceNumber(self):
        return self.seq

    def OpenClipboard(self, hwnd):
        if self.opened:
            return 0
        self.opened = True
        return 1

    def CloseClipboard(self):
        self.opened = False
        return 1

    def EmptyClipboard(self):
        assert self.opened
        self.clip.clear()
        self.seq += 1
        return 1

    def SetClipboardData(self, fmt, h):
        assert self.opened and h in self.mem
        self.clip[fmt] = h
        self.seq += 1
        return h

    def GetClipboardData(self, fmt):
        return self.clip.get(fmt)

    def IsClipboardFormatAvailable(self, fmt):
        return 1 if fmt in self.clip else 0

    def RegisterClipboardFormatW(self, name):
        return self.formats.setdefault(name, 0xC000 + len(self.formats))

    def GetForegroundWindow(self):
        return 0x1234

    def GetWindowThreadProcessId(self, hwnd, pid_ref):
        ctypes.cast(pid_ref, ctypes.POINTER(ctypes.c_uint32)).contents.value = 4321
        return 1

    def GetWindowTextLengthW(self, hwnd):
        return len("Grok Bot")

    def GetWindowTextW(self, hwnd, buf, n):
        buf.value = "Grok Bot"[: n - 1]
        return len(buf.value)

    def DragQueryFileW(self, h, i, buf, n):
        files = ["C:\\a.txt", "C:\\b.png"]
        if i == 0xFFFFFFFF:
            return len(files)
        if buf is None:
            return len(files[i])
        buf.value = files[i]
        return len(files[i])

    # kernel32
    def GlobalAlloc(self, flags, size):
        buf = ctypes.create_string_buffer(size)
        h = ctypes.addressof(buf) + 1  # 句柄与指针不同，确保代码没有混用
        self.mem[h] = buf
        return h

    def GlobalLock(self, h):
        return ctypes.addressof(self.mem[h]) if h in self.mem else None

    def GlobalUnlock(self, h):
        return 0

    def GlobalFree(self, h):
        self.freed.append(h)
        return None

    def GlobalSize(self, h):
        return len(self.mem[h]) if h in self.mem else 0

    def OpenProcess(self, access, inherit, pid):
        return 0x99

    def CloseHandle(self, h):
        return 1

    def QueryFullProcessImageNameW(self, hp, flags, buf, size_ref):
        buf.value = "C:\\Program Files\\Grok Bot\\Grok Bot.exe"
        return 1

    def put(self, fmt, data):
        h = self.GlobalAlloc(0, len(data))
        ctypes.memmove(self.GlobalLock(h), data, len(data))
        self.clip[fmt] = h
        self.seq += 1


class WindowsBackendSimulationTests(unittest.TestCase):
    def setUp(self):
        self.win = FakeWin()
        patches = [mock.patch.object(ctypes, "WinDLL", self.win.dll, create=True),
                   mock.patch.object(ctypes, "get_last_error", lambda: 0, create=True)]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        sys.modules.pop("grokclip.clipboard_win", None)
        self.cw = importlib.import_module("grokclip.clipboard_win")
        self.addCleanup(sys.modules.pop, "grokclip.clipboard_win", None)
        self.b = self.cw.WindowsClipboardBackend()

    def test_every_api_has_explicit_pointer_safe_signature(self):
        handle_like = {"HANDLE", "HWND", "LPVOID"}
        for key, f in self.win.funcs.items():
            self.assertIsNot(f.argtypes, _UNSET, key + " 未设置 argtypes")
            self.assertIsNot(f.restype, _UNSET, key + " 未设置 restype")
        ptr = ctypes.sizeof(ctypes.c_void_p)
        for name in ("GlobalAlloc", "GlobalLock", "GlobalFree", "GetClipboardData", "SetClipboardData",
                     "OpenProcess", "GetForegroundWindow"):
            f = self.win.funcs["kernel32." + name] if ("kernel32." + name) in self.win.funcs else self.win.funcs["user32." + name]
            self.assertEqual(ctypes.sizeof(f.restype), ptr, name + " 返回值必须是指针宽度")
        self.assertEqual(ctypes.sizeof(self.win.funcs["kernel32.GlobalSize"].restype), ptr)
        self.assertEqual(ctypes.sizeof(self.win.funcs["kernel32.GlobalAlloc"].argtypes[1]), ptr)  # SIZE_T
        self.assertEqual(ctypes.sizeof(self.win.funcs["user32.SetClipboardData"].argtypes[1]), ptr)
        self.assertTrue(handle_like)

    def test_write_text_then_no_self_capture(self):
        ok, err = self.b.write_text("中文 原文\n第二行")
        self.assertTrue(ok, err)
        raw = bytes(self.win.mem[self.win.clip[13]])
        self.assertEqual(raw, ("中文 原文\n第二行\0").encode("utf-16-le"))
        self.assertFalse(self.win.opened)
        self.assertIsNone(self.b.poll_change())  # 写完立即记录序号

    def test_write_files_cf_hdrop_and_dropeffect(self):
        ok, err = self.b.write_files([r"C:\Users\someone\grok bot\GrokSpace_1.html"])
        self.assertTrue(ok, err)
        data = bytes(self.win.mem[self.win.clip[15]])
        self.assertEqual(data, cb.build_dropfiles([r"C:\Users\someone\grok bot\GrokSpace_1.html"]))
        eff = self.win.formats["Preferred DropEffect"]
        self.assertEqual(struct.unpack("<I", bytes(self.win.mem[self.win.clip[eff]]))[0], 1)
        self.assertEqual(self.win.freed, [])  # 成功后内存归系统，不得释放

    def test_failed_set_frees_memory(self):
        self.win.SetClipboardData = lambda fmt, h: None
        self.win.funcs.pop("user32.SetClipboardData", None)
        sys.modules.pop("grokclip.clipboard_win", None)
        cw = importlib.import_module("grokclip.clipboard_win")
        ok, _ = cw.WindowsClipboardBackend().write_text("x")
        self.assertFalse(ok)
        self.assertEqual(len(self.win.freed), 1)

    def test_poll_reads_text_and_sensitive_formats(self):
        self.assertIsNone(self.b.poll_change())
        self.win.put(13, "复制的原文\0".encode("utf-16-le"))
        snap = self.b.poll_change()
        self.assertEqual((snap.text, snap.sensitive), ("复制的原文", False))
        self.assertIsNone(self.b.poll_change())
        for name, value in (("ExcludeClipboardContentFromMonitorProcessing", b"\0"),
                            ("Clipboard Viewer Ignore", b"\0"),
                            ("CanIncludeInClipboardHistory", struct.pack("<I", 0))):
            self.win.clip.clear()
            self.win.put(13, "pw\0".encode("utf-16-le"))
            self.win.put(self.win.formats[name], value)
            self.assertTrue(self.b.poll_change().sensitive, name)
        self.win.clip.clear()
        self.win.put(13, "ok\0".encode("utf-16-le"))
        self.win.put(self.win.formats["CanIncludeInClipboardHistory"], struct.pack("<I", 1))
        self.assertFalse(self.b.poll_change().sensitive)

    def test_manual_files_and_foreground(self):
        self.win.put(15, b"\0" * 24)
        self.assertEqual(self.b.read_manual(), ("files", ["C:\\a.txt", "C:\\b.png"]))
        fg = self.b.foreground()
        self.assertEqual((fg.pid, fg.title), (4321, "Grok Bot"))
        self.assertTrue(cb.is_grok(fg))
        self.assertEqual(cb.display_name(fg), "Grok Bot")

    def test_clipboard_busy(self):
        self.win.opened = True  # 其他程序占用
        with mock.patch("time.sleep"):
            ok, err = self.b.write_text("x")
        self.assertFalse(ok)
        self.assertIn("占用", err)


if __name__ == "__main__":
    unittest.main()
