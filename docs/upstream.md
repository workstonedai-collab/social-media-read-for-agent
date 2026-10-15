# 组件复用记录

首版明确复用 [OpenCLI](https://github.com/jackwener/OpenCLI)，本机已安装版本 1.8.6，软件许可 Apache-2.0。通过其命令接口调用站点适配器和浏览器桥接，不复制适配器源码进本仓库，也不修改全局安装。官方 Chrome 扩展来源为项目 README 链接的应用商店页面，当前公布版本 1.0.24。

已核查本机包的 xiaohongshu search/note/comments、weibo search/post/comments、zhihu search/question/answer-detail/answer-comments/download 和 weixin search/download 的参数与结果结构。特别发现知乎 question 截断至 200 字，因而本项目额外逐个调用 answer-detail 补读正文。公众号 search 只提供搜狗索引链接，download 面向微信原文链接；本项目增加了独立页面的跳转解析，禁止模型提供任意浏览器命令；真实正文导出仍在验证。

[Agent Reach](https://github.com/Panniantong/Agent-Reach) 用于调研中的能力路由与本机诊断，不作为项目运行时依赖。

执行计划中调研过的 [xiaohongshu-mcp](https://github.com/xpzouying/xiaohongshu-mcp)、[MediaCrawler](https://github.com/NanmiCoder/MediaCrawler) 等项目保留为候选与架构参考，本版没有引入其代码或服务。将来加入时必须重新核对具体版本许可、维护情况、访问逻辑和真实验证，不能因为开源就假定可以任意复制或商用。

独立客户端验收使用本机已有的官方 [MCP TypeScript SDK](https://github.com/modelcontextprotocol/typescript-sdk)，仅作为开发验证工具，不把 SDK 打包进 Python 项目。

公众号补充路径从搜狗同一公开索引响应读取文章链接与正常点击处理的偏移参数，使用 Cookie-free HTTP，不执行站点 JavaScript。原文仍经专用浏览器解析；当前微信返回“参数错误”，没有通过正文验收。公开索引成功与微信原文成功分别记录。


## 2026 年 10 月 15 日公众号项目比较

| 项目 | 核查实现 | 本轮采用方式 |
|---|---|---|
| [jackwener/wechat-article-to-markdown](https://github.com/jackwener/wechat-article-to-markdown) | Camoufox 等待 #js_content 后解析标题、正文；GitHub 未识别独立许可 | 借鉴正文加载条件；未复制源码或引入独立浏览器 |
| [gxcsoccer/wechat-article-crawler](https://github.com/gxcsoccer/wechat-article-crawler) | MIT；Crawl4AI 按正文选择器等待与结构提取，设置 UA 与 Referer | 借鉴限定文章区域、等正文与媒体提取；未引入 UA 伪装或绕过验证 |
| [luoziyan100/wechat2md](https://github.com/luoziyan100/wechat2md) | MIT；扩展从已打开页面提取正文、图片和原文元数据 | 借鉴在正常访问上下文中读取、图片顺序及来源元数据；固定脚本自行实现 |
| [likemaoke/wechat-article-to-md](https://github.com/likemaoke/wechat-article-to-md) | MIT；requests 与 BeautifulSoup，Chrome UA，按 rich_media_content/js_content 提取 | 比较公开 HTTP 方案；继续使用无运行依赖的本项目解析器 |

查看的是实际 README、核心读取源码与许可信息，没有只以星数或仓库 updatedAt 判断有效性。本轮没有安装这些项目的额外浏览器，也没有复制其大段代码。OpenCLI 1.8.6 仍是唯一真实浏览器运行依赖，扩展仍为 1.0.24。Agent Reach 检查为 1.5.0 最新。

本项目新增公众号同页点击路径：在独立、专用资料中的搜索页面等待结果，唯一匹配目标标题，确认链接属于搜狗 /link，使用正常点击处理打开原文，等待 #js_content 后提取限定文章区域。只关闭自身创建的页面。没有向模型开放任意 eval、任意选择器或浏览器命令。


## 0.1.0a3 图片长文和评论

OCR 使用 macOS 原生 Vision 的 VNRecognizeTextRequest，中文与英文识别在本机执行，无外部 OCR 服务或模型密钥。参考 Apple 官方文档 https://developer.apple.com/documentation/vision/vnrecognizetextrequest 。本机识别程序自行实现，未引入其他 OCR 项目的源码或模型。

核查 OpenCLI 的小红书图片轮播 state、微博长文端点与知乎 legacy 评论逻辑；另核查其上游问题 https://github.com/jackwener/OpenCLI/issues/2303 及 MediaCrawler 的评论接口说明，借鉴根评论与子回复分开的读取方式，自行实现有界同源读取。没有引入其签名算法、额外账号、验证码绕过或大规模抓取。OpenCLI 固定选择器等待在 body 未出现时的 MutationObserver 错误已复现，改用本项目内部有界就绪检查。


## 0.1.0a4 图片恢复、评论续读与公众号正文

实际改编 `xpzouying/xiaohongshu-mcp` 的评论停止判断和有界加载设计，固定版本 `7797fd375aba02dcd33afe143f6bee13e5870859`。Apache-2.0 全文和来源/修改说明见 `../licenses/xiaohongshu-mcp-Apache-2.0.txt` 与 `../THIRD_PARTY_NOTICES.md`；评论模块标注 Apache-2.0。未引入上游 Go 运行时、登录或写操作。

图片恢复参考 Crawlee/Scrapy 的持久化任务与完成标记范式，本项目自行实现私有原子检查点和 SQLite 评论游标，没有新增爬虫框架运行依赖。MediaCrawler 当前使用非商业学习许可，其代码未复制或安装。

重新核对 `jackwener/wechat-article-to-markdown` 版本 `50b7e63c11a880c991815e4bae6a4a9fb161785a`：README 声明 MIT，仓库未提供独立 LICENSE。本轮不复制其代码，公众号解析器继续自行实现。吸收的公开格式需求包括 #js_content、懒加载图片、代码块、发布时间、来源元数据；不用独立 Camoufox 替换已有专用浏览器。

RapidOCR/PaddleOCR 保留为后续识别质量对照候选，尚未安装或引入模型。MCP Proxy 也尚未作为正式依赖；本轮优先交付读取恢复和正文/评论行为。


搜索真实复测后，小红书补充标题链接文字回退，知乎改为本项目固定内部脚本读取正常搜索卡片，以避免上游接口403被误报登录。知乎回答接口拒绝时只读取已打开目标页的可见区域，不重发该接口。以上是本项目自行实现，未复制其他项目源码。
