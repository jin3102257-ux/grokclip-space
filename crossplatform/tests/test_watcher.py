import os
import unittest

from helpers import tempdir, write  # noqa: E402

from grokclip.watcher import DownloadWatcher, should_ignore


class FilterTests(unittest.TestCase):
    def test_should_ignore(self):
        ignored = ["a.crdownload", "b.download", "c.tmp", "d.part", "e.partial", "f.opdownload", "~$x.docx",
                   ".DS_Store", ".hidden", "GrokSpace_20261120_101010.html", "", "desktop.ini", "Thumbs.db",
                   "A.CRDOWNLOAD"]
        kept = ["ok.pdf", "GrokSpace_1.md", "report.html", "图片.png", "notes.TXT", "my GrokSpace_x.html"]
        for n in ignored:
            self.assertTrue(should_ignore(n), n)
        for n in kept:
            self.assertFalse(should_ignore(n), n)


class WatcherTests(unittest.TestCase):
    def setUp(self):
        self.d = tempdir(self)
        self.dl = os.path.join(self.d, "Downloads")
        os.makedirs(self.dl)
        write(os.path.join(self.dl, "existing.pdf"), b"old")
        self.landed = []
        self.w = DownloadWatcher(on_new_file=self.landed.append)
        self.w.set_directories([self.dl, os.path.join(self.d, "missing")])
        self.w.tick()  # 基线

    def test_existing_files_are_baseline(self):
        self.assertEqual(self.w.tick(), [])
        self.assertEqual(self.landed, [])

    def test_size_stability_required(self):
        p = os.path.join(self.dl, "big.zip")
        write(p, b"a" * 10)
        self.assertEqual(self.w.tick(), [])      # 首次发现：记录大小
        with open(p, "ab") as f:
            f.write(b"b" * 10)
        self.assertEqual(self.w.tick(), [])      # 仍在增长
        self.assertEqual(self.w.tick(), [p])     # 连续两次大小一致 → 写入完成
        self.assertEqual(self.landed, [p])
        self.assertEqual(self.w.tick(), [])      # 只报告一次

    def test_empty_file_waits_until_non_empty(self):
        p = os.path.join(self.dl, "e.txt")
        write(p, b"")
        self.w.tick()
        self.assertEqual(self.w.tick(), [])
        write(p, b"data")
        self.w.tick()
        self.assertEqual(self.w.tick(), [p])

    def test_temp_download_then_rename(self):
        tmp = os.path.join(self.dl, "x.pdf.crdownload")
        write(tmp, b"1234")
        self.w.tick()
        self.w.tick()
        self.assertEqual(self.landed, [])
        final = os.path.join(self.dl, "x.pdf")
        os.replace(tmp, final)
        self.w.tick()
        self.assertEqual(self.w.tick(), [final])

    def test_ignored_path_hidden_and_dirs(self):
        own = os.path.join(self.dl, "我的空间_20261120_101010.html")
        self.w.ignore(own)
        write(own, b"<html>")
        write(os.path.join(self.dl, ".secret"), b"s")
        os.makedirs(os.path.join(self.dl, "subdir"))
        self.w.tick()
        self.assertEqual(self.w.tick(), [])

    def test_deleted_before_stable(self):
        p = os.path.join(self.dl, "gone.bin")
        write(p, b"1")
        self.w.tick()
        os.unlink(p)
        self.assertEqual(self.w.tick(), [])

    def test_directory_added_later_and_removed(self):
        g = os.path.join(self.d, "grok bot")
        os.makedirs(g)
        write(os.path.join(g, "pre.md"), b"pre")
        self.w.set_directories([self.dl, g])
        self.w.tick()  # g 的基线
        p = write(os.path.join(g, "new.md"), b"new")
        self.w.tick()
        self.assertEqual(self.w.tick(), [p])
        self.w.set_directories([self.dl])
        write(os.path.join(g, "after.md"), b"x")
        self.w.tick()
        self.assertEqual(self.w.tick(), [])

    def test_thread_start_stop(self):
        self.w.interval = 0.05
        self.w.start()
        p = write(os.path.join(self.dl, "t.txt"), b"abc")
        import time
        end = time.time() + 3
        while time.time() < end and p not in self.landed:
            time.sleep(0.05)
        self.w.stop()
        self.assertIn(p, self.landed)


if __name__ == "__main__":
    unittest.main()
