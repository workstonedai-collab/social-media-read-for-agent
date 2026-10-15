# 本机 OCR 对比 · 2026-10-15

结论：RapidOCR 的长图默认缩放仍会丢字，分片后改善明显；普通小字没有全面胜出，清晰印刷公式符号和两张真实笔记优于原 Vision 样本。最终采用可选 RapidOCR + 长图分片，macOS Vision 保留为轻量方案。默认 auto 在安装器完成 RapidOCR 安装及模型记录后选 RapidOCR，否则 macOS 用 Vision。原始图片不发送给额外在线 OCR 服务。

## 比较方法

同一台 Apple M4 Mac mini、同一图片字节分别运行 macOS Vision、RapidOCR 3.9.2 原默认缩放、RapidOCR 分片。ONNX Runtime 1.23.2 使用 CPU，2 个运算线程；默认 PP-OCRv6 small 检测/识别与旧角度分类器。默认比较只改线程和日志设置；分片保留横向分辨率，窗口高最多1600像素、160像素重叠，以重叠中点划分文字归属，再按位置排序，避免把不同位置重复文字当重复删除。

六张自制图片有完整已知原文：14/20像素中文小字、160/320行长图（实际高6000/11920像素）、22/38像素印刷公式。两张真实小红书笔记用于人工观察，未建立逐字完整标注，**不计算真实笔记准确率**。制作期间曾发现字体缺字，已更换可显示符号的字体并重跑公式；旧无效图结果不计入最终表。

字符错误率 CER = 最小插入/删除/替换次数 ÷ 参考字符数。只去空白并做 NFC 规范化，保留上标、希腊字母、箭头和运算符；不把 x³ 与 x3 当同字。距离计算与基础动态规划500组短文本对照一致。数字仅代表这组图，不能推导全站 OCR 准确率。长图内容重复、底色清晰，现实的压缩、噪声、手写与复杂分栏尚未系统评测。

## 最终结果

| 图片 | 原 Vision CER | RapidOCR 默认 CER | RapidOCR 分片 CER | Vision / 分片耗时 |
| --- | ---: | ---: | ---: | --- |
| 14px 中文小字 | 0.29% | 0.29% | 0.29% | 0.60s / 0.62s |
| 20px 中文小字 | 0.00% | 0.00% | 0.00% | 0.39s / 0.87s |
| 1000 × 6000 长图 | 0.70% | 28.76% | 0.00% | 2.38s / 12.58s |
| 1000 × 11920 长图 | 100.00% | 98.67% | 0.00% | 0.27s / 21.97s |
| 22px 印刷公式 | 25.37% | 0.00% | 0.00% | 0.52s / 0.36s |
| 38px 印刷公式 | 23.88% | 0.00% | 0.00% | 0.31s / 0.38s |

11920像素长图，Vision 返回 `no_text_detected`，因此相对已知原文 CER 为100%。RapidOCR默认虽然返回success，仍丢掉大部分文字；分片9块后该图 CER 为0%。**success 与 images_all_processed 只表示流程完成，不等于识别准确。**

这里公式是清晰印刷的线性表达式，包括上标、希腊字母、积分与根号字符；不是复杂二维数学版面测试，也没有输出或验证 LaTeX、分式结构、推导含义。不能把此处0%外推到手写、矩阵或论文公式。

耗时是第二轮同图的单次值，包含每张的引擎构造，非统计性能基准；RapidOCR的首张还曾达到45.56秒，Vision首次8.58秒，后续通常显著降低。首次依赖加载、系统缓存和并行任务会影响时间，不能承诺首次像暖机一样快。产品限制每张 RapidOCR45秒、整批150秒，超时保留缺失位置并允许补读；模型第一次加载超过预算时也会明确失败，不无限等待。

## 真实笔记人工对照

- 知识库教程第四张：原 Vision 将书名来源前的中文识别为乱码；RapidOCR读出“出自《Building a Second Brain》”。两者均能读到主标题和大部分要点，条目顺序并非全部一致。
- 错题本截图：原 Vision 把极限箭头识别为乘号和短横，把上标读成普通数字；RapidOCR保留 `lim x→0 (sin x - x) / x³`。RapidOCR仍误读部分很小的界面文字及图标，不能视为整张图已核准。
- 最终真实九图笔记采用 RapidOCR，9/9有结果、0未读；该次复验复用了已完成检查点，不冒充重新识别9张。见 [回执](a5-live-rapid-read.json)。

机器结果含图片 SHA-256、尺寸类别、后端、耗时和 CER，见 [24次测量](ocr-a5-metrics.json)。本地原图、逐行输出及参考原文放在忽略的 `.state/ocr-benchmark/`，不随仓库分发。

## 复跑

```sh
python3 bootstrap.py --apply --install-ocr --clients codex
.state/rapidocr-env/bin/python scripts/create_ocr_fixtures.py --font "/System/Library/Fonts/STHeiti Medium.ttc" --math-font "/System/Library/Fonts/Supplemental/Arial Unicode.ttf"
.state/rapidocr-env/bin/python scripts/benchmark_ocr.py
```

字体只从本机读取，不分发字体文件。其他系统指定已安装、获准使用且覆盖中文及数学符号的字体。真实图可由 Agent 在私有manifest中增加本地路径，无逐字真值时truth设null，避免伪造准确率。对比Vision需macOS及Swift；自动浏览器设置的跨系统流程尚未验收。

代码、模型及其许可来源分别列于 [第三方声明](../THIRD_PARTY_NOTICES.md)；本次安装模型指纹与 [RapidOCR 官方说明](https://rapidai.github.io/RapidOCRDocs/main/en/model_licenses/) 一致。未复制上游算法源码或打包模型。
