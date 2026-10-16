"""Bounded, resumable downloads of public dependencies, never platform traffic."""
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import time


class SetupError(RuntimeError):
    def __init__(self, stage, status, log=None):
        super().__init__(status)
        self.stage, self.status, self.log = stage, status, log

    def result(self):
        return {"stage": self.stage, "reason": self.status,
                "log_file": str(self.log) if self.log else None,
                "resume": "rerun_install_preserves_completed_steps"}


def download_env(service, probe, state):
    env = dict(os.environ)
    route = env.get("SOCIAL_READ_DOWNLOAD_ROUTE", "auto")
    if route not in {"auto", "direct", "proxy"}:
        raise SetupError(service, "invalid_download_route")
    has_proxy = any(env.get(k) for k in ("https_proxy", "HTTPS_PROXY", "http_proxy", "HTTP_PROXY", "all_proxy", "ALL_PROXY"))
    if not has_proxy and os.sys.platform == 'darwin':
        try:
            settings = subprocess.run(['/usr/sbin/scutil', '--proxy'], capture_output=True, text=True, timeout=3).stdout
            host = re.search(r'HTTPSProxy\s+:\s+([\w.-]+)', settings)
            port = re.search(r'HTTPSPort\s+:\s+(\d+)', settings)
            if re.search(r'HTTPSEnable\s+:\s+1\b', settings) and host and port:
                env['https_proxy'] = env['HTTPS_PROXY'] = 'http://' + host[1] + ':' + port[1]
                has_proxy = True
        except (OSError, subprocess.TimeoutExpired):
            pass
    selected = "existing"
    curl = shutil.which("curl")
    if route == "direct":
        selected = "direct"
    elif route == "auto" and has_proxy and curl:
        def reachable(direct=False):
            args = [curl, "--head", "--fail", "--silent", "--show-error", "--proto", "=https",
                    "--connect-timeout", "4", "--max-time", "8", "-o", os.devnull]
            if direct: args += ["--noproxy", "*"]
            try:
                return subprocess.run(args + [probe], env=env, capture_output=True, timeout=10).returncode
            except (OSError, subprocess.TimeoutExpired):
                return 28
        failed = reachable()
        if failed in {5, 6, 7, 28, 35, 52, 55, 56} and reachable(True) == 0:
            selected = "direct"
    if selected == "direct":
        # Child process only. Do not alter the user's system/npm configuration.
        for name in ("http_proxy", "https_proxy", "all_proxy", "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY"):
            env.pop(name, None)
        env.update(NO_PROXY="*", no_proxy="*", npm_config_proxy="", npm_config_https_proxy="", npm_config_noproxy="*")
    state.mkdir(parents=True, exist_ok=True, mode=0o700)
    with (state / "download-routes.jsonl").open("a") as out:
        out.write(json.dumps({"service": service, "route": selected, "tls_verified": True}) + "\n")
    return env


def install_command(args, state, stage, timeout, env=None):
    """Keep actionable private logs and terminate children on timeout."""
    logs = state / "setup-logs"; logs.mkdir(parents=True, exist_ok=True, mode=0o700)
    log = logs / (stage + ".log")
    progress = state / 'setup-progress.json'
    progress.write_text(json.dumps({'stage': stage, 'status': 'running'}))
    with log.open("wb") as output:
        os.chmod(log, 0o600)
        proc = subprocess.Popen([str(a) for a in args], stdout=output, stderr=subprocess.STDOUT,
                                env=env, start_new_session=True)
        try:
            code = proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            os.killpg(proc.pid, signal.SIGTERM)
            try: proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                os.killpg(proc.pid, signal.SIGKILL); proc.wait(timeout=3)
            progress.write_text(json.dumps({'stage': stage, 'status': 'failed', 'reason': 'dependency_download_timeout'}))
            raise SetupError(stage, "dependency_download_timeout", log) from None
    if code:
        text = log.read_text(errors="replace")
        reason = "dependency_install_failed"
        if re.search(r"ECONNRESET|ETIMEDOUT|ENOTFOUND|EAI_AGAIN|ConnectionError|connect.*timed out|SSL", text, re.I):
            reason = "dependency_network_unavailable"
        progress.write_text(json.dumps({'stage': stage, 'status': 'failed', 'reason': reason}))
        raise SetupError(stage, reason, log)
    progress.write_text(json.dumps({'stage': stage, 'status': 'ready'}))


def install_opencli(root, state, npm):
    manifest = root / "dependencies/opencli"
    digest = hashlib.sha256((manifest / "package-lock.json").read_bytes()).hexdigest()
    target = state / "opencli"
    target.mkdir(parents=True, exist_ok=True, mode=0o700)
    marker = target / ".social-read-dependencies"
    executable = target / "node_modules/.bin/opencli"
    if marker.exists() and marker.read_text().strip() == digest and executable.is_file():
        return False
    for name in ("package.json", "package-lock.json"):
        shutil.copyfile(manifest / name, target / name)
    env = download_env("opencli", "https://registry.npmjs.org/@jackwener%2fopencli/1.8.6", state)
    install_command([npm, "ci", "--prefix", target, "--cache", state / "download-cache/npm",
                     "--omit=optional", "--ignore-scripts", "--no-audit", "--no-fund",
                     "--fetch-retries=1", "--fetch-timeout=20000",
                     "--fetch-retry-mintimeout=1000", "--fetch-retry-maxtimeout=3000"],
                    state, "opencli_dependencies", 180, env)
    marker.write_text(digest + "\n")
    return True


def opencli_dependencies_ready(root, state):
    marker = state / 'opencli/.social-read-dependencies'
    manifest = root / 'dependencies/opencli/package-lock.json'
    return (marker.is_file() and manifest.is_file() and
            marker.read_text().strip() == hashlib.sha256(manifest.read_bytes()).hexdigest() and
            (state / 'opencli/node_modules/.bin/opencli').is_file())
