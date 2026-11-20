"""数据模型（与 macOS 版 Models.swift 的 JSON 结构保持一致）。

clips_store.json：[ClipItem]，字段 attachments/contentHash/id/isSelected/rawContent/sourceApp/timestamp
settings.json：AppSettings，字段 autoAttachWindowSeconds/captureScope/exportDirectory/isListening/watchedDirectories
日期统一为 ISO 8601（UTC，形如 2026-01-01T08:00:00Z），UUID 为大写字符串（与 Swift 相同）。
"""
import hashlib
import json
import os
import re
import tempfile
import time
import uuid
from datetime import datetime, timezone

from . import platform_paths

# ---------------- helpers ----------------


def sha256(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def new_id():
    return str(uuid.uuid4()).upper()


def iso_utc(ts):
    """epoch 秒 → '2026-01-01T08:00:00Z'（与 Swift ISO8601DateFormatter 默认格式一致）。"""
    return datetime.fromtimestamp(int(ts), tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


_ISO_RE = re.compile(
    r"^(\d{4})-(\d{2})-(\d{2})[T ](\d{2}):(\d{2}):(\d{2})(\.\d+)?(Z|[+-]\d{2}:?\d{2})?$")


def parse_iso(text, default=None):
    """宽松解析 ISO 8601（兼容 Python 3.9 不支持的 'Z' 后缀与任意位数小数秒）。"""
    if isinstance(text, (int, float)):
        # Swift 默认 Date 编码为 2001-01-01 起的秒数；这里只在 ISO 失败时兜底
        return float(text) + 978307200.0
    if not isinstance(text, str):
        return default
    m = _ISO_RE.match(text.strip())
    if not m:
        return default
    y, mo, d, h, mi, s, frac, tz = m.groups()
    try:
        dt = datetime(int(y), int(mo), int(d), int(h), int(mi), int(s), tzinfo=timezone.utc)
    except ValueError:
        return default
    ts = dt.timestamp() + (float(frac) if frac else 0.0)
    if tz and tz != "Z":
        sign = 1 if tz[0] == "+" else -1
        digits = tz[1:].replace(":", "")
        ts -= sign * (int(digits[:2]) * 3600 + int(digits[2:]) * 60)
    return ts


def local_datetime(ts):
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ts))


def local_time(ts):
    return time.strftime("%H:%M:%S", time.localtime(ts))


def file_stamp(ts):
    return time.strftime("%Y%m%d_%H%M%S", time.localtime(ts))


def byte_string(n):
    """人类可读大小（十进制，与 macOS ByteCountFormatter .file 风格一致）。"""
    n = max(0, int(n))
    if n < 1000:
        return "%d 字节" % n
    for unit in ("KB", "MB", "GB", "TB"):
        n_f = n / 1000.0
        if n_f < 1000 or unit == "TB":
            return ("%.0f %s" if n_f >= 100 else "%.1f %s") % (n_f, unit)
        n = n_f
    return "%d" % n


