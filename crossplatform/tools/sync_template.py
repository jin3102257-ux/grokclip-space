#!/usr/bin/env python3
"""从 macOS 版 Sources/SpaceHTMLGenerator.swift 中提取 HTML 模板，写入
crossplatform/grokclip/space_template.html，保证 Swift 与 Python 使用同一份模板（单一事实来源）。

用法：python3 tools/sync_template.py          # 重新生成
      python3 tools/sync_template.py --check  # 只校验是否一致（不一致返回 1）
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SWIFT = os.path.normpath(os.path.join(HERE, "..", "..", "Sources", "SpaceHTMLGenerator.swift"))
OUT = os.path.normpath(os.path.join(HERE, "..", "grokclip", "space_template.html"))

START = 'static let htmlTemplate: String = #"""\n'
END = '\n"""#'


def extract(swift_text):
    swift_text = swift_text.replace("\r\n", "\n")
    s = swift_text.index(START) + len(START)
    e = swift_text.index(END, s)
    body = swift_text[s:e]
    if "\\#" in body:
        raise ValueError("模板中出现 Swift 原始字符串转义 \\#，无法逐字提取")
    return body


def main(argv):
    with open(SWIFT, "r", encoding="utf-8") as f:
        template = extract(f.read())
    if "--check" in argv:
        with open(OUT, "r", encoding="utf-8", newline="") as f:
            same = f.read() == template
        print("模板一致" if same else "模板不一致：请运行 python3 tools/sync_template.py")
        return 0 if same else 1
    with open(OUT, "w", encoding="utf-8", newline="") as f:
        f.write(template)
    print("已写入", os.path.relpath(OUT, os.path.join(HERE, "..")), len(template.encode("utf-8")), "bytes")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
