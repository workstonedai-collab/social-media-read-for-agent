# 组件复用记录

首版明确复用 [OpenCLI](https://github.com/jackwener/OpenCLI)，本机已安装版本 1.8.6，软件许可 Apache-2.0。通过其命令接口调用站点适配器和浏览器桥接，不复制适配器源码进本仓库，也不修改全局安装。官方 Chrome 扩展来源为项目 README 链接的应用商店页面，当前公布版本 1.0.24。

已核查本机包的 xiaohongshu search/note/comments、weibo search/post/comments、zhihu search/question/answer-detail/answer-comments/download 和 weixin search/download 的参数与结果结构。特别发现知乎 question 截断至 200 字，因而本项目额外逐个调用 answer-detail 补读正文。公众号 search 只提供搜狗索引链接，download 面向微信原文链接；本项目增加了独立页面的跳转解析，禁止模型提供任意浏览器命令；真实正文导出仍在验证。

[Agent Reach](https://github.com/Panniantong/Agent-Reach) 用于调研中的能力路由与本机诊断，不作为项目运行时依赖。

执行计划中调研过的 [xiaohongshu-mcp](https://github.com/xpzouying/xiaohongshu-mcp)、[MediaCrawler](https://github.com/NanmiCoder/MediaCrawler) 等项目保留为候选与架构参考，本版没有引入其代码或服务。将来加入时必须重新核对具体版本许可、维护情况、访问逻辑和真实验证，不能因为开源就假定可以任意复制或商用。

独立客户端验收使用本机已有的官方 [MCP TypeScript SDK](https://github.com/modelcontextprotocol/typescript-sdk)，仅作为开发验证工具，不把 SDK 打包进 Python 项目。

公众号补充路径从搜狗同一公开索引响应读取文章链接与正常点击处理的偏移参数，使用 Cookie-free HTTP，不执行站点 JavaScript。原文仍经专用浏览器解析；当前微信返回“参数错误”，没有通过正文验收。公开索引成功与微信原文成功分别记录。
