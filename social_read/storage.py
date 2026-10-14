import json
import os
import sqlite3
import threading
import uuid
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
            for r in self.db.execute("SELECT id, owner_pid FROM jobs WHERE status IN ('running','queued')").fetchall():
                try:
                    os.kill(r["owner_pid"], 0)
                except ProcessLookupError:
                    self.update_job(r["id"], "interrupted", {"message": "上次运行中断。已保留记录；请重新提交读取任务。"})
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

    def item(self, ref):
        with self.lock:
            r = self.db.execute("SELECT * FROM items WHERE ref=?", (ref,)).fetchone()
        if not r:
            raise ReadError("invalid_input", "未找到此内容记录，请重新搜索或提供原帖链接。")
        return {"platform": r["platform"], "target": r["target"], "data": json.loads(r["data"]), "read_at": r["read_at"]}

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
