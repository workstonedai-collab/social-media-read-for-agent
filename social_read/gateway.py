"""Stable stdio connection, fresh worker after idle source updates.

No task is replayed after an uncertain transport failure. Runtime-only changes
reload at a request boundary after all jobs finish; MCP schema changes need a
client refresh. Never terminate a busy worker to install an update.
"""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys


def revision(root):
    h = hashlib.sha256()
    for path in sorted((root / "social_read").rglob("*")):
        if path.suffix in {".py", ".swift"}:
            h.update(str(path.relative_to(root)).encode()); h.update(path.read_bytes())
    return h.hexdigest()[:16]


class Gateway:
    def __init__(self, root, args):
        self.root, self.args = Path(root), args
        self.worker = None
        self.loaded_revision = None
        self.initialize = None
        self.ready = False
        self.tasks = set()
        self.reloads = 0
        self.error = None

    def exchange(self, worker, message):
        worker.stdin.write(json.dumps(message, ensure_ascii=False)+"\n"); worker.stdin.flush()
        if "id" not in message:
            return None
        line = worker.stdout.readline()
        if not line:
            raise OSError("worker disconnected")
        return json.loads(line)

    def start(self, rev):
        env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
        worker = subprocess.Popen([sys.executable, "-B", str(self.root/"start_mcp.py"), "--worker"]+self.args, stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True, encoding="utf-8", env=env)
        try:
            if self.initialize:
                result = self.exchange(worker, self.initialize)
                if "error" in result:
                    raise OSError("initialize failed")
                if self.ready:
                    self.exchange(worker,{"jsonrpc":"2.0","method":"notifications/initialized"})
        except Exception:
            worker.terminate(); worker.wait(timeout=5); raise
        old = self.worker
        self.worker, self.loaded_revision = worker, rev
        if old:
            old.stdin.close(); old.wait(timeout=5)
            self.reloads += 1

    @staticmethod
    def value(response):
        return json.loads(response["result"]["content"][0]["text"])

    def handle(self, message):
        rev = revision(self.root)
        if self.worker is None:
            self.start(rev)
        if self.worker.poll() is not None:
            self.start(rev)
        if rev != self.loaded_revision and self.ready:
            for task in list(self.tasks):
                answer = self.value(self.exchange(self.worker,{"jsonrpc":"2.0","id":"gateway-check","method":"tools/call","params":{"name":"get_task","arguments":{"task_id":task}}}))
                if answer.get("status") in {"success","partial","failed","interrupted","cancelled"}:
                    self.tasks.discard(task)
            if not self.tasks:
                try:
                    self.start(rev); self.error = None
                except Exception:
                    self.error = "update_failed_using_previous_worker"
        if message.get("method") == "initialize":
            self.initialize = message
        if message.get("method") == "notifications/initialized":
            self.ready = True
        response = self.exchange(self.worker, message)
        if message.get("method") == "tools/call" and response:
            try:
                value = self.value(response)
                if value.get("task_id") and value.get("status") == "queued":
                    self.tasks.add(value["task_id"])
                if value.get("task_id") and value.get("status") in {"success","partial","failed","interrupted","cancelled"}:
                    self.tasks.discard(value["task_id"])
                if message.get("params",{}).get("name") == "list_platforms":
                    value["runtime"] = {"connection":"stable-stdio-gateway", "loaded_revision":self.loaded_revision,"disk_revision":rev,"update_pending":rev != self.loaded_revision,"reloads":self.reloads,"update_error":self.error}
                    response["result"]["content"][0]["text"] = json.dumps(value,ensure_ascii=False)
            except (KeyError,ValueError,TypeError):
                pass
        return response

    def close(self):
        if self.worker:
            self.worker.stdin.close()
            try:
                self.worker.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.worker.terminate(); self.worker.wait(timeout=5)


def run(root, args):
    os.umask(0o077)
    gateway = Gateway(root,args)
    try:
        for line in sys.stdin:
            message = None
            try:
                if len(line) > 128*1024:
                    raise ValueError()
                message = json.loads(line)
                if not isinstance(message,dict):
                    raise ValueError()
                result = gateway.handle(message)
            except ValueError:
                result = {"jsonrpc":"2.0","id":None,"error":{"code":-32700,"message":"Parse error"}}
            except Exception:
                result = {"jsonrpc":"2.0","id":message.get("id") if message else None,"error":{"code":-32603,"message":"Worker unavailable; request outcome unknown. Poll existing task before resubmitting."}}
            if result is not None:
                print(json.dumps(result,ensure_ascii=False),flush=True)
    finally:
        gateway.close()
