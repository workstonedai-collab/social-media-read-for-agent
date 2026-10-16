# 演示图片 / Demo screenshots

`offline-demo.jpg` 是本项目网页在独立 demo 状态目录中实际执行四平台搜索并打开一条内容后的完整截图；`offline-preview.jpg` 是同一页面上半部分的直接截图。全部资料来自项目自带的虚构 DemoAdapter，无真实账号、社交平台全文、访问令牌或浏览器地址栏。

复现：运行 `python3 -m social_read --mode demo serve`，打开返回的本机地址，搜索“个人知识管理工具”，点击小红书结果的“阅读”。无需社交账号；浏览器显示的日期来自实际演示时间。命令行可重现结果为 `python3 scripts/demo_research.py`。

The images show the project's actual local dashboard with fictional demo data. They are not live-platform acceptance evidence. Run the local demo server, search the sample topic, then open a result to reproduce the view. The CLI example is reproducible without a browser or network.


## a11 real-task receipt replay

`live-demo.mp4` (79 seconds), `live-demo-poster.png` and `live-demo.srt` are generated from the current Codex's actual a11 tool receipts on 2026-10-16. They contain sanitized counts and coverage, with compressed waiting. They are not a platform screen recording or proof of a first-account sign-in. No original social posts, account identities, signed source URLs or platform screenshots are included. See `../a11-validation.json` for scope and retained failures.
