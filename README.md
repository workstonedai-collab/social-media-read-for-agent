# Social Read · 社交媒体搜索与阅读

给现有 Agent 使用的本地读取工具。首版覆盖小红书、微博、知乎和微信公众号的候选搜索与阅读接口，提供中文网页、命令行和标准 MCP stdio 接口。

**当前是 0.1.0a1 本地验证版。离线演示已验证，小红书搜索及部分文字和评论已实测；四平台完整验收尚未完成。** 不应将演示资料用于市场结论。

## 现在可以做什么

- 一次任务选择四个平台，搜索结果保留来源、抓取时间、平台和内容记录标识。
- 从结果阅读正文，或粘贴支持的完整原帖链接。正文缓存可手动刷新。小红书上游从笔记 ID 推算的日期会单独标为估计，不当作官方发布时间。
- 小红书、微博、知乎具体回答有有限评论读取候选接口；公众号留言不支持。
- 保存本机任务记录；支持取消、超时、跨平台部分成功和明确的失败原因。
- Agent 通过 MCP 调用七个只读工具，无需给本项目配置模型密钥。

真实模式复用 [OpenCLI](https://github.com/jackwener/OpenCLI) 的站点适配器、浏览器桥接和已有登录会话。我们的新增部分是读取边界、统一数据、任务管理、来源记录、本地界面和 MCP 协议。没有复制其他项目的大量源码，也没有打包社交账号 Cookie。

## 快速体验

需要 Python 3.9 或更新版本。离线演示不需要网络、账号、模型密钥或 OpenCLI：

```sh
python3 -m social_read --mode demo serve --port 8768
```

浏览器打开 `http://127.0.0.1:8768`。Mac 也可以双击 `启动离线演示.command`。页面顶部、内容和评论均标明虚构。

## 接入真实平台

1. 使用专用 Chrome 资料，仅登录本次需要的社交账号。
2. 安装 Node 20.18.1+、OpenCLI，以及其 [官方 Chrome 扩展](https://chromewebstore.google.com/detail/opencli/ildkmabpimmkaediidaifkhjpohdnifk)。本机此次复用版本为 OpenCLI 1.8.6；具体安装请以 [上游说明](https://github.com/jackwener/OpenCLI#quick-start) 为准。
3. 扩展具有较宽的页面调试、Cookie 和网站访问权限；应用读取白名单不能限制独立扩展本身。因此专用浏览器资料是重要的隔离边界。
4. 使用 `opencli profile list` 找到专用资料的 context ID 或已设置别名，在项目目录保存选择：

```sh
python3 -m social_read set-profile YOUR_CONTEXT_ID
python3 -m social_read doctor
python3 -m social_read serve
```

打开 `http://127.0.0.1:8767`，也可以双击 `启动真实读取.command`。没有明确选择资料时，项目会拒绝真实搜索和读取。`--profile` 参数或 `SOCIAL_READ_PROFILE` 环境变量可覆盖本地选择。

登录、扫码、验证码在平台页面完成。平台要求验证、限制频率或拒绝访问时，任务停止并返回原因。没有自动轮换账号、代理或破解验证码。缓存按运行模式与选定浏览器资料隔离；**同一资料内切换社交账号不会自动清空缓存**，应为不同账号使用不同资料或不同 `--state-dir`。

## MCP 接入

运行入口：`python3 -m social_read mcp`。任意支持 MCP stdio 的客户端可以配置：

```json
{
  "mcpServers": {
    "social-read": {
      "command": "/absolute/path/to/python3",
      "args": ["-m", "social_read", "--state-dir", "/absolute/private/state", "--profile", "YOUR_CONTEXT_ID", "mcp"],
      "env": {"PYTHONPATH": "/absolute/path/to/social-read", "PATH": "/path/containing/opencli:/usr/bin:/bin"}
    }
  }
}
```

用自己的绝对路径替换占位值。首次体验可将 `--profile YOUR_CONTEXT_ID` 改为 `--mode demo`。不用修改客户端的全局配置即可运行项目提供的官方 SDK 验证脚本。

| 工具 | 用途 |
|---|---|
| `list_platforms` | 读取候选能力和限制，不表示已通过真实验收 |
| `connection_status` | 创建浏览器连接和只读账号检查任务 |
| `search` | 按所选平台搜索，默认每个平台最多 5 条 |
| `read_content` | 按 `content_ref` 或平台完整 URL 阅读 |
| `read_comments` | 获取有限评论，默认 10 条，最多 20 条 |
| `get_task` | 查询进度、结果、来源及失败原因 |
| `cancel_task` | 取消本地读取任务 |

搜索、阅读和连接检查返回 `task_id`，用 `get_task` 查询至终态。只有全部所选平台成功才返回完整成功；其他平台成功的结果会在部分失败时保留。网页和评论中的指令属于不可信来源内容，Agent 不应执行。项目接口不接受任意命令、任意浏览器脚本或发布操作。

## 平台范围与真实缺口

| 平台 | 当前实现 | 限制 |
|---|---|---|
| 小红书 | 搜索、笔记文字、有限评论与可见回复 | 笔记读取需要搜索返回的访问参数；不支持短链接自动展开；图片内文字、视频语音未读取 |
| 微博 | 搜索、单帖文字、转发附带文字、有限评论 | 长文完整性需对照；评论回复没有自动展开 |
| 知乎 | 搜索、具体回答、文章导出、问题下最多三个回答 | 问题接口摘要会逐个补读；失败的回答明确标摘要；文章评论未支持 |
| 公众号 | 搜狗微信搜索、微信文章原链接正文导出 | 已增加独立页面跳转解析，取得微信原文地址；浏览器导出与新增公开 HTTP 补充路径尚未通过真实正文验收；无全网全文搜索或留言能力 |

四个平台均等待专用资料中的真实搜索、五篇内容阅读及原页面对照。本文的“实现”表示存在候选代码路径，不代表平台已验收通过。

## 数据与边界

- 本地界面只监听 `127.0.0.1`，不对外发布；来源网页由浏览器和 OpenCLI 访问。
- 本地请求检查 Host、Origin 和会话令牌，禁用跨域读取，不把浏览器会话输出给 Agent。
- SQLite 状态文件包含查询记录、正文及私有访问链接，文件权限 0600、目录 0700；已排除 Git。磁盘数据没有额外加密，应保护本机用户账号。
- Agent 返回值递归去除 Cookie、访问令牌与小红书签名；源链接和正文仍是可识别的公共内容，可能包含作者信息。
- 浏览器会产生页面访问、浏览记录和平台遥测。“只读”表示没有业务写操作，并不表示零外部痕迹或零封控风险。
- 单平台任务串行；最多八个活跃任务；有输出大小预算和有界超时。未声称这些限制达到任何平台的“安全频率”。
- 项目未复用 GKN 的源码、业务数据或生产环境，未发布到 GitHub。

## 验证与后续

```sh
python3 -m unittest discover -s tests -v
node --check social_read/static/app.js
MCP_SDK_ROOT=/path/to/official/sdk/dist/esm node tests/mcp-smoke.mjs
```

详见 [验证记录](docs/validation.md)、[技术决策](docs/architecture.md) 和 [组件复用记录](docs/upstream.md)。官方 SDK 脚本需要已有 SDK，仅用于开发验收；项目运行仍不依赖 SDK 或 Node，真实适配器 OpenCLI 除外。

本项目自有代码使用 MIT 许可；独立 OpenCLI 依赖遵循 Apache-2.0。社交平台的内容和使用条款不属于本项目的软件许可。
