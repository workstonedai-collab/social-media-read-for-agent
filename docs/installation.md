# 安装、登录与日常使用

[English](installation.en.md) · [返回项目介绍](../README.md)

## 给普通用户

把仓库地址交给支持本地执行的 Agent，让它读 AGENTS.md 并完成安装。你不需要先安装 Python、Node、npm 或 Docker，不需要找浏览器资料编号、复制 Cookie 或手改配置。macOS 也可下载源码压缩包、解压后双击“安装.command”，然后回到 Agent 继续。

你参与的步骤是官方 Chrome 扩展的权限确认、社交平台正常登录，以及平台要求的安全验证。密码、Cookie、验证码不要发给 Agent；在平台页面扫码或输入。建议使用无过多个人隐私内容的专用账号。微信公众号文章读取不需要公众号后台账号。

## 给执行安装的 Agent

### 1. 取得项目并安装

有 Git 可克隆仓库；没有 Git 可下载 Releases 中的源码压缩包并解压。先读 AGENTS.md、README 和 THIRD_PARTY_NOTICES，再运行当前客户端对应的命令：

```sh
/bin/bash install.sh --clients codex --install-ocr
# Hermes: --clients hermes
# 用户明确需要两边: --clients codex hermes
# 其他本地客户端: --clients generic
```

不需要先安装系统 Python/Node，也不需要 Homebrew、uv、Docker 或管理员权限。安装器使用系统基础工具 bash、curl、tar、awk 与 SHA256，从固定官方发布下载独立运行时，按 runtime-lock.tsv 校验后执行。运行时、OpenCLI、配置备份和可选 OCR 都在忽略的 .state 中；不会修改 shell 配置、系统运行时和 Agent 的依赖。

固定 Python 3.11.17（Astral python-build-standalone 构建20261013）、Node22.23.3、OpenCLI1.8.6。运行时解压约260 MB；组件及可选 OCR 需要额外空间。首次需要联网，后续可复用运行时。下载校验失败直接停止；已有目录与固定版本冲突时不覆盖，由 Agent 处理。下载、解压中断会清理本次临时文件，已完成组件保留。安装锁阻止同时安装；异常退出留下锁时由 Agent 先核对是否仍有安装进程，不让用户盲删。

独立运行时提供 macOS Apple Silicon/Intel、Linux glibc x64/arm64；Windows和Linux musl不在此入口范围。自动创建专用Chrome主要覆盖macOS，其他Linux桌面需Agent引导连接。Chrome仍需存在；缺少时按宿主权限处理。双击文件可能需要macOS正常确认，不修改安全策略或强制移除隔离标记。

安装器只配置请求的客户端，遵循 CODEX_HOME/HERMES_HOME，先备份再合并，保留其他设置。没有检测到客户端时输出通用配置；generic导出不代表已修改任何客户端。没有本地执行或stdio能力的云聊天不能直接访问本机浏览器。

可选OCR在独立环境中安装并预热，不发送图片到额外在线服务。省略 `--install-ocr` 时保留其他读取功能；macOS已有Swift工具时可用Vision，否则图片明确报告未识别。

### 2. 引导扩展授权和正常登录

已有保存的专用连接直接复用，没有时先解释扩展能访问网站、页面调试和Cookie，然后正常引导用户授权：

```sh
/bin/bash run.sh setup --open-browser
```

该命令在macOS打开项目独立Chrome资料与官方商店页面，不自动安装或授权扩展。不能采用未经确认的日常资料，不注入Cookie，不下载不明扩展。用户安装后重跑安装命令，会自动识别刚新增的专用连接；多个新连接时核对用户专用窗口，不让用户寻找内部ID。

按需打开平台登录页面：

```sh
/bin/bash run.sh setup --open-login xiaohongshu
# 也支持 weibo / zhihu / weixin
```

用户直接在平台页面扫码或输入密码，不把密码或验证码发给对话。平台验证、限流、拒绝访问时停止对应平台，不能轮换账号、代理或高频请求绕过。

### 3. 验收当前客户端

```sh
/bin/bash run.sh setup --verify --platforms xiaohongshu
# 省略 --platforms 时验收四个平台
```

只对用户需要的平台操作，不强求一次登录全部账号。验收会实际搜索并刷新读取正文；accepted=true仅代表requested_platforms本批通过。setup_progress区分依赖、专用浏览器、配置和真实读取，连接或身份检查不等于正文可读。

最后必须用当前Agent的list_platforms、search、get_task、read_content核对实际版本与运行指纹。兼容读取代码在空闲时更新；启动器、工具结构、依赖和客户端首次发现可能需要正常刷新。不得强杀忙碌MCP。暂时无法刷新时由Agent用同版CLI继续，并如实说明旧注册尚未更新，用户不必重复登录。

### 4. 日常使用与更新

```sh
/bin/bash run.sh search "个人知识管理工具" --limit 1
/bin/bash run.sh read --content-ref CONTENT_REF
/bin/bash run.sh comments --content-ref CONTENT_REF --limit 2 --replies-limit 1
```

run.sh会使用项目解释器及Node，即使Agent的启动环境没有这些命令。配置中的解释器与启动器都是绝对路径，和Agent当前工作目录无关。移动项目目录后，由Agent重跑安装更新路径。

更新前核对当前工作区，不覆盖用户改动；正常快进取得新源码后重跑安装。运行时固定版本冲突、安装中断、客户端发现问题由Agent处理，用户只参与真正需要的授权与登录。不要把内部排障动作转交用户。

## 状态与处理

| 状态 | Agent处理 |
| --- | --- |
| 下载失败/校验失败 | 保留已完成组件，核对官方来源与网络后有界重试；不关闭TLS或跳过校验 |
| 已有安装锁 | 先核对本次项目安装进程；仍在运行则等待，不能删除别的进程的锁 |
| dedicated_browser_extension | 引导专用Chrome、解释并确认官方扩展权限 |
| login_required / verification_required | 在专用窗口让用户正常完成，不反复提交 |
| rate_limited / access_denied | 停止对应平台并保留其他结果 |
| navigation_loading_timeout / content_loading_timeout | 页面未在预算内就绪；保留失败，不解释为账号被封或搜索无结果 |
| command_outcome_unknown | 不自动重放操作，先核对任务 |
| comments_loading_incomplete | 评论尚未加载完整；不能把零条当作没有评论 |
| 图片部分失败 | 查看unread_orders，按原内容记录补读；成功图片保留 |
| source_context_changed | 重新搜索取得新上下文，不猜另一篇同名文章 |
| 公众号留言unsupported | 不提交或重试，本版只支持文章正文 |

## 自行验证与反馈

```sh
# 已有Python时运行；此命令只检查隔离安装，不使用账号
python3 scripts/verify_clean_install.py --portable --output .state/portable-install.json
# 记录每天一次的真实样本（仅在用户授权账号后执行）
/bin/bash run.sh stability --run
# 仅读本机已有跨日期摘要，不访问平台
/bin/bash run.sh stability
```

隔离安装会从全新目录安装，子进程PATH不提供Python、Node、npm，保留哨兵配置，并实际调用七工具虚构流程。它不复制账号、不采用日常资料，不能代表新机器已登录。跨日期回执在私有.state/stability，同日重复不增加天数；这条命令自身不创建定时任务，不向GitHub上传记录。

反馈请提供平台、版本、错误状态和脱敏复现步骤，不附Cookie、验证码、私有链接、真实全文或浏览器资料。
