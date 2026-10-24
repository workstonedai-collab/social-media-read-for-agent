import json
import os
import sqlite3
import threading
import uuid
import time
from pathlib import Path

from .model import ReadError, digest, now, public_url, scrub


class Store:
    def __init__(self, root):
        self.root = Path(root).expanduser().resolve()
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(str(self.root), 0o700)
        self.path = self.root / "research.sqlite3"
        self.lock = threading.RLock()
        self.db = sqlite3.connect(str(self.path), check_same_thread=False, timeout=10)
        os.chmod(str(self.path), 0o600)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=DELETE")
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS items (
                ref TEXT PRIMARY KEY, platform TEXT NOT NULL, target TEXT NOT NULL,
                data TEXT NOT NULL, raw TEXT, read_at TEXT
            );
            CREATE TABLE IF NOT EXISTS comment_cursors (
                id TEXT PRIMARY KEY, source TEXT NOT NULL, expires REAL NOT NULL, data TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS platform_activity (
                platform TEXT NOT NULL, action TEXT NOT NULL,
                latest TEXT NOT NULL, last_success TEXT, last_failure TEXT,
                PRIMARY KEY (platform, action)
            );
            CREATE TABLE IF NOT EXISTS platform_cooldowns (
                platform TEXT PRIMARY KEY, until REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS jobs (
                id TEXT PRIMARY KEY, action TEXT NOT NULL, status TEXT NOT NULL,
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                request TEXT NOT NULL, result TEXT NOT NULL, owner_pid INTEGER
            );
        """)
        self.db.commit()
        self.recover()

    def recover(self):
        with self.lock:
            for r in self.db.execute("SELECT id, owner_pid, result FROM jobs WHERE status IN ('running','queued')").fetchall():
                try:
                    os.kill(r["owner_pid"], 0)
                except ProcessLookupError:
                    result = json.loads(r["result"])
                    result["message"] = "上次运行中断，已保留部分内容；可用原内容记录继续读取。"
                    self.update_job(r["id"], "interrupted", result)
                except (PermissionError, TypeError):
                    pass

    def close(self):
        with self.lock:
            self.db.close()

    def save_item(self, site, item, raw=None, read=False):
        private = dict(item)
        target = private.pop("_target")
        private.pop("_raw", None)
        ref = "content_" + digest(site + "|" + public_url(target))[:24]
        clean = scrub(private)
        clean["content_ref"] = ref
        clean["mode"] = "demo" if clean.get("backend") == "fictional-demo" else "live"
        raw_json = json.dumps(raw, ensure_ascii=False) if raw is not None else None
        with self.lock:
            existing = self.db.execute("SELECT data,read_at FROM items WHERE ref=?", (ref,)).fetchone()
            # A later search must not overwrite a previously fetched body/cache.
            if existing and existing["read_at"] and not read:
                saved = json.loads(existing["data"])
                self.db.execute("UPDATE items SET target=? WHERE ref=?", (target, ref))
            else:
                saved = clean
                self.db.execute("INSERT OR REPLACE INTO items VALUES (?,?,?,?,?,?)", (ref, site, target, json.dumps(clean, ensure_ascii=False), raw_json, now() if read else None))
            self.db.commit()
        return clean  # Search returns its own result metadata, not an old body.

    def bind_read(self, source_ref, content):
        """Keep the original search handle readable after an article redirects.

        Retain its private search target/query for an explicit refresh. The public
        content_ref still identifies the resolved article; no title-only matching.
        """
        if not source_ref or source_ref == content.get("content_ref"):
            return
        with self.lock:
            source = self.db.execute("SELECT platform,data FROM items WHERE ref=?",(source_ref,)).fetchone()
            resolved = self.db.execute("SELECT platform,read_at FROM items WHERE ref=?",(content.get("content_ref"),)).fetchone()
            if not source or not resolved or source["platform"] != resolved["platform"] or not resolved["read_at"]:
                return
            data = dict(content)
            context = json.loads(source["data"])
            if context.get("query"):data["query"] = context["query"]
            data["requested_content_ref"] = source_ref
            self.db.execute("UPDATE items SET data=?,read_at=? WHERE ref=?",(json.dumps(data,ensure_ascii=False),resolved["read_at"],source_ref))
            self.db.commit()

    def item(self, ref):
        with self.lock:
            r = self.db.execute("SELECT * FROM items WHERE ref=?", (ref,)).fetchone()
        if not r:
            raise ReadError("invalid_input", "未找到此内容记录，请重新搜索或提供原帖链接。")
        # Upgrade pre-a5 source records only on an exact recorded source URL match.
        # Never infer that equal titles refer to the same article.
        if r["platform"] == "weixin" and not r["read_at"]:
            with self.lock:
                records = self.db.execute("SELECT data FROM items WHERE platform='weixin' AND read_at IS NOT NULL ORDER BY read_at DESC LIMIT 500").fetchall()
                for old in records:
                    article = json.loads(old["data"])
                    if public_url(article.get("search_source_url")) == public_url(r["target"]):
                        self.bind_read(ref,article)
                        r = self.db.execute("SELECT * FROM items WHERE ref=?",(ref,)).fetchone()
                        break
        return {"platform": r["platform"], "target": r["target"], "data": json.loads(r["data"]), "read_at": r["read_at"]}

    def save_comment_cursor(self, site, target, data):
        token = "comments_" + uuid.uuid4().hex
        source = digest(site + "|" + public_url(target))
        encoded = json.dumps(data, ensure_ascii=False)
        if len(encoded) > 500000:
            return None
        with self.lock:
            self.db.execute("DELETE FROM comment_cursors WHERE expires < ?", (time.time(),))
            self.db.execute("INSERT INTO comment_cursors VALUES (?,?,?,?)", (token, source, time.time()+1800, encoded))
            self.db.commit()
        return token

    def comment_cursor(self, token, site, target):
        if not isinstance(token, str) or len(token) != 41 or not token.startswith("comments_"):
            raise ReadError("invalid_input", "评论续读标识无效，请使用上次返回的 next_cursor。")
        with self.lock:
            row = self.db.execute("SELECT * FROM comment_cursors WHERE id=?", (token,)).fetchone()
        if not row or row["source"] != digest(site + "|" + public_url(target)) or row["expires"] < time.time():
            raise ReadError("cursor_expired", "评论续读标识已过期或不属于这篇内容，请重新读取第一页。")
        return json.loads(row["data"])

    def create_job(self, action, request):
        job_id = "task_" + uuid.uuid4().hex
        stamp = now()
        with self.lock:
            self.db.execute("INSERT INTO jobs VALUES (?,?,?,?,?,?,?,?)", (job_id, action, "queued", stamp, stamp, json.dumps(scrub(request), ensure_ascii=False), "{}", os.getpid()))
            self.db.commit()
        return job_id

    def update_job(self, job_id, status, result):
        with self.lock:
            self.db.execute("UPDATE jobs SET status=?,updated_at=?,result=? WHERE id=?", (status, now(), json.dumps(scrub(result), ensure_ascii=False), job_id))
            self.db.commit()

    def job(self, job_id):
        with self.lock:
            r = self.db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        if not r:
            raise ReadError("invalid_input", "没有找到此任务。")
        result = json.loads(r["result"])
        content = result.get("content")
        if isinstance(content, dict) and isinstance(content.get("body"), str):
            content["body_chars"] = len(content["body"])
            content["media_count"] = len(content.get("media", []))
        return {"task_id": r["id"], "action": r["action"], "status": r["status"], "created_at": r["created_at"], "updated_at": r["updated_at"], "request": json.loads(r["request"]), "result": result}

    def history(self):
        with self.lock:
            ids = [r[0] for r in self.db.execute("SELECT id FROM jobs ORDER BY created_at DESC LIMIT 30").fetchall()]
        return [self.job(i) for i in ids]

    def record_activity(self, site, action, observation, rate_limited=False):
        # Only explicitly constructed metadata reaches this table: no queries,
        # URLs, account identities, result bodies or upstream exception messages.
        data = json.dumps(observation, ensure_ascii=False)
        success = data if observation["status"] == "success" else None
        failure = data if observation["status"] != "success" else None
        with self.lock:
            self.db.execute("""INSERT INTO platform_activity VALUES (?,?,?,?,?)
                ON CONFLICT(platform,action) DO UPDATE SET
                latest=excluded.latest,
                last_success=COALESCE(excluded.last_success,platform_activity.last_success),
                last_failure=COALESCE(excluded.last_failure,platform_activity.last_failure)
                """, (site, action, data, success, failure))
            if rate_limited:
                from .health import RATE_LIMIT_SECONDS
                self.db.execute("""INSERT INTO platform_cooldowns VALUES (?,?)
                    ON CONFLICT(platform) DO UPDATE SET until=MAX(until,excluded.until)
                    """, (site, time.time() + RATE_LIMIT_SECONDS))
            self.db.commit()

    def platform_health(self, site):
        with self.lock:
            rows = self.db.execute("SELECT * FROM platform_activity WHERE platform=?", (site,)).fetchall()
            pause = self.db.execute("SELECT until FROM platform_cooldowns WHERE platform=?", (site,)).fetchone()
        return {"actions": {row["action"]: {key: json.loads(row[key]) if row[key] else None
                for key in ("latest", "last_success", "last_failure")} for row in rows},
                "cooldown": {"until": pause["until"]} if pause else None}
