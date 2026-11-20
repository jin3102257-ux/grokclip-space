"""离线单文件 HTML 空间生成器 —— SpaceHTMLGenerator.swift 的逐行移植。

模板文件 space_template.html 由 tools/sync_template.py 从 Swift 源码逐字提取，
两端共用同一份模板；tests/test_space_html.py 会断言二者完全一致。
"""
import base64
import json
import mimetypes
import os
import time
import unicodedata

from . import models

PER_FILE_EMBED_LIMIT = 25 * 1024 * 1024
TOTAL_EMBED_LIMIT = 80 * 1024 * 1024
TEXT_PREVIEW_LIMIT = 512 * 1024
GENERATOR = "GrokClip Space 1.0"

IMAGE_EXTENSIONS = {"png", "jpg", "jpeg", "gif", "webp", "svg", "bmp", "ico", "avif"}
TEXT_EXTENSIONS = {
    "md", "markdown", "txt", "text", "csv", "tsv", "json", "jsonl", "yaml", "yml", "xml", "toml", "ini",
    "log", "html", "htm", "css", "js", "mjs", "ts", "tsx", "jsx", "py", "swift", "sh", "zsh", "bash",
    "rb", "go", "rs", "java", "kt", "c", "h", "cpp", "hpp", "m", "sql", "r", "php", "vue", "env", "conf",
}
FALLBACK_MIME_TYPES = {
    "png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg", "gif": "image/gif",
    "webp": "image/webp", "svg": "image/svg+xml", "bmp": "image/bmp", "ico": "image/x-icon",
    "avif": "image/avif", "pdf": "application/pdf",
    "md": "text/markdown", "markdown": "text/markdown", "txt": "text/plain", "csv": "text/csv",
    "tsv": "text/tab-separated-values", "json": "application/json", "html": "text/html",
    "xml": "application/xml", "yaml": "text/yaml", "yml": "text/yaml",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    "xls": "application/vnd.ms-excel", "doc": "application/msword", "ppt": "application/vnd.ms-powerpoint",
    "zip": "application/zip", "mp4": "video/mp4", "mov": "video/quicktime", "mp3": "audio/mpeg",
    "wav": "audio/wav",
}
_TEXTUAL_APP_MIMES = {"application/json", "application/xml", "application/javascript", "application/x-sh",
                      "application/x-python", "application/toml", "application/x-yaml", "application/sql"}

_TEMPLATE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "space_template.html")
_template_cache = None


class SpaceGeneratorError(Exception):
    pass


def html_template():
    global _template_cache
    if _template_cache is None:
        with open(_TEMPLATE_PATH, "r", encoding="utf-8", newline="") as f:
            _template_cache = f.read()
    return _template_cache


# ---------------- escaping ----------------

def escape_json_for_script(raw):
    """与 Swift escapeJSONForScript 完全相同的规则：`</`→`<\\/`，`<` `>` `&` U+2028 U+2029 → \\uXXXX。"""
    out = raw.replace("</", "<\\/")
    out = out.replace("<", "\\u003c")
    out = out.replace(">", "\\u003e")
    out = out.replace("&", "\\u0026")
    out = out.replace("\u2028", "\\u2028")
    out = out.replace("\u2029", "\\u2029")
    return out


def encode_payload_for_script_tag(value):
    """对齐 Swift JSONEncoder(.sortedKeys)：紧凑输出、键排序、非 ASCII 原样、`/` 转义为 `\\/`。"""
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    # Swift Foundation 默认把 "/" 编码为 "\/"；JSON 转义序列里从不出现 "/"，整体替换是安全的
    raw = raw.replace("/", "\\/")
    return escape_json_for_script(raw)


def escape_html(s):
    o = s.replace("&", "&amp;")
    o = o.replace("<", "&lt;")
    o = o.replace(">", "&gt;")
    o = o.replace('"', "&quot;")
    o = o.replace("'", "&#39;")
    return o


# ---------------- attachments ----------------

def _ext_of(path):
    name = os.path.basename(path)
    if name.startswith(".") and name.count(".") == 1:
        return ""
    _, ext = os.path.splitext(name)
    return ext[1:].lower() if ext else ""


def mime_type(ext):
    lower = ext.lower()
    if lower in FALLBACK_MIME_TYPES:
        return FALLBACK_MIME_TYPES[lower]
    if lower:
        guess, _ = mimetypes.guess_type("x." + lower, strict=False)
        if guess:
            return guess
    return "application/octet-stream"


def kind(ext):
    lower = ext.lower()
    if lower in IMAGE_EXTENSIONS:
        return "image"
    if lower == "pdf":
        return "pdf"
    if lower in TEXT_EXTENSIONS:
        return "text"
    if lower:
        guess, _ = mimetypes.guess_type("x." + lower, strict=False)
        if guess:
            if guess == "application/pdf":
                return "pdf"
            if guess.startswith("text/") or guess in _TEXTUAL_APP_MIMES:
                return "text"
    return "file"


