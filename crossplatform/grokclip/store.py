"""核心状态容器（ClipStore.swift 的移植，不含任何 UI 代码，便于单元测试）。

所有方法应在同一线程（UI 主线程）调用。时间与文件存在性可注入，测试用假时钟。
"""
import os
import time

from . import models, platform_paths, space_html
from .models import ClipAttachment, ClipItem, PendingFile, AppSettings


def norm_path(p):
    return os.path.abspath(os.path.expanduser(str(p)))


def same_path(a, b):
    return os.path.normcase(norm_path(a)) == os.path.normcase(norm_path(b))


class ClipStore(object):
    def __init__(self, data_dir=None, now_fn=None, exists_fn=None, autoload=True):
        self.data_dir = str(data_dir or platform_paths.data_dir())
        self.clips_path = os.path.join(self.data_dir, "clips_store.json")
        self.settings_path = os.path.join(self.data_dir, "settings.json")
        self.now = now_fn or time.time
        self.exists = exists_fn or os.path.exists
        self.items = []                # 严格按收集先后：#01, #02 …
        self.pending = []              # PendingFile
        self.settings = AppSettings()
        self.space_title = ""
        self.last_export = None
        self.exported_paths = set()
        self.on_event = None           # callable(kind, payload_dict)
        self.on_dedup_reset = None     # callable()
        self.on_settings_changed = None
        if autoload:
            self.load()

    # ---------------- persistence ----------------

    def load(self):
        s = models.load_json(self.settings_path)
        self.settings = AppSettings.from_json(s) if s is not None else AppSettings()
        raw = models.load_json(self.clips_path)
        items = []
        if isinstance(raw, list):
            for d in raw:
                it = ClipItem.from_json(d)
                if it is not None:
                    items.append(it)
        self.items = items

    def save_items(self):
        try:
            models.atomic_write_json(self.clips_path, [i.to_json() for i in self.items])
        except OSError as e:
            self._emit("error", message="保存失败：%s" % e)

    def save_settings(self):
        try:
            models.atomic_write_json(self.settings_path, self.settings.to_json())
        except OSError as e:
            self._emit("error", message="保存设置失败：%s" % e)

    def _emit(self, kind, **payload):
        cb = self.on_event
        if cb:
            cb(kind, payload)

    # ---------------- derived ----------------

    @property
    def selected_items(self):
        return [i for i in self.items if i.is_selected]

    @property
    def all_selected(self):
        return bool(self.items) and all(i.is_selected for i in self.items)

    def index_of(self, clip_id):
        for i, it in enumerate(self.items):
            if it.id == clip_id:
                return i
        return None

    def latest_item_index(self):
        """最近“新增”的条目（按收录时间，而非列表位置）。"""
        if not self.items:
            return None
        best = 0
        for i in range(len(self.items)):
            if self.items[i].timestamp >= self.items[best].timestamp:
                best = i
        return best

    def _is_already_attached(self, path):
        return any(same_path(a.path, path) for it in self.items for a in it.attachments)

    # ---------------- capture ----------------

    def add_clip(self, text, source_app):
        """返回新条目；与最近一条完全相同则去重返回 None。"""
        h = models.sha256(text)
        li = self.latest_item_index()
        if li is not None and self.items[li].content_hash == h:
            return None
        now = self.now()
        item = ClipItem(source_app=source_app, raw_content=text, timestamp=now)
        # 反向认领：文本到来时，把最近 N 秒内落地的文件挂到这条原文下
        self.prune_pending()
        window = self.settings.auto_attach_window_seconds
        claim = [p for p in self.pending if abs(now - p.detected_at) <= window]
        for f in claim:
            if self.exists(f.path):
                item.attachments.append(ClipAttachment(f.path, added_at=now))
        claimed = {p.id for p in claim}
        self.pending = [p for p in self.pending if p.id not in claimed]
        self.items.append(item)
        self.save_items()
        self._emit("captured", index=len(self.items), auto_attached=len(item.attachments))
        return item

    # ---------------- files ----------------

    def prune_pending(self):
        window = max(self.settings.auto_attach_window_seconds, 120.0)
        now = self.now()
        self.pending = [p for p in self.pending if now - p.detected_at <= window and self.exists(p.path)]

    def handle_landed_file(self, path):
        """正向关联：最近一条原文在窗口内则直接挂载，否则进入缓冲池等待下一条原文认领。"""
        path = norm_path(path)
        if any(same_path(path, e) for e in self.exported_paths) or self._is_already_attached(path):
            return "ignored"
        if any(same_path(p.path, path) for p in self.pending):
            return "ignored"
        self.prune_pending()
        now = self.now()
        li = self.latest_item_index()
        if li is not None and abs(now - self.items[li].timestamp) <= self.settings.auto_attach_window_seconds:
            self.items[li].attachments.append(ClipAttachment(path, added_at=now))
            self.save_items()
            self._emit("attached", file_name=os.path.basename(path), count=1)
            return "attached"
        self.pending.append(PendingFile(path, now))
        self._emit("pending", file_name=os.path.basename(path))
        return "pending"

    def attach_files(self, paths, clip_id):
        idx = self.index_of(clip_id)
        if idx is None:
            return 0
        added = []
        for p in paths:
            p = norm_path(p)
            if not os.path.isfile(p):
                continue
            if any(same_path(a.path, p) for a in self.items[idx].attachments):
                continue
            self.items[idx].attachments.append(ClipAttachment(p, added_at=self.now()))
            self.pending = [x for x in self.pending if not same_path(x.path, p)]
            added.append(os.path.basename(p))
        if not added:
            self._emit("info", message="没有可挂载的新文件（文件夹不支持）")
            return 0
        self.save_items()
        self._emit("attached", file_name=added[0], count=len(added))
        return len(added)

    def attach_files_to_latest_or_create(self, paths, source_app="拖拽文件"):
        li = self.latest_item_index()
        if li is not None:
            return self.attach_files(paths, self.items[li].id)
        files = [norm_path(p) for p in paths if os.path.isfile(norm_path(p))]
        if not files:
            self._emit("info", message="没有可挂载的文件（文件夹不支持）")
            return 0
        now = self.now()
        atts = [ClipAttachment(p, added_at=now) for p in files]
        self.items.append(ClipItem(source_app=source_app, raw_content="", attachments=atts, timestamp=now))
        self.pending = [x for x in self.pending if not any(same_path(x.path, f) for f in files)]
        self.save_items()
        self._emit("attached", file_name=os.path.basename(files[0]), count=len(files))
        return len(files)

    def attach_pending_to_latest(self, pending_id):
        f = next((p for p in self.pending if p.id == pending_id), None)
        if f is None:
            return
        self.attach_files_to_latest_or_create([f.path])
        self.pending = [p for p in self.pending if p.id != pending_id]

    def dismiss_pending(self, pending_id):
        self.pending = [p for p in self.pending if p.id != pending_id]

    def dismiss_all_pending(self):
        self.pending = []

    def remove_attachment(self, clip_id, attachment_id):
        idx = self.index_of(clip_id)
        if idx is None:
            return
        it = self.items[idx]
        it.attachments = [a for a in it.attachments if a.id != attachment_id]
        if it.raw_content == "" and not it.attachments:
            del self.items[idx]
        self.save_items()

    # ---------------- editing ----------------

    def set_selected(self, clip_id, value):
        idx = self.index_of(clip_id)
        if idx is None:
            return
        self.items[idx].is_selected = bool(value)
        self.save_items()

    def set_all_selected(self, value):
        for it in self.items:
            it.is_selected = bool(value)
        self.save_items()

    def move_up(self, clip_id):
        idx = self.index_of(clip_id)
        if idx is None or idx == 0:
            return
        self.items[idx - 1], self.items[idx] = self.items[idx], self.items[idx - 1]
        self.save_items()

    def move_down(self, clip_id):
        idx = self.index_of(clip_id)
        if idx is None or idx >= len(self.items) - 1:
            return
        self.items[idx + 1], self.items[idx] = self.items[idx], self.items[idx + 1]
        self.save_items()

    def delete(self, clip_id):
        self.items = [i for i in self.items if i.id != clip_id]
        if self.on_dedup_reset:
            self.on_dedup_reset()
        self.save_items()

    def clear_all(self):
        self.items = []
        self.pending = []
        self.last_export = None
        if self.on_dedup_reset:
            self.on_dedup_reset()
        self.save_items()

    # ---------------- output ----------------

    @staticmethod
    def joined_raw_text(clips, separator="\n\n"):
        return separator.join(c.raw_content for c in clips if c.raw_content != "")

    def selected_raw_text(self):
        sel = [c for c in self.selected_items if c.raw_content != ""]
        return (self.joined_raw_text(sel), len(sel))

    def export_space(self, clips=None, title=None):
        """同步生成空间文件；返回 SpaceExportResult（失败抛 SpaceGeneratorError）。"""
        clips = self.selected_items if clips is None else clips
        title = self.space_title if title is None else title
        result = space_html.write_space(clips, title, self.settings.export_directory, now=self.now())
        self.exported_paths.add(norm_path(result.path))
        self.last_export = result
        return result

    # ---------------- settings ----------------

    def _settings_changed(self, reload_watcher):
        self.save_settings()
        if self.on_settings_changed:
            self.on_settings_changed(reload_watcher)

    def set_capture_scope(self, scope):
        if scope not in models.CAPTURE_SCOPES or scope == self.settings.capture_scope:
            return
        self.settings.capture_scope = scope
        self._settings_changed(False)

    def set_listening(self, on):
        on = bool(on)
        if on == self.settings.is_listening:
            return
        self.settings.is_listening = on
        self._settings_changed(True)

    def toggle_listening(self):
        self.set_listening(not self.settings.is_listening)

    def set_auto_attach_window(self, seconds):
        self.settings.auto_attach_window_seconds = min(max(float(seconds), 10.0), 3600.0)
        self._settings_changed(False)

    def add_watched_directory(self, path):
        p = norm_path(path)
        if any(same_path(p, d) for d in self.settings.watched_directories):
            return
        self.settings.watched_directories.append(p)
        self._settings_changed(True)

    def remove_watched_directory(self, path):
        self.settings.watched_directories = [d for d in self.settings.watched_directories if d != path]
        self._settings_changed(True)

    def set_export_directory(self, path):
        self.settings.export_directory = norm_path(path)
        self._settings_changed(False)

    def reset_directories_to_default(self):
        self.settings.watched_directories = platform_paths.default_watched_directories()
        self.settings.export_directory = platform_paths.default_export_directory()
        self._settings_changed(True)
