"""入口：
    python -m grokclip                         启动悬浮球 + 收集篮
    python -m grokclip --self-test out.html    不启动界面，生成示例空间并自检
    python -m grokclip --version
"""
import os
import sys
import tempfile


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    from . import __version__
    if "--version" in argv:
        print("GrokClip Space (Python) %s" % __version__)
        return 0
    if "--self-test" in argv:
        from . import selftest
        i = argv.index("--self-test")
        out = argv[i + 1] if i + 1 < len(argv) else os.path.join(tempfile.gettempdir(), "GrokSpace_selftest.html")
        return selftest.run(out)
    try:
        import tkinter  # noqa: F401
    except ImportError:
        msg = ("未找到 Tkinter。\nWindows：请从 python.org 安装 Python（勾选 tcl/tk）。\n"
               "Linux：Debian/Ubuntu 运行 sudo apt install python3-tk；Fedora 运行 sudo dnf install python3-tkinter；"
               "Arch 运行 sudo pacman -S tk。")
        sys.stderr.write(msg + "\n") if sys.stderr else None
        return 2
    from . import system
    lock = system.SingleInstance()
    if not lock.acquire():
        if sys.stderr:
            sys.stderr.write("GrokClip Space 已在运行。\n")
        return 0
    from .app_ui import run_app
    return run_app()


if __name__ == "__main__":
    sys.exit(main())
