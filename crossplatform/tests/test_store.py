import json
import os
import unittest
from unittest import mock

from helpers import FakeClock, tempdir, write  # noqa: E402

from grokclip import models
from grokclip.models import AppSettings, ClipItem
from grokclip.store import ClipStore


def make_store(tc, clock=None, window=120):
    d = tempdir(tc)
    clock = clock or FakeClock()
    s = ClipStore(data_dir=os.path.join(d, "data"), now_fn=clock)
    s.settings = AppSettings(watched_directories=[], export_directory=os.path.join(d, "out"),
                             auto_attach_window_seconds=window)
    s.events = []
    s.on_event = lambda k, p: s.events.append((k, p))
    return s, clock, d


class PersistenceTests(unittest.TestCase):
    def test_atomic_roundtrip_and_schema(self):
        s, clock, d = make_store(self)
        f = write(os.path.join(d, "a.txt"), b"hello")
        s.add_clip("第一段 </script>\u2028", "Grok Bot")
        s.attach_files([f], s.items[0].id)
        s2 = ClipStore(data_dir=s.data_dir, now_fn=clock)
        self.assertEqual(len(s2.items), 1)
        it = s2.items[0]
        self.assertEqual(it.raw_content, "第一段 </script>\u2028")
        self.assertEqual(it.source_app, "Grok Bot")
        self.assertEqual(it.content_hash, models.sha256(it.raw_content))
        self.assertEqual([a.file_name for a in it.attachments], ["a.txt"])
        with open(s.clips_path, encoding="utf-8") as fh:
            raw = json.load(fh)
        self.assertEqual(sorted(raw[0]), ["attachments", "contentHash", "id", "isSelected", "rawContent",
                                          "sourceApp", "timestamp"])
        self.assertRegex(raw[0]["timestamp"], r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$")
        self.assertEqual(raw[0]["id"], raw[0]["id"].upper())
        self.assertEqual(sorted(raw[0]["attachments"][0]), ["addedAt", "id", "path"])
        self.assertFalse([n for n in os.listdir(s.data_dir) if n.startswith(".tmp-")])

    def test_failed_write_keeps_previous_file(self):
        s, _, _ = make_store(self)
        s.add_clip("one", "Grok Bot")
        with open(s.clips_path, encoding="utf-8") as fh:
            before = fh.read()
        with mock.patch("grokclip.models.os.replace", side_effect=OSError("disk full")):
            s.add_clip("two", "Grok Bot")  # 保存失败只发出 error 事件，不抛异常
        with open(s.clips_path, encoding="utf-8") as fh:
            self.assertEqual(fh.read(), before)
        self.assertTrue(any(k == "error" for k, _ in s.events))
        self.assertFalse([n for n in os.listdir(s.data_dir) if n.startswith(".tmp-")])

    def test_settings_roundtrip_and_clamp(self):
        s, _, _ = make_store(self)
        s.set_auto_attach_window(99999)
        self.assertEqual(s.settings.auto_attach_window_seconds, 3600)
        s.set_capture_scope("grokAndBrowsers")
        loaded = AppSettings.from_json(models.load_json(s.settings_path))
        self.assertEqual(loaded.capture_scope, "grokAndBrowsers")
        self.assertEqual(loaded.auto_attach_window_seconds, 3600)
        self.assertEqual(AppSettings.from_json({"captureScope": "bogus", "autoAttachWindowSeconds": 1}).capture_scope,
                         "grokOnly")
        self.assertEqual(AppSettings.from_json({"autoAttachWindowSeconds": 1}).auto_attach_window_seconds, 10)

    def test_reads_swift_written_json(self):
        swift_json = [{"attachments": [{"addedAt": "2026-11-20T06:25:00Z", "id": "A1", "path": "/tmp/x.png"}],
                       "contentHash": "abc", "id": "B2", "isSelected": False, "rawContent": "hi",
                       "sourceApp": "Grok Bot", "timestamp": "2026-11-20T06:25:01Z"}]
        it = ClipItem.from_json(swift_json[0])
        self.assertFalse(it.is_selected)
        self.assertEqual(models.iso_utc(it.timestamp), "2026-11-20T06:25:01Z")
        self.assertEqual(it.attachments[0].path, "/tmp/x.png")
        self.assertEqual(models.parse_iso("2026-11-20T14:25:01.5+08:00"), models.parse_iso("2026-11-20T06:25:01Z") + 0.5)

    def test_corrupt_json_is_ignored(self):
        s, clock, _ = make_store(self)
        os.makedirs(s.data_dir, exist_ok=True)
        with open(s.clips_path, "w") as f:
            f.write("{not json")
        s2 = ClipStore(data_dir=s.data_dir, now_fn=clock)
        self.assertEqual(s2.items, [])


class CaptureTests(unittest.TestCase):
    def test_dedup_against_latest(self):
        s, clock, _ = make_store(self)
        self.assertIsNotNone(s.add_clip("A", "Grok Bot"))
        self.assertIsNone(s.add_clip("A", "Grok Bot"))
        clock.advance(1)
        s.add_clip("B", "Grok Bot")
        clock.advance(1)
        self.assertIsNotNone(s.add_clip("A", "Grok Bot"))  # 只与最近一条比较
        self.assertEqual([i.raw_content for i in s.items], ["A", "B", "A"])

    def test_chronological_order_and_reorder(self):
        s, clock, _ = make_store(self)
        for t in "ABC":
            s.add_clip(t, "Grok Bot")
            clock.advance(1)
        ids = [i.id for i in s.items]
        s.move_up(ids[2])
        self.assertEqual([i.raw_content for i in s.items], ["A", "C", "B"])
        s.move_down(ids[0])
        self.assertEqual([i.raw_content for i in s.items], ["C", "A", "B"])
        s.move_up(s.items[0].id)  # 越界无操作
        self.assertEqual([i.raw_content for i in s.items], ["C", "A", "B"])
        self.assertEqual(s.items[s.latest_item_index()].raw_content, "C")  # 按收录时间而非位置

    def test_latest_is_by_time_not_position(self):
        s, clock, _ = make_store(self)
        s.add_clip("A", "x")
        clock.advance(1)
        s.add_clip("B", "x")
        s.move_up(s.items[1].id)
        self.assertEqual(s.items[s.latest_item_index()].raw_content, "B")

    def test_selection_delete_clear_and_raw_join(self):
        s, clock, _ = make_store(self)
        resets = []
        s.on_dedup_reset = lambda: resets.append(1)
        for t in ("一", "", "三"):
            if t:
                s.add_clip(t, "Grok Bot")
            clock.advance(1)
        s.set_selected(s.items[0].id, False)
        self.assertEqual(s.selected_raw_text(), ("三", 1))
        s.set_all_selected(True)
        self.assertEqual(s.selected_raw_text(), ("一\n\n三", 2))
        self.assertEqual(ClipStore.joined_raw_text(s.items, "\n\n---\n\n"), "一\n\n---\n\n三")
        s.delete(s.items[0].id)
        self.assertEqual(len(s.items), 1)
        s.clear_all()
        self.assertEqual(s.items, [])
        self.assertEqual(len(resets), 2)


class AssociationTests(unittest.TestCase):
    """120 秒双向关联（假时钟）。"""

    def test_forward_attach_within_window(self):
        s, clock, d = make_store(self)
        s.add_clip("text", "Grok Bot")
        clock.advance(119)
        f = write(os.path.join(d, "dl", "a.pdf"))
        self.assertEqual(s.handle_landed_file(f), "attached")
        self.assertEqual([a.file_name for a in s.items[0].attachments], ["a.pdf"])
        self.assertEqual(s.handle_landed_file(f), "ignored")  # 同一文件不重复挂载

    def test_forward_outside_window_goes_pending_then_backward_claim(self):
        s, clock, d = make_store(self)
        s.add_clip("old", "Grok Bot")
        clock.advance(121)
        f = write(os.path.join(d, "dl", "b.png"))
        self.assertEqual(s.handle_landed_file(f), "pending")
        self.assertEqual(len(s.pending), 1)
        self.assertEqual(s.items[0].attachments, [])
        clock.advance(60)
        item = s.add_clip("new", "Grok Bot")
        self.assertEqual([a.file_name for a in item.attachments], ["b.png"])
        self.assertEqual(s.pending, [])
        self.assertEqual(s.events[-1], ("captured", {"index": 2, "auto_attached": 1}))

    def test_file_before_any_text_is_claimed(self):
        s, clock, d = make_store(self)
        f = write(os.path.join(d, "dl", "c.docx"))
        self.assertEqual(s.handle_landed_file(f), "pending")
        clock.advance(100)
        item = s.add_clip("summary", "Grok Bot")
        self.assertEqual(len(item.attachments), 1)

    def test_pending_expires_and_deleted_files_are_dropped(self):
        s, clock, d = make_store(self)
        f1 = write(os.path.join(d, "dl", "old.txt"))
        f2 = write(os.path.join(d, "dl", "gone.txt"))
        s.handle_landed_file(f1)
        s.handle_landed_file(f2)
        os.unlink(f2)
        clock.advance(121)
        item = s.add_clip("late", "Grok Bot")
        self.assertEqual(item.attachments, [])
        self.assertEqual(s.pending, [])

    def test_custom_window(self):
        s, clock, d = make_store(self, window=10)
        s.add_clip("t", "Grok Bot")
        clock.advance(11)
        f = write(os.path.join(d, "dl", "x.bin"))
        self.assertEqual(s.handle_landed_file(f), "pending")
        clock.advance(5)
        self.assertEqual(len(s.add_clip("u", "Grok Bot").attachments), 1)

    def test_exported_space_is_never_attached(self):
        s, clock, d = make_store(self)
        s.add_clip("t", "Grok Bot")
        r = s.export_space()
        self.assertEqual(s.handle_landed_file(r.path), "ignored")
        self.assertEqual(s.items[0].attachments, [])

    def test_manual_attach_pending_and_remove(self):
        s, clock, d = make_store(self)
        f = write(os.path.join(d, "dl", "m.zip"))
        s.handle_landed_file(f)
        s.attach_pending_to_latest(s.pending[0].id)  # 收集篮为空 → 新建“仅附件”条目
        self.assertEqual(len(s.items), 1)
        self.assertTrue(s.items[0].is_file_only)
        att = s.items[0].attachments[0]
        s.remove_attachment(s.items[0].id, att.id)
        self.assertEqual(s.items, [])  # 仅附件条目没有附件后自动删除
        self.assertTrue(os.path.exists(f))  # 磁盘文件不删除

    def test_attach_files_skips_dirs_and_duplicates(self):
        s, clock, d = make_store(self)
        s.add_clip("t", "Grok Bot")
        f = write(os.path.join(d, "dl", "a.md"))
        self.assertEqual(s.attach_files([f, f, os.path.join(d, "dl")], s.items[0].id), 1)
        self.assertEqual(s.attach_files([f], s.items[0].id), 0)


if __name__ == "__main__":
    unittest.main()
