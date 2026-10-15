"""Reproducible offline walkthrough. All platform content is fictional."""
import argparse
import json
from pathlib import Path
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from social_read.engine import Engine


def demonstrate():
    with tempfile.TemporaryDirectory(prefix="social-read-offline-") as state:
        engine = Engine(state, "demo")
        def task(action, arguments):
            job = engine.wait(engine.submit(action, arguments)["task_id"], 5)
            if job["status"] != "success":
                raise RuntimeError("Offline example failed: " + job["status"])
            return job["result"]
        try:
            search = task("search", {"platforms": ["xiaohongshu", "weibo", "zhihu", "weixin"], "query": "个人知识管理工具", "limit": 1})
            articles = []
            for item in search["items"]:
                content = task("read", {"content_ref": item["content_ref"]})["content"]
                articles.append({k: content[k] for k in ("platform", "title", "body", "body_chars", "source_url", "notice")})
            comments = task("comments", {"content_ref": search["items"][0]["content_ref"], "limit": 1, "replies_limit": 0})
            return {"mode": "demo", "fictional": True, "search_counts": search["coverage"]["returned_by_platform"], "read_count": len(articles), "comment_rows": len(comments["items"]), "articles": articles, "comments": comments["items"], "notice": "完全虚构的离线演示，不连接浏览器，不证明真实平台可读。"}
        finally:
            engine.shutdown()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    text = json.dumps(demonstrate(), ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text, encoding="utf-8")
    else:
        print(text, end="")


if __name__ == "__main__":
    main()
