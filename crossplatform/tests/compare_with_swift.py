#!/usr/bin/env python3
"""用与 Swift Linux harness 完全相同的样例生成 Python 版空间，并与 Swift 输出逐字节比对
（只把时间相关字段归一化）。

用法：python3 tests/compare_with_swift.py <clip1.md> <python_out.html> [swift_harness_binary]
"""
import os
import re
import shutil
import subprocess
import sys
import tempfile
import base64

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from grokclip import space_html  # noqa: E402
from grokclip.models import ClipAttachment, ClipItem  # noqa: E402

PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAIAAAACCAYAAABytg0kAAAAFklEQVR4nGNkYPj/n4GBgYGJgYGBAQAj/gL+3n5zaQAAAABJRU5ErkJggg==")
CLIP2 = "## 代码块里包含 </script>\n\n```html\n<script>alert(\"xss\")</script>\n</script><!-- x -->\n```\n\u2028行\u2029段 & <b>"
TITLE = "测试 </title><script>alert(1)</script> 空间"
TIME_FIELDS = re.compile(r'"(timestamp|timeDisplay|createdAt|createdAtDisplay)":"[^"]*"')


def build_python(clip1_path, out_path):
    tmp = tempfile.mkdtemp(prefix="gc-py-")
    try:
        def w(name, data):
            p = os.path.join(tmp, name)
            with open(p, "wb") as f:
                f.write(data)
            return p
        png = w("示例图片.png", PNG)
        txt = w("notes.md", "# 附件笔记\n- </script><script>alert('x')</script>\n".encode("utf-8"))
        big = os.path.join(tmp, "big.zip")
        with open(big, "wb") as f:
            f.truncate(26 * 1024 * 1024)
        docx = w("报告.docx", bytes(i % 256 for i in range(4096)))
        with open(clip1_path, encoding="utf-8") as f:
            clip1 = f.read()
        items = [
            ClipItem("Grok Bot", clip1, [ClipAttachment(png), ClipAttachment(docx)]),
            ClipItem("Grok Bot <img src=x onerror=alert(1)>", CLIP2,
                     [ClipAttachment(txt), ClipAttachment(big), ClipAttachment("/nonexistent/gone.pdf")]),
            ClipItem("拖拽文件", "", [ClipAttachment(png)]),
        ]
        html, n, skipped = space_html.build_html(items, TITLE)
        with open(out_path, "wb") as f:
            f.write(html.encode("utf-8"))
        return html, n, skipped
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def main(argv):
    clip1, out = argv[0], argv[1]
    harness = argv[2] if len(argv) > 2 else None
    html, n, skipped = build_python(clip1, out)
    print("PY_SPACE_OK %s %d bytes, %d attachments (%d skipped)" % (out, len(html.encode("utf-8")), n, skipped))
    if not harness or not os.path.exists(harness):
        print("（未提供 Swift harness，跳过逐字节比对）")
        return 0
    swift_out = out + ".swift.html"
    r = subprocess.run([harness, swift_out, clip1], capture_output=True, text=True)
    if r.returncode != 0:
        print("Swift harness 失败：", r.stderr)
        return 1
    with open(swift_out, encoding="utf-8") as f:
        swift_html = f.read()
    a, b = TIME_FIELDS.sub(r'"\1":"T"', html), TIME_FIELDS.sub(r'"\1":"T"', swift_html)
    if a == b:
        print("BYTE_IDENTICAL_WITH_SWIFT（归一化时间字段后，%d 字节完全一致）" % len(a.encode("utf-8")))
        os.unlink(swift_out)
        return 0
    i = next((k for k in range(min(len(a), len(b))) if a[k] != b[k]), min(len(a), len(b)))
    print("与 Swift 输出不一致，首个差异位置 %d：\nPY   …%r\nSWIFT…%r" % (i, a[i - 80:i + 80], b[i - 80:i + 80]))
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
