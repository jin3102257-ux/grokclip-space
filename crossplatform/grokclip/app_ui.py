"""Tkinter 界面：悬浮球（置顶、无边框、可拖拽）+ 收集篮抽屉 + 设置 + Toast。

线程模型：剪贴板监听与文件监听各自在后台线程运行，只往队列里放事件；
所有 Tk 调用与 ClipStore 读写都在主线程完成（root.after 定时取队列）。
"""
import json
import os
import queue
import sys
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from . import APP_NAME, __version__, models, platform_paths, space_html, system
from .clipboard_base import ClipboardMonitor
from .models import SCOPE_NAMES, CAPTURE_SCOPES
from .store import ClipStore
from .watcher import DownloadWatcher

IS_WINDOWS = platform_paths.IS_WINDOWS

ACCENT = "#5b4bdb"
ACCENT_2 = "#8b5cf6"
ACCENT_SOFT = "#ece9fb"
TEXT = "#1f1d1a"
MUTED = "#7a756c"
LINE = "#e8e3d9"
BG = "#f7f5f0"
PANEL = "#ffffff"
OK = "#16a34a"
DANGER = "#dc2626"
ORB_KEY = "#ff00fe"  # Windows 透明色键

# 可选拖放支持：仅当用户自行安装了 tkinterdnd2 时启用（不是必需依赖）
try:  # pragma: no cover
    from tkinterdnd2 import DND_FILES, TkinterDnD  # type: ignore
    HAS_DND = True
except Exception:  # pragma: no cover
    HAS_DND = False


def tk_safe(s):
    """Tcl 8.6 不支持 BMP 以外的字符（如 emoji），仅在界面显示时替换，原文不受影响。"""
    if not s:
        return ""
    return "".join(c if ord(c) <= 0xFFFF else "\ufffd" for c in s)


def preview_text(raw, max_lines=3, max_chars=180):
    lines = [ln.rstrip() for ln in raw.split("\n") if ln.strip()]
    out = "\n".join(lines[:max_lines])
    if len(out) > max_chars:
        out = out[:max_chars].rstrip() + "…"
    elif len(lines) > max_lines:
        out += " …"
    return tk_safe(out)


def _enable_dpi_awareness():
    if not IS_WINDOWS:
        return
    try:
        import ctypes
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        try:
            import ctypes
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass


def _make_no_activate(win):
    """Windows：悬浮球与 Toast 点击时不抢焦点、不出现在任务栏（WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW）。"""
    if not IS_WINDOWS:
        return
    try:
        import ctypes
        from ctypes import wintypes
        win.update_idletasks()
        hwnd = int(win.wm_frame(), 16)
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        GWL_EXSTYLE = -20
        LONG_PTR = ctypes.c_ssize_t
        if ctypes.sizeof(ctypes.c_void_p) == 8:
            get_ = user32.GetWindowLongPtrW
            set_ = user32.SetWindowLongPtrW
        else:
            get_ = user32.GetWindowLongW
            set_ = user32.SetWindowLongW
        get_.argtypes = [wintypes.HWND, ctypes.c_int]
        get_.restype = LONG_PTR
        set_.argtypes = [wintypes.HWND, ctypes.c_int, LONG_PTR]
        set_.restype = LONG_PTR
        style = get_(hwnd, GWL_EXSTYLE)
        set_(hwnd, GWL_EXSTYLE, style | 0x08000000 | 0x00000080)
    except Exception:
        pass


def _pick_font_family(root):
    try:
        from tkinter import font as tkfont
        fams = set(tkfont.families(root))
    except Exception:
        return None
    for f in ("Microsoft YaHei UI", "Microsoft YaHei", "PingFang SC", "Noto Sans CJK SC", "Noto Sans SC",
              "Source Han Sans SC", "WenQuanYi Micro Hei", "WenQuanYi Zen Hei", "Droid Sans Fallback",
              "Segoe UI", "DejaVu Sans"):
        if f in fams:
            return f
    return None


