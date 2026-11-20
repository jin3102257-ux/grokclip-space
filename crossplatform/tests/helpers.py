import os
import sys
import tempfile

PKG_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PKG_DIR not in sys.path:
    sys.path.insert(0, PKG_DIR)


class FakeClock(object):
    def __init__(self, t=1_800_000_000.0):
        self.t = float(t)

    def __call__(self):
        return self.t

    def advance(self, s):
        self.t += s


def tempdir(testcase):
    d = tempfile.mkdtemp(prefix="grokclip-test-")
    import shutil
    testcase.addCleanup(shutil.rmtree, d, True)
    return d


def write(path, data=b"x"):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(data if isinstance(data, bytes) else data.encode("utf-8"))
    return path
