"""系统集成：打开文件、在文件管理器中定位、单实例锁、选择剪贴板后端。全部为本机操作。"""
import os
import subprocess
import sys

from . import platform_paths
from .platform_paths import IS_WINDOWS, IS_MAC


def _detached(args):
    try:
        subprocess.Popen(args, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, start_new_session=not IS_WINDOWS)
        return True
    except OSError:
        return False


def open_path(path):
    """用系统默认程序打开（.html → 默认浏览器，目录 → 文件管理器）。"""
    path = os.path.abspath(path)
    if IS_WINDOWS:
        try:
            os.startfile(path)  # noqa: 仅 Windows
            return True
        except OSError:
            return False
    if IS_MAC:
        return _detached(["open", path])
    return _detached(["xdg-open", path])


def reveal_in_file_manager(path):
    path = os.path.abspath(path)
    if IS_WINDOWS:
        try:
            subprocess.Popen('explorer /select,"%s"' % path)
            return True
        except OSError:
            return open_path(os.path.dirname(path))
    if IS_MAC:
        return _detached(["open", "-R", path])
    # freedesktop 标准接口（Nautilus / Dolphin / Nemo / Thunar 等都支持），只走本机 D-Bus
    try:
        from pathlib import Path
        uri = Path(path).as_uri()
        r = subprocess.run(["gdbus", "call", "--session", "--dest", "org.freedesktop.FileManager1",
                            "--object-path", "/org/freedesktop/FileManager1",
                            "--method", "org.freedesktop.FileManager1.ShowItems", "['%s']" % uri, ""],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=3)
        if r.returncode == 0:
            return True
    except (OSError, subprocess.SubprocessError):
        pass
    return open_path(os.path.dirname(path))


def make_clipboard_backend():
    from .clipboard_base import NullBackend
    if IS_WINDOWS:
        try:
            from .clipboard_win import WindowsClipboardBackend
            return WindowsClipboardBackend()
        except Exception as e:  # pragma: no cover
            return NullBackend("无法初始化 Windows 剪贴板：%s" % e)
    if IS_MAC:
        return NullBackend("macOS 请使用原生版（项目根目录 bash install.sh），Python 版在 macOS 上不监听剪贴板。")
    from .clipboard_linux import LinuxClipboardBackend
    return LinuxClipboardBackend()


class SingleInstance(object):
    """数据目录下的锁文件：同一用户只运行一个收集端。"""

    def __init__(self, name="instance.lock"):
        self.path = os.path.join(str(platform_paths.data_dir()), name)
        self.fh = None

    def acquire(self):
        try:
            self.fh = open(self.path, "a+")
        except OSError:
            return True  # 无法创建锁文件时不阻止启动
        try:
            if IS_WINDOWS:
                import msvcrt
                self.fh.seek(0)
                msvcrt.locking(self.fh.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        except OSError:
            self.fh.close()
            self.fh = None
            return False


def log_error(message):
    """只记录程序错误，绝不记录剪贴板内容。pythonw 下没有 stderr，写入数据目录的 error.log。"""
    try:
        if sys.stderr is not None:
            sys.stderr.write("[GrokClip] %s\n" % message)
    except Exception:
        pass
    try:
        p = os.path.join(str(platform_paths.data_dir()), "error.log")
        if os.path.exists(p) and os.path.getsize(p) > 256 * 1024:
            os.replace(p, p + ".1")
        with open(p, "a", encoding="utf-8") as f:
            import time
            f.write("%s %s\n" % (time.strftime("%Y-%m-%d %H:%M:%S"), message))
    except Exception:
        pass