def build_attachment(path, budget):
    """返回 (attachment_dict, new_budget)。字段为 None 时省略（与 Swift Codable 对 nil 的处理一致）。"""
    name = os.path.basename(path)
    ext = _ext_of(path)
    mime = mime_type(ext)
    file_kind = kind(ext)
    try:
        size = os.stat(path).st_size
    except OSError:
        size = -1

    def skipped(reason, preview=None):
        d = {"name": name, "ext": ext, "mimeType": mime, "size": max(size, 0), "kind": file_kind,
             "embedded": False, "skipReason": reason}
        if preview is not None:
            d["textPreview"] = preview
        return d

    if size < 0:
        return skipped("原文件已不存在或无法读取，请单独发送原文件"), budget
    if size > PER_FILE_EMBED_LIMIT:
        return skipped("文件超过单文件内嵌上限，请单独发送原文件"), budget
    try:
        with open(path, "rb") as f:
            data = f.read(PER_FILE_EMBED_LIMIT + 1)
    except OSError:
        return skipped("原文件无法读取（可能缺少访问权限），请单独发送原文件"), budget
    if len(data) > PER_FILE_EMBED_LIMIT:  # 读取期间文件变大
        return skipped("文件超过单文件内嵌上限，请单独发送原文件"), budget

    preview = None
    if file_kind == "text" and len(data) <= TEXT_PREVIEW_LIMIT:
        preview = data.decode("utf-8", errors="replace")

    if len(data) > budget:
        return skipped("附件总大小超过 80 MB 内嵌上限，请单独发送原文件", preview), budget
    budget -= len(data)

    d = {"name": name, "ext": ext, "mimeType": mime, "size": len(data), "kind": file_kind,
         "embedded": True, "base64": base64.b64encode(data).decode("ascii")}
    if preview is not None:
        d["textPreview"] = preview
    return d, budget


# ---------------- build ----------------

def build_html(clips, title, now=None):
    """纯函数：返回 (html, attachment_count, skipped_count)。clips 为 models.ClipItem 列表。"""
    now = time.time() if now is None else now
    trimmed = title.strip()
    display_title = trimmed if trimmed else "GrokClip 空间 · %s" % models.local_datetime(now)

    budget = TOTAL_EMBED_LIMIT
    attachment_count = 0
    skipped = 0
    space_clips = []
    for i, clip in enumerate(clips):
        atts = []
        for att in clip.attachments:
            built, budget = build_attachment(att.path, budget)
            attachment_count += 1
            if not built["embedded"]:
                skipped += 1
            atts.append(built)
        space_clips.append({
            "index": i + 1,
            "timestamp": models.iso_utc(clip.timestamp),
            "timeDisplay": models.local_datetime(clip.timestamp),
            "sourceApp": clip.source_app,
            "rawContent": clip.raw_content,
            "attachments": atts,
        })

    payload = {
        "version": 1,
        "title": display_title,
        "createdAt": models.iso_utc(now),
        "createdAtDisplay": models.local_datetime(now),
        "generator": GENERATOR,
        "clips": space_clips,
    }
    js = encode_payload_for_script_tag(payload)

    template = html_template()
    parts = template.split("__GROKCLIP_PAYLOAD__")
    esc_title = escape_html(display_title)
    head = parts[0].replace("__GROKCLIP_TITLE__", esc_title)
    tail = "".join(parts[1:]).replace("__GROKCLIP_TITLE__", esc_title)
    return head + js + tail, attachment_count, skipped


# ---------------- file naming ----------------

_ILLEGAL = set('/\\:*?"<>|\n\r\t')


def _is_illegal(ch):
    return ch in _ILLEGAL or unicodedata.category(ch) in ("Cc", "Cf")


def _is_trim(ch):
    return ch in "._\t" or unicodedata.category(ch) == "Zs"


def sanitize_title(title):
    cleaned = "".join("_" if _is_illegal(c) else c for c in title)
    start, end = 0, len(cleaned)
    while start < end and _is_trim(cleaned[start]):
        start += 1
    while end > start and _is_trim(cleaned[end - 1]):
        end -= 1
    cleaned = cleaned[start:end]
    while "__" in cleaned:
        cleaned = cleaned.replace("__", "_")
    if len(cleaned) > 60:
        cleaned = cleaned[:60]
    return cleaned


def file_base_name(title, now):
    stamp = models.file_stamp(now)
    clean = sanitize_title(title)
    return "%s_%s" % (clean, stamp) if clean else "GrokSpace_%s" % stamp


def unique_path(directory, base_name, ext):
    candidate = os.path.join(directory, "%s.%s" % (base_name, ext))
    n = 2
    while os.path.exists(candidate):
        candidate = os.path.join(directory, "%s_%d.%s" % (base_name, n, ext))
        n += 1
    return candidate


class SpaceExportResult(object):
    __slots__ = ("path", "clip_count", "attachment_count", "skipped_attachment_count", "byte_size")

    def __init__(self, path, clip_count, attachment_count, skipped_attachment_count, byte_size):
        self.path = path
        self.clip_count = clip_count
        self.attachment_count = attachment_count
        self.skipped_attachment_count = skipped_attachment_count
        self.byte_size = byte_size


def write_space(clips, title, export_directory, now=None):
    if not clips:
        raise SpaceGeneratorError("没有勾选任何条目")
    now = time.time() if now is None else now
    try:
        os.makedirs(export_directory, exist_ok=True)
    except OSError:
        raise SpaceGeneratorError("无法创建导出目录：%s" % export_directory)
    html, att_count, skipped = build_html(clips, title, now)
    path = unique_path(export_directory, file_base_name(title, now), "html")
    data = html.encode("utf-8")
    # 隐藏临时文件（以 . 开头）：文件监听会忽略它，写完再原子改名
    tmp = os.path.join(os.path.dirname(path), "." + os.path.basename(path) + ".tmp")
    try:
        with open(tmp, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except OSError as e:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise SpaceGeneratorError("写入失败：%s" % e)
    return SpaceExportResult(path, len(clips), att_count, skipped, len(data))
