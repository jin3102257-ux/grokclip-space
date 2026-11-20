# 参与贡献

感谢你愿意改进 GrokClip Space！提交之前请花两分钟读完下面的约定。

## 三条底线

1. **隐私红线**：不加入任何联网代码（统计、埋点、更新检查、CDN 资源都不行）；不在源码、测试、文档、截图里出现真实姓名、邮箱、用户名、个人主目录路径（带有你用户名的路径）、公司名或真实对话内容。示例数据请用虚构内容。
2. **零依赖**：macOS 版只用系统框架；Windows / Linux 版只用 Python 标准库 + Tkinter。可选增强（如 `tkinterdnd2`）必须在缺失时自动降级。
3. **空间格式一致**：HTML 模板的唯一来源是 `Sources/SpaceHTMLGenerator.swift`。修改后运行 `python3 crossplatform/tools/sync_template.py` 同步，三端输出必须保持一致。

## 开发与测试

```bash
cd crossplatform
python3 -m unittest discover -s tests       # 单元测试（任意平台）
python3 tools/sync_template.py --check      # 模板一致性
python3 -m grokclip --self-test /tmp/t.html # 生成示例空间
# Linux 端到端（需要 xvfb xclip xdotool imagemagick openbox python3-tk）
python3 tests/e2e_linux_xvfb.py /tmp/linux_ui.png
```

macOS 版：`bash build.sh` 会编译并运行 `--self-test`。

提交前请自查隐私：

```bash
rg -i "你的用户名|你的真实姓名|你的公司名" .
rg -n "urllib\.request|http\.client|import socket|requests" .
```

## 提交流程

1. 先开 Issue 讨论较大的改动。
2. Fork 后新建分支，保持提交小而清晰，提交信息用中文或英文均可。
3. 用 Pull Request 模板说明改动、测试方式和影响的平台。
4. 代码风格：Python 遵循 PEP 8（行宽 120），注释和界面文案用简体中文。

## 特别欢迎

- 在真实 **Windows 10 / 11** 和 **Wayland** 桌面上的试用报告
- 更多浏览器 / Grok 客户端的前台识别规则
- 无障碍与高分屏适配
