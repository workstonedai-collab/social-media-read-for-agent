# 把项目交给 Agent

向你常用的本地 Agent 提出：

> 安装并配置 social-media-read-for-agent，按仓库 AGENTS.md 完成依赖、专用浏览器和 MCP 设置。需要我扫码或在平台输入密码时再引导我；设置完成后，用你当前这段对话实际搜索并阅读，告诉我哪些平台可用。

Agent 自动检查并安装缺失的 OpenCLI、配置自己的工具入口、发现专用浏览器、安装可选本机 OCR，最后实际搜索与阅读。普通用户不用找资料编号、复制 Cookie、改配置或启动后台服务；MCP 客户端按需启动工具。

正常首次体验：Agent 配置环境 → 你确认一次官方 Chrome 扩展权限 → 你在专用浏览器正常登录小红书、微博和知乎 → Agent 搜索和阅读验收。微信公众号走公开索引和文章，不需要公众号后台登录。平台要求验证码或重新扫码时，需要本人完成。

已登录且专用浏览器仍连接时，日常只需提出调研问题。启动器保留连接，空闲时加载读取代码更新；正在执行的任务继续完成。初次添加 MCP、旧版连接迁移、工具接口或稳定入口本身变化时，客户端可能需要一次刷新；不会重新配置或索要账号密码。

## Agent 使用的入口

```sh
python3 bootstrap.py
python3 bootstrap.py --apply --clients codex hermes
python3 bootstrap.py --apply --clients codex hermes --install-ocr
python3 bootstrap.py --open-browser
python3 bootstrap.py --open-login xiaohongshu
python3 bootstrap.py --verify
```

只选择实际使用的客户端。`--open-browser` 在 macOS 自动打开项目独立 Chrome 用户目录；已有保存的专用资料时会拒绝多建窗口，请用 `--open-login`。首次扩展安装后重跑 `--apply` 自动识别新增连接；多个新增连接须由用户或 Agent 核对专用窗口，再使用 `--profile` 选择。不会默认采用唯一但未经确认的日常资料。

`--apply` 只修改所选客户端本项目的配置；每次变化前备份在 `.state/setup-backups/`，文件权限 0600。再次运行无变化时不写文件。Hermes 使用其自带 YAML 解析器合并配置，保留其他设置值。`--verify` 的 JSON 区分依赖配置完成与实际读取通过；只有四个平台各有真实正文才验收通过。

当前实现及登录沿用已在这台 macOS 机器验证。新机器的 Chrome 首装、Linux/Windows 浏览器自动启动、断电恢复和七日稳定性尚未验收。安装器遇到缺依赖会返回原因供 Agent 处理，不应宣称完全无人值守。Node 与 Python 的系统安装需要由 Agent 依照本机包管理器及宿主权限完成。
