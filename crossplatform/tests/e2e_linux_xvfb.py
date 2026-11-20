#!/usr/bin/env python3
"""Linux 端到端测试：在 Xvfb 虚拟桌面里真实启动 Tk 收集端，模拟复制、下载、导出、写剪贴板并截图。

依赖（仅测试机需要）：xvfb xclip xdotool imagemagick openbox python3-tk
用法：python3 tests/e2e_linux_xvfb.py [截图输出路径]
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
PKG_DIR = os.path.dirname(HERE)
DISPLAY = ":97"
SHOT = sys.argv[1] if len(sys.argv) > 1 else os.path.join(tempfile.gettempdir(), "grokclip_linux_ui.png")

results = []


def check(name, cond, extra=""):
    results.append((name, bool(cond)))
    print(("  ✓ " if cond else "  ✗ ") + name + ("" if cond else "  → " + str(extra)))


def sh(args, env, input_bytes=None, timeout=10):
    r = subprocess.run(args, env=env, input=input_bytes, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                       timeout=timeout)
    return r.returncode, r.stdout


def xclip_set(text, env):
    p = subprocess.Popen(["xclip", "-selection", "clipboard", "-i"], env=env, stdin=subprocess.PIPE,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    p.communicate(text.encode("utf-8"))


def activate(title, env):
    wid = None
    for _ in range(25):
        rc, out = sh(["xdotool", "search", "--onlyvisible", "--name", title], env, timeout=5)
        if out.split():
            wid = out.split()[-1].decode()
            break
        time.sleep(0.2)
    if wid:
        sh(["xdotool", "windowactivate", "--sync", wid], env)
        time.sleep(0.3)
    return wid


def load_items(data_dir):
    try:
        with open(os.path.join(data_dir, "clips_store.json"), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return []


def wait_for(fn, timeout=8.0, step=0.2):
    end = time.time() + timeout
    while time.time() < end:
        v = fn()
        if v:
            return v
        time.sleep(step)
    return fn()


def main():
    for tool in ("Xvfb", "xclip", "xdotool", "import"):
        if not shutil.which(tool):
            print("缺少测试依赖：", tool)
            return 2
    tmp = tempfile.mkdtemp(prefix="grokclip-e2e-")
    home = os.path.join(tmp, "home")
    downloads = os.path.join(home, "Downloads")
    grokdir = os.path.join(home, "grok bot")
    data_dir = os.path.join(home, ".local", "share", "GrokClipSpace")
    for d in (downloads, grokdir, data_dir):
        os.makedirs(d)
    # 10 秒窗口，便于在测试里覆盖“先下载、后复制”的反向认领
    with open(os.path.join(data_dir, "settings.json"), "w", encoding="utf-8") as f:
        json.dump({"captureScope": "grokOnly", "watchedDirectories": [downloads, grokdir],
                   "exportDirectory": grokdir, "isListening": True, "autoAttachWindowSeconds": 10}, f)

    env = dict(os.environ)
    env.update({"DISPLAY": DISPLAY, "HOME": home, "XDG_DATA_HOME": os.path.join(home, ".local", "share"),
                "PYTHONPATH": PKG_DIR, "LANG": "C.UTF-8"})
    env.pop("WAYLAND_DISPLAY", None)
    env.pop("GROKCLIP_DATA_DIR", None)
    procs = []

    def spawn(args, **kw):
        p = subprocess.Popen(args, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, **kw)
        procs.append(p)
        return p

    try:
        spawn(["Xvfb", DISPLAY, "-screen", "0", "1440x900x24", "-nolisten", "tcp"])
        time.sleep(1.2)
        if shutil.which("openbox"):
            spawn(["openbox"])
            time.sleep(0.8)
        # 背景“Grok Bot”窗口与一个普通“记事本”窗口
        spawn([sys.executable, os.path.join(HERE, "fake_window.py"), "Notes - 记事本"])
        spawn([sys.executable, os.path.join(HERE, "fake_window.py"), "Grok Bot — 对话"])
        time.sleep(1.0)

        app = subprocess.Popen([sys.executable, "-m", "grokclip"], cwd=PKG_DIR, env=env,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        procs.append(app)
        time.sleep(2.5)
        check("Tk 收集端已在 Xvfb 中启动并保持运行", app.poll() is None,
              app.stderr.read().decode() if app.poll() is not None else "")

        print("\n[1] 剪贴板收录（前台 = Grok Bot）")
        activate("Grok Bot", env)
        t1 = "# 第一段\n\n这是 **Grok** 的回复，含表格：\n\n| a | b |\n|---|---|\n| 1 | 2 |\n\n```html\n</script>\n```\n"
        xclip_set(t1, env)
        items = wait_for(lambda: load_items(data_dir) if len(load_items(data_dir)) >= 1 else None)
        check("复制后 1.5 秒内被收录", items and len(items) == 1, items)
        check("原文逐字一致（含结尾换行）", items and items[0]["rawContent"] == t1)
        check("来源识别为 Grok Bot", items and items[0]["sourceApp"] == "Grok Bot", items and items[0]["sourceApp"])

        xclip_set(t1, env)
        time.sleep(1.5)
        check("重复复制同一内容不会重复收录（SHA-256 去重）", len(load_items(data_dir)) == 1)

        print("\n[2] 文件 120 秒（测试中 10 秒）窗口内自动挂载")
        f1 = os.path.join(downloads, "报告.docx")
        with open(f1 + ".crdownload", "wb") as f:
            f.write(b"x" * 4096)
        time.sleep(1.2)
        with open(f1 + ".crdownload", "ab") as f:
            f.write(b"y" * 4096)
        os.replace(f1 + ".crdownload", f1)
        items = wait_for(lambda: (lambda it: it if it and it[-1]["attachments"] else None)(load_items(data_dir)))
        check("下载完成的文件挂到最近一条原文下", items and [os.path.basename(a["path"]) for a in items[-1]["attachments"]] == ["报告.docx"],
              items and items[-1]["attachments"])

        print("\n[3] 来源白名单：前台是其他应用时不收录")
        activate("Notes", env)
        xclip_set("记事本里复制的内容，不应被收录", env)
        time.sleep(1.6)
        check("非 Grok 前台窗口的复制被忽略", len(load_items(data_dir)) == 1, len(load_items(data_dir)))

        print("\n[4] 密码管理器标记（x-kde-passwordManagerHint=secret）")
        activate("Grok Bot", env)
        spawn([sys.executable, os.path.join(HERE, "fake_window.py"), "secret-holder", "--secret-text",
               "hunter2-密码", "--seconds", "4"])
        time.sleep(2.0)
        _, tg = sh(["xclip", "-o", "-selection", "clipboard", "-t", "TARGETS"], env)
        check("测试剪贴板确实带有 x-kde-passwordManagerHint", b"x-kde-passwordManagerHint" in tg, tg)
        check("敏感内容被跳过", len(load_items(data_dir)) == 1 and all("hunter2" not in i["rawContent"] for i in load_items(data_dir)))
        time.sleep(2.5)

        print("\n[5] 先下载、后复制：缓冲池反向认领")
        time.sleep(9.0)  # 超出 10 秒窗口
        f2 = os.path.join(grokdir, "图表.png")
        with open(f2, "wb") as f:
            f.write(b"\x89PNG\r\n\x1a\n" + b"0" * 2000)
        time.sleep(2.6)
        n_before = len(load_items(data_dir))
        check("超出窗口的新文件进入缓冲池（未挂到旧条目）",
              all(os.path.basename(a["path"]) != "图表.png" for i in load_items(data_dir) for a in i["attachments"]))
        activate("Grok Bot", env)
        t2 = "## 第二段\n\n中文内容 & <b>标签</b>\u2028行分隔符"
        xclip_set(t2, env)
        items = wait_for(lambda: (lambda it: it if len(it) > n_before else None)(load_items(data_dir)))
        check("第二段被收录，编号顺序 #01 → #02", items and len(items) == 2 and items[1]["rawContent"] == t2)
        check("新原文自动认领缓冲池中的文件", items and [os.path.basename(a["path"]) for a in items[1]["attachments"]] == ["图表.png"],
              items and items[1]["attachments"])

        print("\n[6] 打开收集篮 → 一键生成空间并复制")
        sh(["xdotool", "mousemove", str(1440 - 76 - 24 + 38), str(int(900 * 0.38) + 38), "click", "1"], env)
        time.sleep(1.5)
        wid = activate("GrokClip Space", env)
        check("单击悬浮球打开收集篮窗口", bool(wid))
        sh(["xdotool", "key", "--clearmodifiers", "ctrl+Return"], env)
        spaces = wait_for(lambda: [n for n in os.listdir(grokdir) if n.startswith("GrokSpace_") and n.endswith(".html")])
        check("空间文件已写入导出目录（~/grok bot）", spaces, os.listdir(grokdir))
        time.sleep(0.8)
        _, tg = sh(["xclip", "-o", "-selection", "clipboard", "-t", "TARGETS"], env)
        tgs = tg.decode().split()
        check("剪贴板提供 text/uri-list 与 x-special/gnome-copied-files", "text/uri-list" in tgs and "x-special/gnome-copied-files" in tgs, tgs)
        _, ul = sh(["xclip", "-o", "-selection", "clipboard", "-t", "text/uri-list"], env)
        from pathlib import Path
        expect_uri = Path(os.path.join(grokdir, spaces[0])).as_uri() if spaces else "?"
        check("text/uri-list 指向刚生成的空间文件", ul.decode().strip() == expect_uri, (ul, expect_uri))
        _, gc = sh(["xclip", "-o", "-selection", "clipboard", "-t", "x-special/gnome-copied-files"], env)
        check("gnome-copied-files 为 copy + URI", gc.decode() == "copy\n" + expect_uri, gc)
        time.sleep(1.2)
        check("写入剪贴板不会被自己再次收录", len(load_items(data_dir)) == 2)
        if spaces:
            html = open(os.path.join(grokdir, spaces[0]), encoding="utf-8").read()
            s = html.index('id="grokclip-payload">') + len('id="grokclip-payload">')
            payload = json.loads(html[s:html.index("</script>", s)])
            check("空间内 2 段原文、顺序与内容一致", [c["rawContent"] for c in payload["clips"]] == [t1, t2])
            check("空间内附件已内嵌（docx + png）", [a["name"] for c in payload["clips"] for a in c["attachments"]] == ["报告.docx", "图表.png"]
                  and all(a["embedded"] for c in payload["clips"] for a in c["attachments"]))
            check("空间文件只有 2 个 </script>", html.count("</script>") == 2)

        time.sleep(0.4)
        sh(["import", "-window", "root", SHOT], env, timeout=20)
        check("截图已保存", os.path.exists(SHOT), SHOT)

        print("\n[7] 复制纯原文")
        activate("GrokClip Space", env)
        sh(["xdotool", "key", "--clearmodifiers", "ctrl+shift+c"], env)
        time.sleep(1.0)
        _, raw = sh(["xclip", "-o", "-selection", "clipboard", "-t", "UTF8_STRING"], env)
        check("剪贴板为两段原文用空行拼接（零改写、无水印）", raw.decode("utf-8") == t1 + "\n\n" + t2, repr(raw[:80]))
        time.sleep(1.2)
        check("复制纯原文不会被自己再次收录", len(load_items(data_dir)) == 2)

        print("\n[8] 持久化")
        with open(os.path.join(data_dir, "clips_store.json"), encoding="utf-8") as f:
            raw_json = json.load(f)
        keys = sorted(raw_json[0].keys())
        check("clips_store.json 字段与 macOS 版一致", keys == ["attachments", "contentHash", "id", "isSelected", "rawContent",
                                                     "sourceApp", "timestamp"], keys)
        check("无残留临时文件（原子写入）", not [n for n in os.listdir(data_dir) if n.startswith(".tmp-")])
        check("应用全程未崩溃", app.poll() is None)
    finally:
        for p in reversed(procs):
            try:
                p.terminate()
                p.wait(timeout=3)
            except Exception:
                try:
                    p.kill()
                except Exception:
                    pass
        shutil.rmtree(tmp, ignore_errors=True)

    passed = sum(1 for _, ok in results if ok)
    print("\n结果：%d 通过，%d 失败" % (passed, len(results) - passed))
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
