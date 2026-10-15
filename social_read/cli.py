import argparse
import json
import os
import sys
from pathlib import Path

from . import __version__
from .engine import Engine
from .mcp import serve_stdio
from .model import ReadError
from .web import serve


def default_state():
    return Path(__file__).resolve().parent.parent / ".state"


def main():
    os.umask(0o077)
    parser = argparse.ArgumentParser(description="social-media-read-for-agent · 四平台只读搜索与阅读")
    parser.add_argument("--version", action="version", version=__version__)
    parser.add_argument("--state-dir", default=str(default_state()))
    parser.add_argument("--mode", choices=["live", "demo"], default="live")
    parser.add_argument("--profile", default=os.environ.get("SOCIAL_READ_PROFILE"), help="OpenCLI 已配置的浏览器资料别名")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("platforms")
    sub.add_parser("doctor")
    sub.add_parser("history")
    sub.add_parser("mcp")
    setup = sub.add_parser("set-profile")
    setup.add_argument("name", help="OpenCLI 连接资料的 context ID 或别名")
    web = sub.add_parser("serve")
    web.add_argument("--port", type=int, default=8767)
    search = sub.add_parser("search")
    search.add_argument("query")
    search.add_argument("--platforms", nargs="+", default=["xiaohongshu", "weibo", "zhihu", "weixin"])
    search.add_argument("--limit", type=int, default=5)
    for name in ("read", "comments"):
        p = sub.add_parser(name)
        p.add_argument("--platform")
        target = p.add_mutually_exclusive_group(required=True)
        target.add_argument("--url")
        target.add_argument("--content-ref")
        if name == "read":
            p.add_argument("--refresh", action="store_true")
        else:
            p.add_argument("--cursor")
            p.add_argument("--limit", type=int, default=10)
            p.add_argument("--replies-limit", type=int, default=3)
    args = parser.parse_args()
    engine = None
    exit_code = 0
    try:
        config = Path(args.state_dir) / "browser-profile.txt"
        if args.command == "set-profile":
            from .adapter import Runner
            Runner(Path(args.state_dir), profile=args.name)
            config.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            config.write_text(args.name + "\n", encoding="utf-8")
            config.chmod(0o600)
            print("专用浏览器资料已保存。下一步检查连接。")
            return 0
        selected = args.profile or (config.read_text(encoding="utf-8").strip() if args.mode == "live" and config.exists() else None)
        engine = Engine(args.state_dir, args.mode, selected)
        if args.command == "mcp":
            serve_stdio(engine)
        elif args.command == "serve":
            if not 1024 <= args.port <= 65535:
                raise ReadError("invalid_input", "端口须为 1024 至 65535。")
            serve(engine, args.port)
        elif args.command == "platforms":
            print(json.dumps(engine.capabilities(), ensure_ascii=False, indent=2))
        elif args.command == "history":
            print(json.dumps(engine.store.history(), ensure_ascii=False, indent=2))
        else:
            if args.command == "search":
                action, req = "search", {"platforms": args.platforms, "query": args.query, "limit": args.limit}
            elif args.command == "doctor":
                action, req = "connection", {}
            else:
                action, req = args.command, {k: v for k, v in {"platform": args.platform, "url": args.url, "content_ref": args.content_ref}.items() if v is not None}
                req.update({"refresh": args.refresh} if action == "read" else {"limit": args.limit, "replies_limit": args.replies_limit, **({"cursor": args.cursor} if args.cursor else {})})
            submitted = engine.submit(action, req)
            job = engine.wait(submitted["task_id"], 300)
            print(json.dumps(job, ensure_ascii=False, indent=2))
            exit_code = 1 if job["status"] in {"failed", "interrupted", "cancelled"} else 0
    except ReadError as exc:
        print(json.dumps(exc.result(), ensure_ascii=False), file=sys.stderr)
        exit_code = 1
    except OSError as exc:
        from .model import classify_error
        print(json.dumps(classify_error(str(exc)).result(), ensure_ascii=False), file=sys.stderr)
        exit_code = 1
    except KeyboardInterrupt:
        pass
    finally:
        if engine:
            engine.shutdown()
    return exit_code