class App(object):
    def __init__(self):
        _enable_dpi_awareness()
        self.root = TkinterDnD.Tk() if HAS_DND else tk.Tk()
        self.root.withdraw()
        self.root.title(APP_NAME)
        self.scale = max(1.0, self.root.winfo_fpixels("1i") / 96.0)
        fam = _pick_font_family(self.root)
        self.font = (fam, 10) if fam else ("TkDefaultFont", 10)
        self.font_small = (fam, 9) if fam else ("TkDefaultFont", 9)
        self.font_bold = (fam, 10, "bold") if fam else ("TkDefaultFont", 10, "bold")
        self.font_title = (fam, 13, "bold") if fam else ("TkDefaultFont", 13, "bold")
        self._init_styles()

        self.ui_state_path = os.path.join(str(platform_paths.data_dir()), "ui_state.json")
        self.ui_state = models.load_json(self.ui_state_path) or {}

        self.store = ClipStore()
        self.backend = system.make_clipboard_backend()
        self.backend.attach_tk(self.root)
        self.monitor = ClipboardMonitor(self.backend, interval=0.4 if IS_WINDOWS else 0.5)
        self.files_q = queue.Queue()
        self.export_q = queue.Queue()
        self.watcher = DownloadWatcher(on_new_file=self.files_q.put, interval=1.0)
        self.store.on_event = self._on_store_event
        self.store.on_dedup_reset = self.monitor.reset_dedup
        self.store.on_settings_changed = self._apply_settings

        self.drawer = None
        self.settings_win = None
        self.toast_win = None
        self._toast_job = None
        self._refresh_pending = False
        self._exporting = False
        self._pulse = 0

        self._build_orb()
        self._build_menu()
        self._apply_settings(True)
        self.monitor.start()
        self.watcher.start()
        self.root.after(120, self._drain)
        self.root.protocol("WM_DELETE_WINDOW", self.quit)
        if self.backend.warnings:
            self.root.after(800, lambda: self.toast(self.backend.warnings[0], ms=5200))

    # ------------------------------------------------------------------ styles

    def _init_styles(self):
        st = ttk.Style(self.root)
        try:
            if "clam" in st.theme_names() and not IS_WINDOWS:
                st.theme_use("clam")
        except tk.TclError:
            pass
        st.configure(".", font=self.font)
        st.configure("TFrame", background=BG)
        st.configure("Card.TFrame", background=PANEL)
        st.configure("TLabel", background=BG, foreground=TEXT)
        st.configure("Card.TLabel", background=PANEL, foreground=TEXT)
        st.configure("Muted.TLabel", background=BG, foreground=MUTED, font=self.font_small)
        st.configure("CardMuted.TLabel", background=PANEL, foreground=MUTED, font=self.font_small)
        st.configure("Badge.TLabel", background=PANEL, foreground=ACCENT, font=self.font_bold)
        st.configure("Title.TLabel", background=BG, foreground=TEXT, font=self.font_title)
        st.configure("Warn.TLabel", background="#fff7e6", foreground="#9a5b00", font=self.font_small)
        st.configure("Ok.TLabel", background=ACCENT_SOFT, foreground=TEXT, font=self.font_small)
        st.configure("TCheckbutton", background=BG)
        st.configure("Card.TCheckbutton", background=PANEL)
        st.configure("Small.TButton", font=self.font_small, padding=(4, 1))
        st.configure("Accent.TButton", font=self.font_bold, foreground="#ffffff", background=ACCENT,
                     padding=(10, 6))
        st.map("Accent.TButton", background=[("active", ACCENT_2), ("disabled", "#b9b2ee")],
               foreground=[("disabled", "#f2f0ff")])

    # ------------------------------------------------------------------ orb

    def _px(self, v):
        return int(round(v * self.scale))

    def _build_orb(self):
        size = self._px(76)
        self.orb_size = size
        w = tk.Toplevel(self.root)
        w.overrideredirect(True)
        w.attributes("-topmost", True)
        bg = ORB_KEY if IS_WINDOWS else "#2a2540"
        w.configure(bg=bg)
        if IS_WINDOWS:
            try:
                w.attributes("-transparentcolor", ORB_KEY)
            except tk.TclError:
                pass
        c = tk.Canvas(w, width=size, height=size, bg=bg, highlightthickness=0, bd=0, cursor="hand2")
        c.pack()
        self.orb, self.orb_canvas = w, c
        sw, sh = w.winfo_screenwidth(), w.winfo_screenheight()
        x = self.ui_state.get("orbX")
        y = self.ui_state.get("orbY")
        if not isinstance(x, int) or not isinstance(y, int):
            x, y = sw - size - self._px(24), int(sh * 0.38)
        x = min(max(0, x), max(0, sw - size))
        y = min(max(0, y), max(0, sh - size))
        w.geometry("%dx%d+%d+%d" % (size, size, x, y))
        c.bind("<ButtonPress-1>", self._orb_press)
        c.bind("<B1-Motion>", self._orb_motion)
        c.bind("<ButtonRelease-1>", self._orb_release)
        c.bind("<Button-3>", self._orb_menu)
        if sys.platform == "darwin":
            c.bind("<Button-2>", self._orb_menu)
        if HAS_DND:  # pragma: no cover
            try:
                c.drop_target_register(DND_FILES)
                c.dnd_bind("<<Drop>>", lambda e: self._on_drop(e, None))
            except Exception:
                pass
        self._draw_orb()
        _make_no_activate(w)

    def _draw_orb(self):
        c = self.orb_canvas
        c.delete("all")
        s = self.orb_size
        pad = self._px(8)
        r0 = s - 2 * pad
        if self._pulse:
            grow = self._px(self._pulse)
            c.create_oval(pad - grow, pad - grow, s - pad + grow, s - pad + grow, outline=ACCENT_2,
                          width=max(1, self._px(2)))
        steps = 10
        c1 = (0x4f, 0x46, 0xe5)
        c2 = (0x9b, 0x66, 0xfa)
        for i in range(steps):
            t = i / float(steps - 1)
            col = "#%02x%02x%02x" % tuple(int(c1[k] + (c2[k] - c1[k]) * t) for k in range(3))
            inset = int(r0 * 0.5 * t * 0.85)
            c.create_oval(pad + inset * 0.6, pad + inset * 0.4, s - pad - inset * 1.0, s - pad - inset * 1.2,
                          fill=col, outline=col)
        c.create_oval(pad, pad, s - pad, s - pad, outline="#ffffff", width=max(1, self._px(1.5)))
        c.create_text(s / 2, s / 2, text="✦", fill="#ffffff",
                      font=(self.font[0], int(18 * 1), "bold"))
        n = len(self.store.items)
        if n:
            br = self._px(11)
            cx, cy = s - pad - self._px(3), pad + self._px(3)
            c.create_oval(cx - br, cy - br, cx + br, cy + br, fill=DANGER, outline="#ffffff",
                          width=max(1, self._px(1.5)))
            c.create_text(cx, cy, text=str(n) if n < 100 else "99+", fill="#ffffff",
                          font=(self.font[0], 8, "bold"))
        dr = self._px(5)
        dx, dy = s - pad - self._px(5), s - pad - self._px(5)
        on = self.store.settings.is_listening and getattr(self.backend, "reader", True) is not None
        c.create_oval(dx - dr, dy - dr, dx + dr, dy + dr, fill=OK if on else "#9ca3af", outline="#ffffff",
                      width=max(1, self._px(1.5)))

    def _pulse_orb(self, step=0):
        seq = [2, 4, 6, 4, 2, 0]
        if step < len(seq):
            self._pulse = seq[step]
            self._draw_orb()
            self.root.after(45, lambda: self._pulse_orb(step + 1))

    def _orb_press(self, e):
        self._drag = {"x": e.x_root, "y": e.y_root, "wx": self.orb.winfo_x(), "wy": self.orb.winfo_y(),
                      "moved": False}

    def _orb_motion(self, e):
        d = getattr(self, "_drag", None)
        if not d:
            return
        dx, dy = e.x_root - d["x"], e.y_root - d["y"]
        if not d["moved"] and abs(dx) < 5 and abs(dy) < 5:
            return
        d["moved"] = True
        self.orb.geometry("+%d+%d" % (d["wx"] + dx, d["wy"] + dy))

    def _orb_release(self, e):
        d = getattr(self, "_drag", None)
        self._drag = None
        if d and d["moved"]:
            self.ui_state["orbX"], self.ui_state["orbY"] = self.orb.winfo_x(), self.orb.winfo_y()
            self._save_ui_state()
        else:
            self.toggle_drawer()

    def _save_ui_state(self):
        try:
            models.atomic_write_json(self.ui_state_path, self.ui_state)
        except OSError:
            pass

    def _build_menu(self):
        m = tk.Menu(self.root, tearoff=0)
        self.scope_var = tk.StringVar(value=self.store.settings.capture_scope)
        m.add_command(label="打开收集篮", command=self.show_drawer)
        m.add_command(label="暂停监听", command=self.store.toggle_listening)
        sub = tk.Menu(m, tearoff=0)
        for s in CAPTURE_SCOPES:
            sub.add_radiobutton(label=SCOPE_NAMES[s], value=s, variable=self.scope_var,
                                command=lambda s=s: self.store.set_capture_scope(s))
        m.add_cascade(label="收录范围", menu=sub)
        m.add_separator()
        m.add_command(label="粘贴收录（当前剪贴板）", command=self.manual_capture)
        m.add_command(label="一键生成空间并复制", command=self.generate_space)
        m.add_command(label="复制纯原文", command=self.copy_raw)
        m.add_command(label="打开导出目录", command=self.open_export_dir)
        m.add_separator()
        m.add_command(label="设置…", command=self.show_settings)
        m.add_command(label="退出 GrokClip Space", command=self.quit)
        self.menu = m

    def _orb_menu(self, e):
        self.menu.entryconfigure(1, label="继续监听" if not self.store.settings.is_listening else "暂停监听")
        self.scope_var.set(self.store.settings.capture_scope)
        try:
            self.menu.tk_popup(e.x_root, e.y_root)
        finally:
            self.menu.grab_release()

    # ------------------------------------------------------------------ toast

    def toast(self, message, ms=1900, kind="info"):
        if self.toast_win is not None:
            try:
                self.toast_win.destroy()
            except tk.TclError:
                pass
        if self._toast_job:
            self.root.after_cancel(self._toast_job)
        t = tk.Toplevel(self.root)
        t.overrideredirect(True)
        t.attributes("-topmost", True)
        bg = {"error": "#7f1d1d", "ok": "#1f2937"}.get(kind, "#1f2937")
        t.configure(bg=bg)
        tk.Label(t, text=tk_safe(message), bg=bg, fg="#ffffff", font=self.font, wraplength=self._px(300),
                 justify="left", padx=self._px(12), pady=self._px(7)).pack()
        t.update_idletasks()
        tw, th = t.winfo_reqwidth(), t.winfo_reqheight()
        ox, oy = self.orb.winfo_x(), self.orb.winfo_y()
        sw = t.winfo_screenwidth()
        x = ox - tw - self._px(8) if ox - tw - self._px(8) > 0 else min(ox + self.orb_size + self._px(8), sw - tw)
        y = oy + (self.orb_size - th) // 2
        t.geometry("+%d+%d" % (max(0, x), max(0, y)))
        _make_no_activate(t)
        self.toast_win = t
        self._toast_job = self.root.after(ms, self._hide_toast)

    def _hide_toast(self):
        self._toast_job = None
        if self.toast_win is not None:
            try:
                self.toast_win.destroy()
            except tk.TclError:
                pass
            self.toast_win = None

    # ------------------------------------------------------------------ events

    def _apply_settings(self, reload_watcher):
        s = self.store.settings
        self.monitor.capture_scope = s.capture_scope
        self.monitor.is_listening = s.is_listening
        if reload_watcher:
            self.watcher.set_directories(s.watched_directories if s.is_listening else [])
        self.schedule_refresh()

    def _on_store_event(self, kind, p):
        if kind == "captured":
            msg = "+1 已收录 #%02d" % p["index"]
            if p.get("auto_attached"):
                msg += " · 自动挂载 %d 个文件" % p["auto_attached"]
            self.toast(msg)
            self._pulse_orb()
        elif kind == "attached":
            more = " 等 %d 个" % p["count"] if p.get("count", 1) > 1 else ""
            self.toast("已挂载：%s%s" % (p["file_name"], more))
            self._pulse_orb()
        elif kind == "pending":
            self.toast("新文件：%s\n下一条复制的原文会自动认领，也可在收集篮里手动挂载" % p["file_name"], ms=2600)
        elif kind in ("info", "error"):
            self.toast(p.get("message", ""), kind=kind)
        self.schedule_refresh()

    def _drain(self):
        try:
            while True:
                try:
                    ev = self.monitor.events.get_nowait()
                except queue.Empty:
                    break
                if ev[0] == "text":
                    self.store.add_clip(ev[1], ev[2])
                elif ev[0] == "manual":
                    self._handle_manual(ev[1])
            while True:
                try:
                    path = self.files_q.get_nowait()
                except queue.Empty:
                    break
                self.store.handle_landed_file(path)
            while True:
                try:
                    res = self.export_q.get_nowait()
                except queue.Empty:
                    break
                self._finish_export(res)
        except Exception as e:  # pragma: no cover
            system.log_error("drain: %r" % (e,))
        self.root.after(120, self._drain)

    def _handle_manual(self, res):
        kind = res[0] if res else "empty"
        if kind == "text":
            before = len(self.store.items)
            self.store.add_clip(res[1], "手动收录")
            if len(self.store.items) == before:
                self.toast("这段内容已在收集篮中")
        elif kind == "files":
            self.store.attach_files_to_latest_or_create(res[1], source_app="手动收录")
        elif kind == "sensitive":
            self.toast("已跳过：剪贴板内容被标记为敏感（密码等）", kind="error")
        elif kind == "error":
            self.toast(res[1], kind="error")
        else:
            self.toast("剪贴板里没有可收录的文本或文件")

    # ------------------------------------------------------------------ actions

    def manual_capture(self):
        self.monitor.request_manual_capture()

    def copy_raw(self):
        text, n = self.store.selected_raw_text()
        if not n:
            self.toast("没有勾选包含文本的条目")
            return
        ok, err = self.monitor.write_text(text)
        self.toast("已复制 %d 段纯原文，可直接粘贴" % n if ok else err, kind="ok" if ok else "error")

    def generate_space(self):
        clips = list(self.store.selected_items)
        if not clips:
            self.toast("请先勾选要放进空间的条目")
            return
        if self._exporting:
            return
        self._exporting = True
        title = self.title_var.get() if getattr(self, "title_var", None) is not None else self.store.space_title
        self.store.space_title = title
        directory = self.store.settings.export_directory
        now = self.store.now()
        self.toast("正在生成空间…", ms=8000)
        self.schedule_refresh()

        def work():
            try:
                r = space_html.write_space(clips, title, directory, now=now)
                self.export_q.put(("ok", r))
            except Exception as e:
                self.export_q.put(("error", str(e)))

        threading.Thread(target=work, name="grokclip-export", daemon=True).start()

    def _finish_export(self, res):
        self._exporting = False
        if res[0] != "ok":
            self.toast("生成失败：%s" % res[1], kind="error", ms=3500)
            self.schedule_refresh()
            return
        r = res[1]
        self.store.exported_paths.add(os.path.abspath(r.path))
        self.store.last_export = r
        self.watcher.ignore(r.path)
        ok, err = self.monitor.write_files([r.path])
        paste = "Ctrl+V" if not sys.platform == "darwin" else "⌘V"
        if ok:
            self.toast("空间已生成并复制为文件\n去微信 / 飞书聊天框按 %s 发送" % paste, kind="ok", ms=3200)
        else:
            self.toast("空间已生成：%s\n%s" % (os.path.basename(r.path), err), kind="error", ms=4500)
        self.schedule_refresh()

    def open_export_dir(self):
        d = self.store.settings.export_directory
        try:
            os.makedirs(d, exist_ok=True)
        except OSError:
            pass
        system.open_path(d)

    def quit(self):
        try:
            self.monitor.stop()
            self.watcher.stop()
            self.store.save_items()
            self.store.save_settings()
        finally:
            self.root.destroy()

    def _on_drop(self, event, clip_id):  # pragma: no cover - 仅安装了 tkinterdnd2 时
        try:
            paths = list(self.root.tk.splitlist(event.data))
        except Exception:
            return
        if clip_id is None:
            self.store.attach_files_to_latest_or_create(paths)
        else:
            self.store.attach_files(paths, clip_id)

    # ------------------------------------------------------------------ drawer

    def toggle_drawer(self):
        if self.drawer is not None and self.drawer.winfo_viewable():
            self.drawer.withdraw()
        else:
            self.show_drawer()

    def show_drawer(self):
        if self.drawer is None:
            self._build_drawer()
        self._position_drawer()
        self.drawer.deiconify()
        self.drawer.lift()
        self.refresh()

    def _position_drawer(self):
        d = self.drawer
        w, h = self._px(460), self._px(660)
        sw, sh = d.winfo_screenwidth(), d.winfo_screenheight()
        h = min(h, sh - self._px(60))
        ox, oy = self.orb.winfo_x(), self.orb.winfo_y()
        x = ox - w - self._px(10) if ox - w - self._px(10) >= 0 else min(ox + self.orb_size + self._px(10), sw - w)
        y = min(max(self._px(10), oy - h // 3), max(0, sh - h - self._px(40)))
        d.geometry("%dx%d+%d+%d" % (w, h, max(0, x), max(0, y)))

    def _build_drawer(self):
        d = tk.Toplevel(self.root)
        d.title("GrokClip Space · 收集篮")
        d.configure(bg=BG)
        d.attributes("-topmost", True)
        d.protocol("WM_DELETE_WINDOW", d.withdraw)
        d.bind("<Escape>", lambda e: d.withdraw())
        # 快捷键：Ctrl+Enter 生成空间并复制；Ctrl+Shift+C 复制纯原文；Ctrl+Shift+V 粘贴收录
        d.bind("<Control-Return>", lambda e: self.generate_space())
        for k in ("<Control-Shift-C>", "<Control-Shift-c>"):
            d.bind(k, lambda e: self.copy_raw())
        for k in ("<Control-Shift-V>", "<Control-Shift-v>"):
            d.bind(k, lambda e: self.manual_capture())
        self.drawer = d
        pad = self._px(12)

        top = ttk.Frame(d, padding=(pad, pad, pad, 4))
        top.pack(fill="x")
        ttk.Label(top, text="收集篮", style="Title.TLabel").pack(side="left")
        self.count_label = ttk.Label(top, text="", style="Muted.TLabel")
        self.count_label.pack(side="left", padx=(8, 0), pady=(4, 0))
        ttk.Button(top, text="⚙ 设置", style="Small.TButton", command=self.show_settings).pack(side="right")
        self.listen_btn = ttk.Button(top, text="", style="Small.TButton", command=self.store.toggle_listening)
        self.listen_btn.pack(side="right", padx=(0, 6))

        row = ttk.Frame(d, padding=(pad, 2, pad, 4))
        row.pack(fill="x")
        ttk.Label(row, text="收录范围", style="Muted.TLabel").pack(side="left")
        self.scope_combo = ttk.Combobox(row, state="readonly", width=14,
                                        values=[SCOPE_NAMES[s] for s in CAPTURE_SCOPES])
        self.scope_combo.pack(side="left", padx=(8, 0))
        self.scope_combo.bind("<<ComboboxSelected>>", self._on_scope_combo)
        ttk.Button(row, text="粘贴收录", style="Small.TButton", command=self.manual_capture).pack(side="right")

        self.warn_label = ttk.Label(d, text="", style="Warn.TLabel", wraplength=self._px(430),
                                    justify="left", padding=(pad, 5))
        self.pending_frame = ttk.Frame(d, padding=(pad, 2, pad, 2))

        # 列表（Canvas + 内部 Frame + 滚动条）
        mid = ttk.Frame(d, padding=(pad, 4, 2, 4))
        mid.pack(fill="both", expand=True)
        self.list_mid = mid
        cv = tk.Canvas(mid, bg=BG, highlightthickness=0, bd=0)
        sb = ttk.Scrollbar(mid, orient="vertical", command=cv.yview)
        cv.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        cv.pack(side="left", fill="both", expand=True)
        inner = ttk.Frame(cv)
        win_id = cv.create_window((0, 0), window=inner, anchor="nw")
        inner.bind("<Configure>", lambda e: cv.configure(scrollregion=cv.bbox("all")))
        cv.bind("<Configure>", lambda e: cv.itemconfigure(win_id, width=e.width - 4))
        self.list_canvas, self.list_inner = cv, inner
        for w in (cv, inner):
            w.bind("<Enter>", lambda e: self._bind_wheel(True))
            w.bind("<Leave>", lambda e: self._bind_wheel(False))

        # 底部
        bottom = ttk.Frame(d, padding=(pad, 4, pad, pad))
        bottom.pack(fill="x", side="bottom")
        self.bottom = bottom
        self.export_frame = ttk.Frame(bottom)
        r1 = ttk.Frame(bottom)
        r1.pack(fill="x", pady=(0, 6))
        self.all_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(r1, text="全选", variable=self.all_var,
                        command=lambda: self.store.set_all_selected(self.all_var.get()) or self.schedule_refresh()
                        ).pack(side="left")
        self.sel_label = ttk.Label(r1, text="", style="Muted.TLabel")
        self.sel_label.pack(side="left", padx=(8, 0))
        ttk.Button(r1, text="清空", style="Small.TButton", command=self._confirm_clear).pack(side="right")
        ttk.Button(r1, text="导出目录", style="Small.TButton", command=self.open_export_dir).pack(side="right",
                                                                                              padx=(0, 6))
        r2 = ttk.Frame(bottom)
        r2.pack(fill="x", pady=(0, 6))
        ttk.Label(r2, text="空间标题（选填）", style="Muted.TLabel").pack(side="left")
        self.title_var = tk.StringVar(value=self.store.space_title)
        ttk.Entry(r2, textvariable=self.title_var).pack(side="left", fill="x", expand=True, padx=(8, 0))
        r3 = ttk.Frame(bottom)
        r3.pack(fill="x")
        # 用经典 tk.Button：Windows 原生主题不允许修改 ttk 按钮背景色，白字会看不见
        self.gen_btn = tk.Button(r3, text="一键生成空间并复制", command=self.generate_space, bg=ACCENT, fg="#ffffff",
                                 activebackground=ACCENT_2, activeforeground="#ffffff", disabledforeground="#e6e3ff",
                                 relief="flat", bd=0, font=self.font_bold, cursor="hand2", padx=10, pady=7,
                                 highlightthickness=0)
        self.gen_btn.pack(side="left", fill="x", expand=True)
        ttk.Button(r3, text="复制纯原文", command=self.copy_raw).pack(side="left", padx=(8, 0), ipady=3)

    def _bind_wheel(self, on):
        cv = self.list_canvas
        if on:
            cv.bind_all("<MouseWheel>", lambda e: cv.yview_scroll(int(-e.delta / 120) or (-1 if e.delta > 0 else 1),
                                                                   "units"))
            cv.bind_all("<Button-4>", lambda e: cv.yview_scroll(-2, "units"))
            cv.bind_all("<Button-5>", lambda e: cv.yview_scroll(2, "units"))
        else:
            cv.unbind_all("<MouseWheel>")
            cv.unbind_all("<Button-4>")
            cv.unbind_all("<Button-5>")

    def _on_scope_combo(self, _e=None):
        name = self.scope_combo.get()
        for s in CAPTURE_SCOPES:
            if SCOPE_NAMES[s] == name:
                self.store.set_capture_scope(s)

    def _confirm_clear(self):
        if not self.store.items:
            return
        if messagebox.askyesno("清空收集篮", "确定清空全部 %d 条？（磁盘上的文件不会被删除）" % len(self.store.items),
                               parent=self.drawer):
            self.store.clear_all()
            self.schedule_refresh()

    def schedule_refresh(self):
        if not self._refresh_pending:
            self._refresh_pending = True
            self.root.after_idle(self.refresh)

    def refresh(self):
        self._refresh_pending = False
        self._draw_orb()
        if self.drawer is None:
            return
        st = self.store
        n, sel = len(st.items), len(st.selected_items)
        self.count_label.configure(text="%d 条" % n)
        self.listen_btn.configure(text="⏸ 暂停监听" if st.settings.is_listening else "▶ 继续监听")
        self.scope_combo.set(SCOPE_NAMES[st.settings.capture_scope])
        self.sel_label.configure(text="已选 %d / 共 %d" % (sel, n))
        self.all_var.set(st.all_selected)
        busy_or_empty = self._exporting or not sel
        self.gen_btn.configure(state="disabled" if busy_or_empty else "normal",
                               bg="#b9b2ee" if busy_or_empty else ACCENT,
                               text="正在生成…" if self._exporting else "一键生成空间并复制")

        warns = list(self.backend.warnings)
        if (not self.backend.can_detect_foreground and st.settings.capture_scope != models.ALL_APPS
                and not any("全部应用" in w for w in warns)):
            warns.append("无法判断前台应用，当前按「全部应用」收录。")
        if warns:
            self.warn_label.configure(text="⚠ " + "\n⚠ ".join(warns))
            self.warn_label.pack(fill="x", padx=self._px(12), pady=(0, 4), before=self.list_mid)
        else:
            self.warn_label.pack_forget()

        self._render_pending()
        self._render_list()
        self._render_export()

    def _render_pending(self):
        f = self.pending_frame
        for c in f.winfo_children():
            c.destroy()
        self.store.prune_pending()
        pend = self.store.pending
        if not pend:
            f.pack_forget()
            return
        f.pack(fill="x", before=self.list_mid)
        head = ttk.Frame(f)
        head.pack(fill="x")
        ttk.Label(head, text="新落地的文件（%d）" % len(pend), style="Muted.TLabel").pack(side="left")
        ttk.Button(head, text="全部忽略", style="Small.TButton",
                   command=lambda: (self.store.dismiss_all_pending(), self.schedule_refresh())).pack(side="right")
        for p in pend[-5:]:
            r = ttk.Frame(f)
            r.pack(fill="x", pady=1)
            ttk.Label(r, text="• " + tk_safe(p.file_name), wraplength=self._px(250)).pack(side="left")
            ttk.Button(r, text="忽略", style="Small.TButton",
                       command=lambda i=p.id: (self.store.dismiss_pending(i), self.schedule_refresh())
                       ).pack(side="right")
            ttk.Button(r, text="挂到最新", style="Small.TButton",
                       command=lambda i=p.id: (self.store.attach_pending_to_latest(i), self.schedule_refresh())
                       ).pack(side="right", padx=(0, 4))

    def _render_list(self):
        inner = self.list_inner
        for c in inner.winfo_children():
            c.destroy()
        items = self.store.items
        if not items:
            ttk.Label(inner, text="还没有收录内容\n\n在 Grok Bot 里点「复制」，就会按顺序收进来；\n"
                                  "也可以复制后点「粘贴收录」。", style="Muted.TLabel", justify="center"
                      ).pack(pady=self._px(60))
            return
        for idx, it in enumerate(items):
            self._render_card(inner, idx, it)
        self.list_canvas.update_idletasks()
        self.list_canvas.configure(scrollregion=self.list_canvas.bbox("all"))

    def _render_card(self, parent, idx, it):
        outer = tk.Frame(parent, bg=ACCENT if it.is_selected else LINE, bd=0)
        outer.pack(fill="x", pady=(0, self._px(8)), padx=(0, self._px(6)))
        card = ttk.Frame(outer, style="Card.TFrame", padding=self._px(8))
        card.pack(fill="x", padx=1, pady=1)
        head = ttk.Frame(card, style="Card.TFrame")
        head.pack(fill="x")
        var = tk.BooleanVar(value=it.is_selected)
        ttk.Checkbutton(head, variable=var, style="Card.TCheckbutton",
                        command=lambda i=it.id, v=var: (self.store.set_selected(i, v.get()), self.schedule_refresh())
                        ).pack(side="left")
        ttk.Label(head, text="#%02d" % (idx + 1), style="Badge.TLabel").pack(side="left")
        ttk.Label(head, text="%s · %s" % (tk_safe(it.source_app), models.local_time(it.timestamp)),
                  style="CardMuted.TLabel").pack(side="left", padx=(6, 0))
        for label, cmd in (("✕", lambda i=it.id: (self.store.delete(i), self.schedule_refresh())),
                           ("附件", lambda i=it.id: self._pick_files(i)),
                           ("↓", lambda i=it.id: (self.store.move_down(i), self.schedule_refresh())),
                           ("↑", lambda i=it.id: (self.store.move_up(i), self.schedule_refresh()))):
            ttk.Button(head, text=label, style="Small.TButton", width=4 if label == "附件" else 2,
                       command=cmd).pack(side="right", padx=(2, 0))
        body = preview_text(it.raw_content) if it.raw_content else "（仅附件）"
        ttk.Label(card, text=body, style="Card.TLabel" if it.raw_content else "CardMuted.TLabel",
                  wraplength=self._px(370), justify="left").pack(fill="x", anchor="w", pady=(self._px(4), 0))
        for a in it.attachments:
            r = ttk.Frame(card, style="Card.TFrame")
            r.pack(fill="x", pady=(2, 0))
            exists = a.exists
            info = "附件 · %s  %s" % (tk_safe(a.file_name), models.byte_string(a.size) if exists else "（文件已不存在）")
            ttk.Label(r, text=info, style="CardMuted.TLabel", wraplength=self._px(310)).pack(side="left")
            ttk.Button(r, text="移除", style="Small.TButton",
                       command=lambda c=it.id, x=a.id: (self.store.remove_attachment(c, x), self.schedule_refresh())
                       ).pack(side="right")
        if HAS_DND:  # pragma: no cover
            try:
                card.drop_target_register(DND_FILES)
                card.dnd_bind("<<Drop>>", lambda e, i=it.id: self._on_drop(e, i))
            except Exception:
                pass

    def _pick_files(self, clip_id):
        paths = filedialog.askopenfilenames(parent=self.drawer, title="选择要挂载的文件")
        if paths:
            self.store.attach_files(list(paths), clip_id)
            self.schedule_refresh()

    def _render_export(self):
        f = self.export_frame
        for c in f.winfo_children():
            c.destroy()
        r = self.store.last_export
        if r is None:
            f.pack_forget()
            return
        f.pack(fill="x", pady=(0, 8), before=self.bottom.winfo_children()[1])
        extra = "（%d 个超限未内嵌）" % r.skipped_attachment_count if r.skipped_attachment_count else ""
        text = "✓ 已生成 %s\n%s · %d 段原文 · %d 个附件%s" % (tk_safe(os.path.basename(r.path)),
                                                       models.byte_string(r.byte_size), r.clip_count,
                                                       r.attachment_count, extra)
        ttk.Label(f, text=text, style="Ok.TLabel", padding=6, wraplength=self._px(420), justify="left"
                  ).pack(fill="x")
        b = ttk.Frame(f)
        b.pack(fill="x", pady=(4, 0))
        ttk.Button(b, text="浏览器打开", style="Small.TButton", command=lambda: system.open_path(r.path)
                   ).pack(side="left")
        ttk.Button(b, text="打开所在文件夹", style="Small.TButton",
                   command=lambda: system.reveal_in_file_manager(r.path)).pack(side="left", padx=(6, 0))
        ttk.Button(b, text="再次复制文件", style="Small.TButton",
                   command=lambda: self._recopy(r.path)).pack(side="left", padx=(6, 0))
        ttk.Button(b, text="×", style="Small.TButton", width=2,
                   command=lambda: (setattr(self.store, "last_export", None), self.schedule_refresh())
                   ).pack(side="right")

    def _recopy(self, path):
        ok, err = self.monitor.write_files([path])
        self.toast("已重新复制空间文件" if ok else err, kind="ok" if ok else "error")

    # ------------------------------------------------------------------ settings

    def show_settings(self):
        if self.settings_win is not None and self.settings_win.winfo_exists():
            self.settings_win.deiconify()
            self.settings_win.lift()
            self._render_settings()
            return
        w = tk.Toplevel(self.root)
        w.title("GrokClip Space · 设置")
        w.configure(bg=BG)
        w.attributes("-topmost", True)
        w.geometry("%dx%d" % (self._px(520), self._px(470)))
        self.settings_win = w
        self.settings_body = ttk.Frame(w, padding=self._px(14))
        self.settings_body.pack(fill="both", expand=True)
        self._render_settings()

    def _render_settings(self):
        b = self.settings_body
        for c in b.winfo_children():
            c.destroy()
        s = self.store.settings
        ttk.Label(b, text="监听目录（新落地的文件会自动挂到对应原文下）", style="TLabel").pack(anchor="w")
        lb = tk.Listbox(b, height=5, font=self.font_small, activestyle="none", selectbackground=ACCENT)
        for d in s.watched_directories:
            lb.insert("end", tk_safe(d))
        lb.pack(fill="x", pady=(4, 4))
        r = ttk.Frame(b)
        r.pack(fill="x")

        def add_dir():
            p = filedialog.askdirectory(parent=self.settings_win, title="选择要监听的文件目录")
            if p:
                self.store.add_watched_directory(p)
                self._render_settings()

        def remove_dir():
            sel = lb.curselection()
            if sel:
                self.store.remove_watched_directory(s.watched_directories[sel[0]])
                self._render_settings()

        ttk.Button(r, text="添加目录…", style="Small.TButton", command=add_dir).pack(side="left")
        ttk.Button(r, text="移除所选", style="Small.TButton", command=remove_dir).pack(side="left", padx=(6, 0))

        ttk.Label(b, text="导出目录（生成的空间 .html 保存在这里）").pack(anchor="w", pady=(14, 2))
        r2 = ttk.Frame(b)
        r2.pack(fill="x")
        ttk.Label(r2, text=tk_safe(s.export_directory), style="Muted.TLabel", wraplength=self._px(360)
                  ).pack(side="left")

        def choose_export():
            p = filedialog.askdirectory(parent=self.settings_win, title="选择空间文件的导出目录")
            if p:
                self.store.set_export_directory(p)
                self._render_settings()

        ttk.Button(r2, text="更改…", style="Small.TButton", command=choose_export).pack(side="right")

        ttk.Label(b, text="自动挂载时间窗口").pack(anchor="w", pady=(14, 2))
        r3 = ttk.Frame(b)
        r3.pack(fill="x")
        win_var = tk.StringVar(value=str(int(s.auto_attach_window_seconds)))
        sp = ttk.Spinbox(r3, from_=10, to=3600, increment=10, width=6, textvariable=win_var)
        sp.pack(side="left")
        ttk.Label(r3, text="秒（复制前后这段时间内落地的文件自动挂上）", style="Muted.TLabel").pack(side="left", padx=6)

        def apply_window(*_):
            try:
                self.store.set_auto_attach_window(float(win_var.get()))
            except ValueError:
                pass

        sp.configure(command=apply_window)
        sp.bind("<FocusOut>", apply_window)
        sp.bind("<Return>", apply_window)

        r4 = ttk.Frame(b)
        r4.pack(fill="x", pady=(16, 0))
        ttk.Button(r4, text="恢复默认目录", style="Small.TButton",
                   command=lambda: (self.store.reset_directories_to_default(), self._render_settings())
                   ).pack(side="left")
        ttk.Button(r4, text="打开数据目录", style="Small.TButton",
                   command=lambda: system.open_path(self.store.data_dir)).pack(side="left", padx=(6, 0))

        info = "剪贴板：%s · 前台应用识别：%s · 拖放：%s\n数据目录：%s\n版本 %s · 全部数据只保存在本机，不联网" % (
            self.backend.name, "可用" if self.backend.can_detect_foreground else "不可用",
            "可用" if HAS_DND else "未安装 tkinterdnd2（可用「附件」按钮代替）",
            tk_safe(self.store.data_dir), __version__)
        ttk.Label(b, text=info, style="Muted.TLabel", wraplength=self._px(480), justify="left"
                  ).pack(anchor="w", pady=(16, 0))

    # ------------------------------------------------------------------ run

    def run(self):
        self.root.mainloop()


def run_app():
    try:
        app = App()
    except tk.TclError as e:
        system.log_error("无法启动界面：%s" % e)
        if sys.stderr:
            sys.stderr.write("无法启动图形界面（%s）。请在桌面会话中运行。\n" % e)
        return 2
    try:
        app.run()
    except KeyboardInterrupt:
        app.quit()
    return 0
