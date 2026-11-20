import base64
import json
import os
import unittest
from unittest import mock

from helpers import FakeClock, tempdir, write  # noqa: E402

from grokclip import space_html
from grokclip.models import ClipAttachment, ClipItem

HERE = os.path.dirname(os.path.abspath(__file__))
SWIFT = os.path.normpath(os.path.join(HERE, "..", "..", "Sources", "SpaceHTMLGenerator.swift"))
OPEN = '<script type="application/json" id="grokclip-payload">'


def payload_of(html):
    s = html.index(OPEN) + len(OPEN)
    return html[s:html.index("</script>", s)]


class TemplateTests(unittest.TestCase):
    @unittest.skipUnless(os.path.exists(SWIFT), "macOS Swift 源码不在旁边")
    def test_template_is_identical_to_swift(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location("sync", os.path.join(HERE, "..", "tools", "sync_template.py"))
        sync = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(sync)
        with open(SWIFT, encoding="utf-8") as f:
            swift_template = sync.extract(f.read())
        self.assertEqual(space_html.html_template(), swift_template)

    def test_template_placeholders(self):
        t = space_html.html_template()
        self.assertEqual(t.count("__GROKCLIP_PAYLOAD__"), 1)
        self.assertEqual(t.count("__GROKCLIP_TITLE__"), 2)
        self.assertNotRegex(t, r'(src|href)\s*=\s*["\']?https?:')


class EscapingTests(unittest.TestCase):
    def test_escape_rules_match_swift(self):
        self.assertEqual(space_html.escape_json_for_script('"</script>&<!--\u2028\u2029"'),
                         '"\\u003c\\/script\\u003e\\u0026\\u003c!--\\u2028\\u2029"')

    def test_payload_encoding_like_swift_jsonencoder(self):
        out = space_html.encode_payload_for_script_tag({"b": "a/b", "a": "中文 </script> <!-- &", "n": None,
                                                         "t": True, "i": 3})
        self.assertTrue(out.startswith('{"a":"中文 \\u003c\\/script\\u003e \\u003c!-- \\u0026"'))
        self.assertIn('"b":"a\\/b"', out)
        self.assertNotIn("</", out)
        self.assertNotIn("<", out)
        self.assertEqual(json.loads(out)["a"], "中文 </script> <!-- &")

    def test_escape_html(self):
        self.assertEqual(space_html.escape_html("<a href=\"x\">'&'</a>"),
                         "&lt;a href=&quot;x&quot;&gt;&#39;&amp;&#39;&lt;/a&gt;")


class NamingTests(unittest.TestCase):
    def test_sanitize_title_like_swift(self):
        cases = {
            "  ../a/b:c*?  ": "a_b_c",          # 与 Swift harness 输出一致
            "": "",
            "正常标题": "正常标题",
            "a\tb\nc\rd": "a_b_c_d",
            "x\u200by": "x_y",                  # Cf 控制字符
            "__..hi..__": "hi",
            'q"<>|w': "q_w",
            "长" * 80: "长" * 60,
        }
        for src, want in cases.items():
            self.assertEqual(space_html.sanitize_title(src), want, repr(src))

    def test_file_base_name_and_unique(self):
        d = tempdir(self)
        now = 1_800_000_000
        base = space_html.file_base_name("", now)
        self.assertRegex(base, r"^GrokSpace_\d{8}_\d{6}$")
        self.assertRegex(space_html.file_base_name("周报 / 草稿", now), r"^周报 _ 草稿_\d{8}_\d{6}$")
        p1 = space_html.unique_path(d, base, "html")
        write(p1, b"x")
        p2 = space_html.unique_path(d, base, "html")
        self.assertTrue(p2.endswith(base + "_2.html"))


class AttachmentTests(unittest.TestCase):
    def test_mime_and_kind(self):
        self.assertEqual(space_html.mime_type("PNG"), "image/png")
        self.assertEqual(space_html.mime_type("docx"),
                         "application/vnd.openxmlformats-officedocument.wordprocessingml.document")
        self.assertEqual(space_html.mime_type("zzzunknown"), "application/octet-stream")
        self.assertEqual(space_html.mime_type(""), "application/octet-stream")
        self.assertEqual(space_html.kind("jpeg"), "image")
        self.assertEqual(space_html.kind("pdf"), "pdf")
        self.assertEqual(space_html.kind("py"), "text")
        self.assertEqual(space_html.kind("docx"), "file")
        self.assertEqual(space_html.kind(""), "file")

    def test_build_attachment_variants(self):
        d = tempdir(self)
        png = write(os.path.join(d, "图.png"), b"\x89PNG....")
        md = write(os.path.join(d, "n.md"), "中文 </script>".encode("utf-8") + b"\xff")
        a, budget = space_html.build_attachment(png, 1000)
        self.assertEqual((a["kind"], a["embedded"], a["size"], budget), ("image", True, 8, 992))
        self.assertEqual(base64.b64decode(a["base64"]), b"\x89PNG....")
        self.assertNotIn("textPreview", a)
        self.assertNotIn("skipReason", a)
        a, _ = space_html.build_attachment(md, 1000)
        self.assertEqual(a["textPreview"], "中文 </script>\ufffd")
        a, _ = space_html.build_attachment(os.path.join(d, "missing.pdf"), 1000)
        self.assertEqual((a["embedded"], a["size"], a["skipReason"]), (False, 0, "原文件已不存在或无法读取，请单独发送原文件"))
        self.assertNotIn("base64", a)
        a, budget = space_html.build_attachment(md, 3)
        self.assertEqual(a["skipReason"], "附件总大小超过 80 MB 内嵌上限，请单独发送原文件")
        self.assertIn("textPreview", a)
        self.assertEqual(budget, 3)
        a, _ = space_html.build_attachment(d, 1000)
        self.assertFalse(a["embedded"])

    def test_caps(self):
        self.assertEqual(space_html.PER_FILE_EMBED_LIMIT, 25 * 1024 * 1024)
        self.assertEqual(space_html.TOTAL_EMBED_LIMIT, 80 * 1024 * 1024)
        d = tempdir(self)
        big = os.path.join(d, "big.zip")
        with open(big, "wb") as f:
            f.truncate(26 * 1024 * 1024)  # 稀疏文件，不占磁盘
        a, budget = space_html.build_attachment(big, space_html.TOTAL_EMBED_LIMIT)
        self.assertEqual((a["embedded"], a["skipReason"], a["size"]),
                         (False, "文件超过单文件内嵌上限，请单独发送原文件", 26 * 1024 * 1024))
        self.assertEqual(budget, space_html.TOTAL_EMBED_LIMIT)
        files = [write(os.path.join(d, "f%d.bin" % i), b"x" * 40) for i in range(3)]
        with mock.patch.object(space_html, "TOTAL_EMBED_LIMIT", 100):
            html, n, skipped = space_html.build_html([ClipItem("g", "t", [ClipAttachment(p) for p in files])], "t")
        atts = json.loads(payload_of(html))["clips"][0]["attachments"]
        self.assertEqual([x["embedded"] for x in atts], [True, True, False])
        self.assertEqual((n, skipped), (3, 1))


class BuildTests(unittest.TestCase):
    def test_build_html_structure(self):
        now = 1_800_000_000
        clips = [ClipItem("Grok <b>", "A </script><!-- & \u2028", timestamp=now - 5),
                 ClipItem("拖拽文件", "", [ClipAttachment("/nonexistent/x.pdf")], timestamp=now - 1)]
        html, n, skipped = space_html.build_html(clips, "  标题 </title><script>  ", now)
        self.assertEqual(html.count("</script>"), 2)
        self.assertIn("<title>标题 &lt;/title&gt;&lt;script&gt;</title>", html)
        p = json.loads(payload_of(html))
        self.assertEqual(sorted(p), ["clips", "createdAt", "createdAtDisplay", "generator", "title", "version"])
        self.assertEqual(p["generator"], "GrokClip Space 1.0")
        self.assertEqual([c["index"] for c in p["clips"]], [1, 2])
        self.assertEqual(p["clips"][0]["rawContent"], "A </script><!-- & \u2028")
        self.assertEqual(sorted(p["clips"][0]), ["attachments", "index", "rawContent", "sourceApp", "timeDisplay",
                                                 "timestamp"])
        self.assertRegex(p["createdAt"], r"Z$")
        self.assertEqual((n, skipped), (1, 1))

    def test_default_title(self):
        html, _, _ = space_html.build_html([ClipItem("g", "x")], "   ", 1_800_000_000)
        self.assertRegex(json.loads(payload_of(html))["title"], r"^GrokClip 空间 · \d{4}-\d\d-\d\d \d\d:\d\d:\d\d$")

    def test_write_space(self):
        d = tempdir(self)
        out = os.path.join(d, "a", "b")
        r = space_html.write_space([ClipItem("g", "x")], "", out, now=1_800_000_000)
        self.assertTrue(os.path.basename(r.path).startswith("GrokSpace_"))
        self.assertEqual(os.path.getsize(r.path), r.byte_size)
        self.assertEqual(os.listdir(out), [os.path.basename(r.path)])  # 无残留临时文件
        with self.assertRaises(space_html.SpaceGeneratorError):
            space_html.write_space([], "", out)


if __name__ == "__main__":
    unittest.main()
