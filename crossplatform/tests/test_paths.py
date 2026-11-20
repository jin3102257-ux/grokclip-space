import ctypes
import os
import unittest
from unittest import mock

from helpers import tempdir  # noqa: E402

from grokclip import platform_paths as pp


class PathTests(unittest.TestCase):
    def test_data_dir_override_and_xdg(self):
        d = tempdir(self)
        with mock.patch.dict(os.environ, {"GROKCLIP_DATA_DIR": os.path.join(d, "x")}):
            self.assertEqual(str(pp.data_dir()), os.path.join(d, "x"))
        env = {"XDG_DATA_HOME": os.path.join(d, "xdg")}
        with mock.patch.dict(os.environ, env), mock.patch.object(pp, "IS_WINDOWS", False), \
                mock.patch.object(pp, "IS_MAC", False):
            os.environ.pop("GROKCLIP_DATA_DIR", None)
            self.assertEqual(str(pp.data_dir()), os.path.join(d, "xdg", "GrokClipSpace"))
        with mock.patch.dict(os.environ, {"APPDATA": os.path.join(d, "Roaming")}), \
                mock.patch.object(pp, "IS_WINDOWS", True):
            os.environ.pop("GROKCLIP_DATA_DIR", None)
            self.assertEqual(str(pp.data_dir(create=False)), os.path.join(d, "Roaming", "GrokClipSpace"))

    def test_defaults_use_grok_bot_when_present(self):
        d = tempdir(self)
        with mock.patch.object(pp, "home", return_value=pp.Path(d)), \
                mock.patch.object(pp, "_xdg_user_dir", return_value=None):
            self.assertEqual(pp.default_watched_directories(), [os.path.join(d, "Downloads")])
            self.assertEqual(pp.default_export_directory(), os.path.join(d, "GrokSpaces"))
            os.makedirs(os.path.join(d, "Desktop"))
            self.assertEqual(pp.default_export_directory(), os.path.join(d, "Desktop", "GrokSpaces"))
            os.makedirs(os.path.join(d, "grok bot"))
            self.assertEqual(pp.default_watched_directories(), [os.path.join(d, "Downloads"), os.path.join(d, "grok bot")])
            self.assertEqual(pp.default_export_directory(), os.path.join(d, "grok bot"))

    def test_xdg_user_dirs_file_fallback(self):
        d = tempdir(self)
        os.makedirs(os.path.join(d, ".config"))
        with open(os.path.join(d, ".config", "user-dirs.dirs"), "w") as f:
            f.write('XDG_DOWNLOAD_DIR="$HOME/下载"\nXDG_DESKTOP_DIR="$HOME"\n')
        with mock.patch.object(pp, "home", return_value=pp.Path(d)), mock.patch.object(pp, "IS_LINUX", True), \
                mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": os.path.join(d, ".config")}), \
                mock.patch("subprocess.run", side_effect=OSError):
            self.assertEqual(str(pp._xdg_user_dir("DOWNLOAD")), os.path.join(d, "下载"))
            self.assertIsNone(pp._xdg_user_dir("DESKTOP"))  # 指向 $HOME 视为未设置

    def test_windows_known_folder_simulation(self):
        """模拟 SHGetKnownFolderPath：验证 GUID 打包、PWSTR 读取与 CoTaskMemFree 释放。"""
        seen = {}
        keep = ctypes.create_unicode_buffer("D:\\用户\\下载")

        class Fn(object):
            def __init__(self, impl):
                self.impl = impl

            def __call__(self, *a):
                return self.impl(*a)

        def sh(guid_ref, flags, token, out_ref):
            g = guid_ref._obj
            seen["guid"] = "%08X-%04X-%04X-%s" % (g.Data1, g.Data2, g.Data3, bytes(g.Data4).hex().upper())
            out_ref._obj.value = ctypes.addressof(keep)
            return 0

        fns = {"SHGetKnownFolderPath": Fn(sh), "CoTaskMemFree": Fn(lambda p: seen.setdefault("freed", p))}

        class D(object):
            def __init__(self, *a, **k):
                pass

            def __getattr__(self, n):
                return fns[n]

        with mock.patch.object(pp, "IS_WINDOWS", True), mock.patch.object(ctypes, "WinDLL", D, create=True):
            p = pp._win_known_folder(pp.FOLDERID_DOWNLOADS)
        self.assertEqual(str(p), "D:\\用户\\下载")
        self.assertEqual(seen["guid"], "374DE290-123F-4565-916439C4925E467B")
        self.assertEqual(seen["freed"], ctypes.addressof(keep))
        f = fns["SHGetKnownFolderPath"]
        self.assertEqual(len(f.argtypes), 4)
        self.assertIs(f.restype, ctypes.c_long)  # HRESULT
        self.assertEqual(fns["CoTaskMemFree"].argtypes, [ctypes.c_void_p])


if __name__ == "__main__":
    unittest.main()
