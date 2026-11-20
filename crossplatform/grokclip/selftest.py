"""--self-test：不启动界面，生成示例空间并自检（与 Swift main.swift 的 SelfTest 对齐）。"""
import base64
import json
import os
import shutil
import struct
import sys
import tempfile
import zlib

from . import space_html
from .models import ClipAttachment, ClipItem

PAYLOAD_OPEN = '<script type="application/json" id="grokclip-payload">'

CLIP1 = """# GrokClip 自检 · 标题

这是一段**中文**原文，含 `inline code`、*斜体*、~~删除线~~ 和 [安全链接](https://example.com)，以及 [恶意链接](javascript:alert(1))。

| 功能 | 状态 |
| :--- | ---: |
| 收录 | ✅ |
| 导出 | ✅ |

- [x] 已完成
- [ ] 待办"""

CLIP2 = """## 代码块里包含 </script>

```html
<script>alert("xss")</script>
</script><!-- 不应提前闭合 -->
```

> 引用：<img src=x onerror=alert(1)>
\u2028行分隔符\u2029段分隔符"""


def make_sample_png(w=64, h=64):
    """纯标准库生成 64×64 渐变 PNG（indigo → violet，与 Swift 自检相同的配色）。"""
    rows = bytearray()
    for y in range(h):
        rows.append(0)
        for x in range(w):
            rows += bytes((79 + (139 - 79) * x // (w - 1), 70 + (92 - 70) * y // (h - 1),
                           229 + (246 - 229) * x // (w - 1), 255))

    def chunk(tag, data):
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(bytes(rows), 9)) + chunk(b"IEND", b""))


def extract_payload(html):
    s = html.index(PAYLOAD_OPEN) + len(PAYLOAD_OPEN)
    e = html.index("</script>", s)
    return json.loads(html[s:e])


def fail(msg):
    sys.stderr.write("SELF_TEST_FAIL %s\n" % msg)
    return 1


def run(output_path):
    tmp = tempfile.mkdtemp(prefix="GrokClipSelfTest-")
    try:
        png = make_sample_png()
        png_path = os.path.join(tmp, "示例图片.png")
        with open(png_path, "wb") as f:
            f.write(png)
        txt_path = os.path.join(tmp, "notes.md")
        with open(txt_path, "wb") as f:
            f.write("# 附件笔记\n\n- 中文内容\n- </script><script>alert('x')</script>\n".encode("utf-8"))
        items = [
            ClipItem("Grok Bot", CLIP1, [ClipAttachment(png_path)]),
            ClipItem("Grok Bot", CLIP2, [ClipAttachment(txt_path)]),
        ]
        html, _, _ = space_html.build_html(items, "GrokClip 自检空间")
        n = html.count("</script>")
        if n != 2:
            return fail("</script> 数量异常：%d" % n)
        p = extract_payload(html)
        if len(p["clips"]) != 2 or p["clips"][0]["rawContent"] != CLIP1 or p["clips"][1]["rawContent"] != CLIP2:
            return fail("原文往返不一致")
        a0 = p["clips"][0]["attachments"][0]
        if a0["kind"] != "image" or not a0["embedded"] or base64.b64decode(a0["base64"]) != png:
            return fail("图片附件内嵌失败")
        a1 = p["clips"][1]["attachments"][0]
        if a1["kind"] != "text" or "</script>" not in a1.get("textPreview", ""):
            return fail("文本附件预览失败")
        out = os.path.abspath(os.path.expanduser(output_path))
        os.makedirs(os.path.dirname(out), exist_ok=True)
        data = html.encode("utf-8")
        with open(out, "wb") as f:
            f.write(data)
        print("SELF_TEST_OK %s %d" % (out, len(data)))
        return 0
    except Exception as e:
        return fail(repr(e))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
