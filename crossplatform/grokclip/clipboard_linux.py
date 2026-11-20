"""Linux 剪贴板后端（X11：xclip / xsel；Wayland：wl-clipboard），只通过 subprocess 调用本机命令。

- 变化检测：每次轮询读取 TARGETS + 文本，按内容签名判断是否变化
- 敏感内容：KeePassXC / KDE 等密码管理器写入 x-kde-passwordManagerHint=secret 时跳过
- 前台应用（仅 X11）：xdotool getactivewindow getwindowpid → /proc/<pid>/comm；退回 xprop
  Wayland 出于安全设计不允许读取其他应用窗口 → 自动按「全部应用」收录并在界面上显示提醒
- 写文件：text/uri-list + x-special/gnome-copied-files（X11 下由本程序的 Tk 窗口持有剪贴板，
  可同时提供多种格式）；Wayland 下用 wl-copy --type text/uri-list
"""
import os
import shutil
import subprocess
import threading
from pathlib import Path

from .clipboard_base import Foreground, Snapshot
from . import models

TIMEOUT = 2.0
SENSITIVE_HINT = "x-kde-passwordManagerHint"


def _which(name):
    return shutil.which(name)


def _run(args, input_bytes=None, timeout=TIMEOUT):
    """运行命令并返回 (returncode, stdout_bytes)。永不抛异常。"""
    try:
        r = subprocess.run(args, input=input_bytes, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                           timeout=timeout)
        return r.returncode, r.stdout
    except (OSError, subprocess.SubprocessError):
        return -1, b""


def _spawn_writer(args, data):
    """xclip / wl-copy 会转入后台继续持有剪贴板：不能接管它的 stdout，否则会一直等待。"""
    try:
        p = subprocess.Popen(args, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL, start_new_session=True)
        p.communicate(data, timeout=TIMEOUT)
        return p.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def file_uri(path):
    return Path(os.path.abspath(path)).as_uri()


def uri_list(paths):
    return "".join(file_uri(p) + "\r\n" for p in paths)


def gnome_copied_files(paths):
    return "copy\n" + "\n".join(file_uri(p) for p in paths)


def parse_uri_list(text):
    from urllib.parse import unquote, urlparse
    out = []
    for line in (text or "").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or line in ("copy", "cut"):
            continue
        u = urlparse(line)
        if u.scheme == "file":
            out.append(unquote(u.path))
    return out


def detect_session():
    if os.environ.get("WAYLAND_DISPLAY") or os.environ.get("XDG_SESSION_TYPE") == "wayland":
        return "wayland"
    if os.environ.get("DISPLAY"):
        return "x11"
    return "none"


def install_hint(packages):
    """根据发行版给出安装命令（只打印，不执行 sudo）。"""
    apt = {"xclip": "xclip", "xsel": "xsel", "xdotool": "xdotool", "wl-clipboard": "wl-clipboard",
           "tk": "python3-tk"}
    dnf = {"xclip": "xclip", "xsel": "xsel", "xdotool": "xdotool", "wl-clipboard": "wl-clipboard",
           "tk": "python3-tkinter"}
    pac = {"xclip": "xclip", "xsel": "xsel", "xdotool": "xdotool", "wl-clipboard": "wl-clipboard", "tk": "tk"}
    if _which("apt-get"):
        return "sudo apt install " + " ".join(apt[p] for p in packages)
    if _which("dnf"):
        return "sudo dnf install " + " ".join(dnf[p] for p in packages)
    if _which("pacman"):
        return "sudo pacman -S " + " ".join(pac[p] for p in packages)
    if _which("zypper"):
        return "sudo zypper install " + " ".join(dnf[p] for p in packages)
    return "请用系统包管理器安装：" + " ".join(packages)


