# Third-party notices

## xiaohongshu-mcp

- Source: https://github.com/xpzouying/xiaohongshu-mcp
- Revision: `7797fd375aba02dcd33afe143f6bee13e5870859`
- File studied/adapted: `xiaohongshu/feed_detail.go`, especially `commentLoader`, `checkNoCommentsArea`, `checkEndContainer`.
- License: Apache License 2.0; complete text in `licenses/xiaohongshu-mcp-Apache-2.0.txt`.
- Attribution: xpzouying and the xiaohongshu-mcp contributors.
- Changes: adapted the no-comment/end-container selectors and checks, bounded progress/stagnation tracking and cancellation design into our Python-owned, fixed browser scripts in `social_read/comments.py`. The upstream Go runtime, login management and write operations are not bundled.

OpenCLI remains an external Apache-2.0 runtime dependency invoked through its CLI. See `docs/upstream.md`.

Crawlee and Scrapy document the persisted queue/handled-request patterns used as design references for independently implemented SQLite cursors and per-image checkpoints. Their code is not copied or bundled.

MediaCrawler has a non-commercial learning license and is a design reference only; no code, signing logic or runtime from it is incorporated.

The wechat-article-to-markdown README declares MIT, but this revision does not contain a standalone LICENSE file. We did not copy its source. The article parser and browser extraction are independently implemented using the platform DOM and HTML conventions.