def atomic_write_json(path, value):
    """原子写入：写临时文件 → fsync → os.replace，外部脚本 / AI Skill 永远读不到半截 JSON。"""
    path = str(path)
    directory = os.path.dirname(path) or "."
    os.makedirs(directory, exist_ok=True)
    data = json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True)
    fd, tmp = tempfile.mkstemp(prefix=".tmp-", suffix=".json", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def load_json(path):
    try:
        with open(str(path), "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


# ---------------- models ----------------


class ClipAttachment(object):
    __slots__ = ("id", "path", "added_at")

    def __init__(self, path, id=None, added_at=None):
        self.id = id or new_id()
        self.path = str(path)
        self.added_at = time.time() if added_at is None else float(added_at)

    @property
    def file_name(self):
        return os.path.basename(self.path)

    @property
    def exists(self):
        return os.path.exists(self.path)

    @property
    def size(self):
        try:
            return os.path.getsize(self.path)
        except OSError:
            return 0

    def to_json(self):
        return {"id": self.id, "path": self.path, "addedAt": iso_utc(self.added_at)}

    @classmethod
    def from_json(cls, d):
        if not isinstance(d, dict) or not isinstance(d.get("path"), str):
            return None
        return cls(d["path"], id=d.get("id") if isinstance(d.get("id"), str) else None,
                   added_at=parse_iso(d.get("addedAt"), time.time()))


class ClipItem(object):
    __slots__ = ("id", "timestamp", "source_app", "raw_content", "content_hash", "attachments", "is_selected")

    def __init__(self, source_app, raw_content, attachments=None, timestamp=None, id=None,
                 is_selected=True, content_hash=None):
        self.id = id or new_id()
        self.timestamp = time.time() if timestamp is None else float(timestamp)
        self.source_app = source_app
        self.raw_content = raw_content
        self.content_hash = content_hash or sha256(raw_content)
        self.attachments = list(attachments or [])
        self.is_selected = bool(is_selected)

    @property
    def is_file_only(self):
        return self.raw_content == "" and bool(self.attachments)

    @property
    def first_line(self):
        for line in self.raw_content.split("\n"):
            t = line.strip()
            if t:
                return t
        return ""

    def to_json(self):
        return {
            "id": self.id,
            "timestamp": iso_utc(self.timestamp),
            "sourceApp": self.source_app,
            "rawContent": self.raw_content,
            "contentHash": self.content_hash,
            "attachments": [a.to_json() for a in self.attachments],
            "isSelected": self.is_selected,
        }

    @classmethod
    def from_json(cls, d):
        if not isinstance(d, dict):
            return None
        raw = d.get("rawContent") if isinstance(d.get("rawContent"), str) else ""
        atts = []
        for a in d.get("attachments") or []:
            att = ClipAttachment.from_json(a)
            if att is not None:
                atts.append(att)
        return cls(
            source_app=d.get("sourceApp") if isinstance(d.get("sourceApp"), str) else "未知来源",
            raw_content=raw,
            attachments=atts,
            timestamp=parse_iso(d.get("timestamp"), time.time()),
            id=d.get("id") if isinstance(d.get("id"), str) else None,
            is_selected=d.get("isSelected", True) is not False,
            content_hash=d.get("contentHash") if isinstance(d.get("contentHash"), str) else None,
        )


class PendingFile(object):
    """新落地但尚未挂载的文件（双向 120 秒关联的缓冲池）。"""
    __slots__ = ("id", "path", "detected_at")

    def __init__(self, path, detected_at):
        self.id = new_id()
        self.path = str(path)
        self.detected_at = float(detected_at)

    @property
    def file_name(self):
        return os.path.basename(self.path)


GROK_ONLY = "grokOnly"
GROK_AND_BROWSERS = "grokAndBrowsers"
ALL_APPS = "allApps"
CAPTURE_SCOPES = [GROK_ONLY, GROK_AND_BROWSERS, ALL_APPS]
SCOPE_NAMES = {GROK_ONLY: "仅 Grok Bot", GROK_AND_BROWSERS: "Grok + 浏览器", ALL_APPS: "全部应用"}


class AppSettings(object):
    __slots__ = ("capture_scope", "watched_directories", "export_directory", "is_listening",
                 "auto_attach_window_seconds")

    def __init__(self, capture_scope=GROK_ONLY, watched_directories=None, export_directory=None,
                 is_listening=True, auto_attach_window_seconds=120.0):
        self.capture_scope = capture_scope if capture_scope in CAPTURE_SCOPES else GROK_ONLY
        self.watched_directories = (list(watched_directories) if watched_directories is not None
                                    else platform_paths.default_watched_directories())
        self.export_directory = export_directory or platform_paths.default_export_directory()
        self.is_listening = bool(is_listening)
        self.auto_attach_window_seconds = min(max(float(auto_attach_window_seconds), 10.0), 3600.0)

    def to_json(self):
        return {
            "captureScope": self.capture_scope,
            "watchedDirectories": list(self.watched_directories),
            "exportDirectory": self.export_directory,
            "isListening": self.is_listening,
            "autoAttachWindowSeconds": self.auto_attach_window_seconds,
        }

    @classmethod
    def from_json(cls, d):
        if not isinstance(d, dict):
            return cls()
        wd = d.get("watchedDirectories")
        if not (isinstance(wd, list) and all(isinstance(x, str) for x in wd)):
            wd = None
        ed = d.get("exportDirectory") if isinstance(d.get("exportDirectory"), str) else None
        w = d.get("autoAttachWindowSeconds")
        w = float(w) if isinstance(w, (int, float)) and not isinstance(w, bool) else 120.0
        return cls(capture_scope=d.get("captureScope"), watched_directories=wd, export_directory=ed,
                   is_listening=d.get("isListening", True) is not False, auto_attach_window_seconds=w)