class LinuxClipboardBackend(object):
    def __init__(self, session=None):
        self.session = session or detect_session()
        self.warnings = []
        self.tk_root = None
        self._gen = 0                 # 每次自写 +1
        self._seen_gen = 0
        self._last_sig = None
        self._lock = threading.Lock()
        self.reader = None
        self.fg_tool = None
        override = os.environ.get("GROKCLIP_CLIPBOARD", "").strip().lower()

        if self.session == "wayland":
            if _which("wl-paste") and _which("wl-copy") and override in ("", "wl", "wayland"):
                self.reader = "wl"
            elif _which("xclip") and override in ("", "xclip"):
                self.reader = "xclip"
                self.warnings.append("未找到 wl-clipboard，暂用 xclip（仅能读到 XWayland 剪贴板）。建议安装：" +
                                     install_hint(["wl-clipboard"]))
            else:
                self.warnings.append("缺少剪贴板工具，无法自动收录。请安装：" + install_hint(["wl-clipboard"]))
            self.warnings.append("Wayland 不允许读取前台应用，已自动按「全部应用」收录；"
                                 "复制密码前可先在收集篮暂停监听。")
        elif self.session == "x11":
            if _which("xclip") and override in ("", "xclip"):
                self.reader = "xclip"
            elif _which("xsel") and override in ("", "xsel"):
                self.reader = "xsel"
                self.warnings.append("xsel 无法识别密码管理器标记，建议安装 xclip：" + install_hint(["xclip"]))
            else:
                self.warnings.append("缺少剪贴板工具，无法自动收录。请安装：" + install_hint(["xclip"]))
            if _which("xdotool"):
                self.fg_tool = "xdotool"
            elif _which("xprop"):
                self.fg_tool = "xprop"
            else:
                self.warnings.append("缺少 xdotool，无法判断前台应用，已自动按「全部应用」收录。请安装：" +
                                     install_hint(["xdotool"]))
        else:
            self.warnings.append("未检测到图形会话（DISPLAY / WAYLAND_DISPLAY），剪贴板不可用。")

        self.name = self.reader or "none"

    @property
    def can_detect_foreground(self):
        return self.session == "x11" and self.fg_tool is not None

    def attach_tk(self, root):
        self.tk_root = root

    # ---------------- low-level reads ----------------

    def list_targets(self):
        if self.reader == "xclip":
            rc, out = _run(["xclip", "-o", "-selection", "clipboard", "-t", "TARGETS"])
        elif self.reader == "wl":
            rc, out = _run(["wl-paste", "--list-types"])
        else:
            return None  # xsel 不支持列出格式
        if rc != 0:
            return []
        return [t.strip() for t in out.decode("utf-8", "replace").splitlines() if t.strip()]

    def read_type(self, mime):
        if self.reader == "xclip":
            rc, out = _run(["xclip", "-o", "-selection", "clipboard", "-t", mime])
        elif self.reader == "wl":
            rc, out = _run(["wl-paste", "--no-newline", "--type", mime])
        else:
            return None
        return out if rc == 0 else None

    def read_text(self, targets=None):
        if self.reader == "xsel":
            rc, out = _run(["xsel", "--clipboard", "--output"])
            return out.decode("utf-8", "replace") if rc == 0 else None
        if targets is not None:
            for t in ("UTF8_STRING", "text/plain;charset=utf-8", "text/plain", "STRING", "TEXT"):
                if t in targets:
                    data = self.read_type(t)
                    if data is not None:
                        return data.decode("utf-8", "replace")
            return None
        if self.reader == "xclip":
            rc, out = _run(["xclip", "-o", "-selection", "clipboard"])
        elif self.reader == "wl":
            rc, out = _run(["wl-paste", "--no-newline"])
        else:
            return None
        return out.decode("utf-8", "replace") if rc == 0 else None

    def is_sensitive(self, targets):
        if not targets or SENSITIVE_HINT not in targets:
            return False
        data = self.read_type(SENSITIVE_HINT)
        return data is not None and data.strip().lower() == b"secret"

    # ---------------- monitor API ----------------

    def poll_change(self):
        if self.reader is None:
            return None
        with self._lock:
            gen = self._gen
        targets = self.list_targets()
        text = self.read_text(targets)
        sig = models.sha256("\x1f".join(targets or []) + "\x00" + (text or ""))
        with self._lock:
            if gen != self._gen or self._seen_gen != self._gen:
                # 读取期间或之前发生了自写：只更新基准，不收录
                self._seen_gen = self._gen
                self._last_sig = sig
                return None
            if sig == self._last_sig:
                return None
            first = self._last_sig is None
            self._last_sig = sig
        if first:
            return None  # 启动时已有的内容不收录
        if self.is_sensitive(targets):
            return Snapshot(None, sensitive=True)
        return Snapshot(text)

    def read_manual(self):
        if self.reader is None:
            return ("error", self.warnings[0] if self.warnings else "剪贴板不可用")
        targets = self.list_targets()
        if self.is_sensitive(targets):
            return ("sensitive",)
        if targets:
            for t in ("text/uri-list", "x-special/gnome-copied-files"):
                if t in targets:
                    data = self.read_type(t)
                    files = [p for p in parse_uri_list((data or b"").decode("utf-8", "replace"))
                             if os.path.isfile(p)]
                    if files:
                        return ("files", files)
        text = self.read_text(targets)
        if text is not None and text.strip():
            return ("text", text)
        return ("empty",)

    def foreground(self):
        pid, title = 0, ""
        if self.fg_tool == "xdotool":
            rc, out = _run(["xdotool", "getactivewindow", "getwindowpid", "getwindowname"])
            if rc == 0:
                lines = out.decode("utf-8", "replace").splitlines()
                if lines and lines[0].strip().isdigit():
                    pid = int(lines[0].strip())
                title = lines[1] if len(lines) > 1 else ""
            elif rc != 0:
                rc2, out2 = _run(["xdotool", "getactivewindow", "getwindowname"])
                title = out2.decode("utf-8", "replace").strip() if rc2 == 0 else ""
        elif self.fg_tool == "xprop":
            rc, out = _run(["xprop", "-root", "_NET_ACTIVE_WINDOW"])
            wid = out.decode("ascii", "replace").strip().split()[-1] if rc == 0 and out.strip() else ""
            if wid.startswith("0x") and wid != "0x0":
                rc, out = _run(["xprop", "-id", wid, "_NET_WM_PID", "_NET_WM_NAME"])
                for line in out.decode("utf-8", "replace").splitlines():
                    if line.startswith("_NET_WM_PID") and "=" in line:
                        v = line.split("=", 1)[1].strip()
                        pid = int(v) if v.isdigit() else 0
                    elif line.startswith("_NET_WM_NAME") and "=" in line:
                        title = line.split("=", 1)[1].strip().strip('"')
        else:
            return None
        comm = ""
        if pid:
            try:
                with open("/proc/%d/comm" % pid, "r", encoding="utf-8", errors="replace") as f:
                    comm = f.read().strip()
            except OSError:
                comm = ""
        if not pid and not title:
            return None
        return Foreground(process=comm, title=title, pid=pid)

    # ---------------- writes (UI thread) ----------------

    def _mark_self_write(self):
        with self._lock:
            self._gen += 1

    def write_text(self, text):
        self._mark_self_write()
        if self.session == "x11" and self.tk_root is not None:
            try:
                self.tk_root.clipboard_clear()
                self.tk_root.clipboard_append(text)
                self.tk_root.update_idletasks()
                self._mark_self_write()
                return True, ""
            except Exception:
                pass
        data = text.encode("utf-8")
        if self.session == "wayland" and _which("wl-copy"):
            ok = _spawn_writer(["wl-copy", "--type", "text/plain;charset=utf-8"], data)
        elif _which("xclip"):
            ok = _spawn_writer(["xclip", "-selection", "clipboard", "-i"], data)
        elif _which("xsel"):
            ok = _spawn_writer(["xsel", "--clipboard", "--input"], data)
        else:
            ok = False
        self._mark_self_write()
        return (True, "") if ok else (False, "写入剪贴板失败：缺少 xclip / wl-clipboard")

    def write_files(self, paths):
        paths = [os.path.abspath(str(p)) for p in paths]
        self._mark_self_write()
        uris = uri_list(paths)
        if self.session == "x11" and self.tk_root is not None:
            try:
                r = self.tk_root
                r.clipboard_clear()
                r.clipboard_append(uris, type="text/uri-list")
                r.clipboard_append(gnome_copied_files(paths), type="x-special/gnome-copied-files")
                r.update_idletasks()
                self._mark_self_write()
                return True, ""
            except Exception:
                pass
        if self.session == "wayland" and _which("wl-copy"):
            ok = _spawn_writer(["wl-copy", "--type", "text/uri-list"], uris.encode("utf-8"))
        elif _which("xclip"):
            ok = _spawn_writer(["xclip", "-selection", "clipboard", "-t", "text/uri-list", "-i"],
                               uris.encode("utf-8"))
        else:
            ok = False
        self._mark_self_write()
        return (True, "") if ok else (False, "当前环境无法以文件形式写入剪贴板，请用「打开所在文件夹」拖拽发送")
