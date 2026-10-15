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


## Optional RapidOCR / PaddleOCR models

- Upstream: https://github.com/RapidAI/RapidOCR ; optional package `rapidocr==3.9.2`, code Apache-2.0, copyright RapidOCR Authors. The installed 3.9.2 wheel did not include an independent LICENSE/NOTICE; the full upstream Apache-2.0 text is retained in `licenses/RapidOCR-Apache-2.0.txt`.
- PP-OCRv6 small detection and recognition weights and legacy angle classifier originate from Baidu/PaddleOCR (Baidu and PaddleOCR contributors), separately attributed under Apache-2.0. Full upstream license retained in `licenses/PaddleOCR-Apache-2.0.txt`. Official provenance: https://rapidai.github.io/RapidOCRDocs/main/en/model_licenses/ . Recorded wheel model hashes match the official model statement:
  - `PP-OCRv6_det_small.onnx`: `090f04abcd9d9a7498bc4ebf677e4cb9bdce1fe4197ddb7e529f1ef44e1ff94f`
  - `PP-OCRv6_rec_small.onnx`: `6f327246b50388f3c176ae304bd95767ea6dc0c9ae92153ef8cbe210b3c14884`
  - `ch_ppocr_mobile_v2.0_cls_mobile.onnx`: `e47acedf663230f8863ff1ab0e64dd2d82b838fceb5957146dab185a89d6215c`
- Optional inference dependency `onnxruntime==1.23.2` is MIT (Microsoft); Pillow 12.3.0 uses its HPND/Pillow license. Other transitive dependencies retain their own installed package notices. No third-party wheel or model binary is redistributed in this repository; `--install-ocr` installs them into a separate private environment. Do not remove their installed license notices when redistributing an environment.
- Our `rapidocr_worker.py`, geometry-based overlap ownership and backend integration are independently written MIT project code invoking the public API. No RapidOCR or PaddleOCR implementation source was copied.
- Test images rendered with locally installed fonts remain private. The repository includes fixture-generation source and metric summaries, not system font files or raw social-media images. Platform screenshots are not relicensed by the OCR dependencies.


## Downloaded standalone runtimes (optional installation path)

`install.sh` is original project code. It downloads fixed Python3.11.17 distributions built by [Astral python-build-standalone](https://github.com/astral-sh/python-build-standalone/releases/tag/20261013) and official [Node.js22.23.3](https://nodejs.org/dist/v22.23.3/) binaries, using committed upstream SHA256 values in runtime-lock.tsv. No runtime binary is committed or bundled in the source release. The complete unpacked distributions, including their license/third-party files, remain in private .state/runtime; do not remove those notices when redistributing an installed environment.

Python-build-standalone's build-system source is MPL-2.0; none of that implementation source is copied into this project. Its downloaded Python distribution contains CPython and other libraries under their respective bundled licenses and is not relicensed MIT. Node's distribution retains its own LICENSE, including third-party components; npm and its dependencies retain their notices. The project MIT license applies to our installer/runtime-routing source, not these independent distributions.
