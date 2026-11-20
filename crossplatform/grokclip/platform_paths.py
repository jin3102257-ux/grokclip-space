"""运行时解析各平台目录。源码中不含任何个人用户名或固定路径。"""
import os
import subprocess
import sys
from pathlib import Path

IS_WINDOWS = sys.platform.startswith("win")
IS_MAC = sys.platform == "darwin"
IS_LINUX = not IS_WINDOWS and not IS_MAC

APP_DIR_NAME = "GrokClipSpace"


def home():
    return Path.home()


# ---------------- Windows Known Folders ----------------

def _win_known_folder(guid_text):
    """SHGetKnownFolderPath；失败返回 None。"""
    if not IS_WINDOWS:
        return None
    try:
        import ctypes
        from ctypes import wintypes
        import uuid

        class GUID(ctypes.Structure):
            _fields_ = [("Data1", wintypes.DWORD), ("Data2", wintypes.WORD),
                        ("Data3", wintypes.WORD), ("Data4", ctypes.c_ubyte * 8)]

        u = uuid.UUID(guid_text)
        g = GUID()
        g.Data1, g.Data2, g.Data3 = u.fields[0], u.fields[1], u.fields[2]
        for i, b in enumerate(u.bytes[8:]):
            g.Data4[i] = b

        shell32 = ctypes.WinDLL("shell32", use_last_error=True)
        ole32 = ctypes.WinDLL("ole32", use_last_error=True)
        fn = shell32.SHGetKnownFolderPath
        fn.argtypes = [ctypes.POINTER(GUID), wintypes.DWORD, wintypes.HANDLE, ctypes.POINTER(ctypes.c_void_p)]
        fn.restype = ctypes.c_long  # HRESULT
        free = ole32.CoTaskMemFree
        free.argtypes = [ctypes.c_void_p]
        free.restype = None

        out = ctypes.c_void_p()  # PWSTR，由系统分配，必须 CoTaskMemFree
        hr = fn(ctypes.byref(g), 0, None, ctypes.byref(out))
        try:
            if hr != 0 or not out.value:
                return None
            text = ctypes.wstring_at(out.value)
            return Path(text) if text else None
        finally:
            if out.value:
                free(out.value)
    except Exception:
        return None


FOLDERID_DOWNLOADS = "374DE290-123F-4565-9164-39C4925E467B"
FOLDERID_DESKTOP = "B4BFCC3A-DB2C-424C-B029-7FE99A87C641"


# ---------------- Linux XDG user dirs ----------------

def _xdg_user_dir(name):
    """xdg-user-dir DOWNLOAD / DESKTOP；退回解析 ~/.config/user-dirs.dirs。"""
    if not IS_LINUX:
        return None
    h = str(home())
    try:
        r = subprocess.run(["xdg-user-dir", name], capture_output=True, text=True, timeout=2)
        p = r.stdout.strip()
        if r.returncode == 0 and p and os.path.normpath(p) != os.path.normpath(h):
            return Path(p)
    except (OSError, subprocess.SubprocessError):
        pass
    cfg = Path(os.environ.get("XDG_CONFIG_HOME") or os.path.join(h, ".config")) / "user-dirs.dirs"
    try:
        for line in cfg.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            key = "XDG_%s_DIR=" % name
            if line.startswith(key):
                val = line[len(key):].strip().strip('"').replace("$HOME", h)
                if val and os.path.normpath(val) != os.path.normpath(h):
                    return Path(val)
    except OSError:
        pass
    return None


def downloads_dir():
    p = None
    if IS_WINDOWS:
        p = _win_known_folder(FOLDERID_DOWNLOADS)
    elif IS_LINUX:
        p = _xdg_user_dir("DOWNLOAD")
    return p or (home() / "Downloads")


def desktop_dir():
    p = None
    if IS_WINDOWS:
        p = _win_known_folder(FOLDERID_DESKTOP)
    elif IS_LINUX:
        p = _xdg_user_dir("DESKTOP")
    return p or (home() / "Desktop")


def grok_bot_dir():
    """`~/grok bot`（仅当该目录真实存在时返回）。"""
    p = home() / "grok bot"
    return p if p.is_dir() else None


def data_dir(create=True):
    """数据目录：Windows %APPDATA%\\GrokClipSpace；Linux $XDG_DATA_HOME/GrokClipSpace。
    可用环境变量 GROKCLIP_DATA_DIR 覆盖（用于测试或便携模式）。"""
    override = os.environ.get("GROKCLIP_DATA_DIR")
    if override:
        d = Path(override)
    elif IS_WINDOWS:
        base = os.environ.get("APPDATA") or str(home() / "AppData" / "Roaming")
        d = Path(base) / APP_DIR_NAME
    elif IS_MAC:
        # 与 Swift 版分开存放，避免两个程序同时写同一个文件
        d = home() / "Library" / "Application Support" / (APP_DIR_NAME + "-Python")
    else:
        base = os.environ.get("XDG_DATA_HOME") or str(home() / ".local" / "share")
        d = Path(base) / APP_DIR_NAME
    if create:
        try:
            d.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass
    return d


def default_watched_directories():
    dirs = [str(downloads_dir())]
    g = grok_bot_dir()
    if g is not None:
        dirs.append(str(g))
    return dirs


def default_export_directory():
    g = grok_bot_dir()
    if g is not None:
        return str(g)
    desk = desktop_dir()
    if desk.is_dir():
        return str(desk / "GrokSpaces")
    return str(home() / "GrokSpaces")
