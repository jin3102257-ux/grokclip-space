"""多目录文件落地监听（纯标准库轮询，跨平台）。

每 ~1 秒扫描一次监听目录；新出现的文件需要「大小 > 0 且连续两次扫描大小不变」才视为写入完成，
然后回调 on_new_file(path)。回调在监听线程中触发，调用方负责切回 UI 线程。
"""
import os
import threading
import time

IGNORED_EXTENSIONS = {"crdownload", "download", "tmp", "part", "opdownload", "ds_store", "partial"}
MAX_STABILITY_ATTEMPTS = 60  # ≈ 60 秒仍在增长则放弃


def should_ignore(name):
    if not name or name.startswith(".") or name.startswith("~$"):
        return True
    lower = name.lower()
    if lower in ("desktop.ini", "thumbs.db"):
        return True
    ext = os.path.splitext(lower)[1][1:]
    if ext in IGNORED_EXTENSIONS:
        return True
    if name.startswith("GrokSpace_") and ext == "html":
        return True
    return False


def _norm(path):
    return os.path.normcase(os.path.abspath(os.path.expanduser(path)))


def _is_hidden_windows(entry):
    attrs = getattr(entry.stat(follow_symlinks=False), "st_file_attributes", 0)
    return bool(attrs & 0x2)  # FILE_ATTRIBUTE_HIDDEN


class DownloadWatcher(object):
    def __init__(self, on_new_file=None, interval=1.0):
        self.on_new_file = on_new_file
        self.interval = interval
        self._lock = threading.Lock()
        self._wanted = []          # 期望监听的目录（原始路径）
        self._known = {}           # norm_dir -> set(names)
        self._dir_paths = {}       # norm_dir -> real dir path
        self._pending = {}         # full path -> [last_size, attempts]
        self._ignored = set()      # normcase 后的完整路径
        self._thread = None
        self._stop = threading.Event()

    # ---------- public (thread-safe) ----------

    def set_directories(self, paths):
        with self._lock:
            self._wanted = list(paths)

    def ignore(self, path):
        with self._lock:
            self._ignored.add(_norm(path))

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="grokclip-watcher", daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()

    # ---------- core ----------

    def _run(self):
        while not self._stop.is_set():
            try:
                self.tick()
            except Exception:
                pass
            self._stop.wait(self.interval)

    @staticmethod
    def _list(directory):
        names = []
        try:
            with os.scandir(directory) as it:
                for e in it:
                    try:
                        if e.is_dir(follow_symlinks=False):
                            continue
                        if os.name == "nt" and _is_hidden_windows(e):
                            continue
                    except OSError:
                        continue
                    names.append(e.name)
        except OSError:
            return None
        return set(names)

    def tick(self):
        """执行一次扫描（测试中可直接调用）。返回本次判定为写入完成的文件列表。"""
        with self._lock:
            wanted = list(self._wanted)
            ignored = set(self._ignored)

        wanted_norm = {}
        for p in wanted:
            real = os.path.abspath(os.path.expanduser(p))
            if os.path.isdir(real):
                wanted_norm[os.path.normcase(real)] = real

        # 移除不再监听的目录
        for d in list(self._known):
            if d not in wanted_norm:
                self._known.pop(d, None)
                self._dir_paths.pop(d, None)
                prefix = d + os.sep
                for f in list(self._pending):
                    if os.path.normcase(f).startswith(prefix):
                        self._pending.pop(f, None)

        # 新目录：先建立基线快照（已有文件不算“新落地”）
        for d, real in wanted_norm.items():
            if d not in self._known:
                names = self._list(real)
                if names is not None:
                    self._known[d] = names
                    self._dir_paths[d] = real
                continue
            names = self._list(real)
            if names is None:
                continue
            added = names - self._known[d]
            self._known[d] = names
            for name in sorted(added):
                if should_ignore(name):
                    continue
                full = os.path.join(real, name)
                if _norm(full) in ignored or full in self._pending:
                    continue
                self._pending[full] = [-1, 0]

        landed = []
        for full in list(self._pending):
            state = self._pending[full]
            try:
                st = os.stat(full)
            except OSError:
                self._pending.pop(full, None)
                continue
            if os.path.isdir(full):
                self._pending.pop(full, None)
                continue
            size = st.st_size
            if size > 0 and size == state[0]:
                self._pending.pop(full, None)
                if _norm(full) not in ignored:
                    landed.append(full)
                continue
            state[0] = size
            state[1] += 1
            if state[1] > MAX_STABILITY_ATTEMPTS:
                self._pending.pop(full, None)

        cb = self.on_new_file
        if cb:
            for f in landed:
                try:
                    cb(f)
                except Exception:
                    pass
        return landed
