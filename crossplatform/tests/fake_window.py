"""测试辅助：模拟一个前台窗口（标题可指定），可选地以多种格式持有剪贴板。
用法：python3 fake_window.py "<窗口标题>" [--secret-text TEXT] [--seconds N]
"""
import sys
import tkinter as tk

title = sys.argv[1]
secret = None
seconds = 600
if "--secret-text" in sys.argv:
    secret = sys.argv[sys.argv.index("--secret-text") + 1]
if "--seconds" in sys.argv:
    seconds = int(sys.argv[sys.argv.index("--seconds") + 1])

root = tk.Tk()
root.title(title)
root.geometry("520x300+60+80")
tk.Label(root, text=title, font=("WenQuanYi Micro Hei", 16)).pack(expand=True)
if secret is not None:
    root.withdraw()
    root.clipboard_clear()
    root.clipboard_append(secret)
    root.clipboard_append("secret", type="x-kde-passwordManagerHint")
root.after(seconds * 1000, root.destroy)
root.mainloop()
