"""Reconnect only the saved browser identity before dispatching a read.

No credential export, profile switching, policy changes or service shutdown.
The loopback status endpoint is OpenCLI 1.8.6's existing readiness contract.
"""
import http.client
import json
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess
import tempfile
import time
from urllib.parse import urlencode

from .browser_page import _pause
from .model import ReadError

EXTENSION = 'ildkmabpimmkaediidaifkhjpohdnifk'


class BrowserConnection:
    def __init__(self, state, profile, executable, chrome_root=None):
        self.state, self.profile = Path(state), profile
        # Resolve only an explicit alias. Never adopt OpenCLI's default profile.
        try:
            config=Path(os.environ.get('OPENCLI_CONFIG_DIR',str(Path.home()/'.opencli')))/'browser-profiles.json'
            value=json.loads(config.read_text()).get('aliases',{}).get(profile)
            if isinstance(value,str) and re.fullmatch(r'[\w.-]{1,80}',value):self.profile=value
        except (OSError,ValueError,AttributeError):pass
        self.executable = Path(executable).resolve()
        self.chrome_root = Path(chrome_root) if chrome_root else Path.home()/'Library/Application Support/Google/Chrome'

    def status(self):
        conn = http.client.HTTPConnection('127.0.0.1', 19825, timeout=1)
        try:
            conn.request('GET', '/status?'+urlencode({'contextId':self.profile}), headers={'X-OpenCLI':'1'})
            response = conn.getresponse()
            data = response.read(131073)
            if response.status != 200 or len(data)>131072:
                return 'service_incompatible'
            data = json.loads(data)
            if not isinstance(data,dict):return 'service_incompatible'
            expected = json.loads((self.executable.parents[2]/'package.json').read_text())['version']
            if data.get('ok') is not True or data.get('daemonVersion') != expected:
                return 'service_incompatible'
            profiles = data.get('profiles')
            if not isinstance(profiles, list):return 'service_incompatible'
            # Never accept an unselected/default extension as our profile.
            return 'ready' if any(p.get('contextId')==self.profile and p.get('extensionConnected') is True for p in profiles if isinstance(p,dict)) else 'profile_disconnected'
        except ConnectionRefusedError:
            return 'daemon_stopped'
        except (OSError, ValueError, KeyError, IndexError, http.client.HTTPException):
            return 'service_unavailable'
        finally:
            conn.close()

    def matches(self, directory):
        if not isinstance(directory,str) or not re.fullmatch(r'(?:Profile [0-9]+|Default|SocialRead[A-Za-z0-9-]{1,70})',directory):return False
        profile = self.chrome_root/directory
        if not profile.is_dir() or profile.is_symlink():return False
        storage = profile/'Local Extension Settings'/EXTENSION
        if storage.is_symlink():return False
        # Read only whether the selected extension context is present; do not
        # decode, return or log storage values, cookies or browser preferences.
        try:
            for path in storage.iterdir():
                if path.is_symlink() or path.suffix not in {'.log','.ldb','.sst'} or path.stat().st_size>5*1024*1024:continue
                if path.is_file() and self.profile.encode() in path.read_bytes():return True
        except OSError:pass
        return False

    def binding(self):
        path = self.state/'browser-launch.json'
        if path.is_symlink():raise ReadError('browser_binding_invalid','专用浏览器启动记录无效，已停止自动打开。')
        try:
            saved = json.loads(path.read_text())
            if saved.get('context_id')==self.profile and self.matches(saved.get('profile_directory')):
                return saved['profile_directory']
        except (OSError,ValueError,AttributeError):pass
        try:
            metadata=json.loads((self.chrome_root/'Local State').read_text()).get('profile',{}).get('info_cache',{})
            matches=[directory for directory in metadata if self.matches(directory)]
        except (OSError,ValueError,AttributeError):matches=[]
        if len(matches)!=1:
            raise ReadError('browser_binding_required','未能唯一核对已保存的专用资料，Agent需先检查该窗口；不会新建资料或改用其他账号。')
        self.state.mkdir(parents=True,exist_ok=True,mode=0o700)
        fd,name=tempfile.mkstemp(prefix='.browser-launch-',dir=str(self.state))
        try:
            with os.fdopen(fd,'w') as stream:
                json.dump({'context_id':self.profile,'profile_directory':matches[0],'verified_by':'selected_extension_context'},stream)
            os.replace(name,path)
        finally:
            if os.path.exists(name):os.unlink(name)
        return matches[0]

    def launch(self, directory):
        if platform.system()!='Darwin':
            raise ReadError('browser_reopen_required','当前系统需由Agent打开已授权的专用浏览器资料。')
        candidates=[Path('/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'),Path.home()/'Applications/Google Chrome.app/Contents/MacOS/Google Chrome']
        binary=next((p for p in candidates if p.is_file()),None)
        if not binary:raise ReadError('browser_missing','未找到已安装的Chrome，请由Agent检查安装位置。')
        subprocess.Popen([str(binary),'--profile-directory='+directory,'--new-window','about:blank'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,start_new_session=True)

    def start_daemon(self):
        from .runtime import command_env
        env=command_env()
        node=shutil.which('node',path=env['PATH'])
        script=self.executable.parent/'daemon.js'
        if not node or not script.is_file():raise ReadError('adapter_missing','本地浏览器服务文件缺失，请由Agent修复项目安装。')
        subprocess.Popen([node,str(script)],env=env,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,start_new_session=True)

    def ensure(self, cancel=None):
        import fcntl
        started=time.monotonic()
        # One process-wide lock also prevents simultaneous Agents from opening
        # the same browser repeatedly. Bound lock waiting and support cancel.
        lock_path=self.state/'browser-reconnect.lock'
        if lock_path.is_symlink():raise ReadError('browser_binding_invalid','专用连接锁无效。')
        with lock_path.open('a') as lock:
            os.chmod(lock_path,0o600)
            while True:
                if cancel and cancel.is_set():raise ReadError('cancelled','任务已取消。')
                try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB);break
                except BlockingIOError:
                    if time.monotonic()-started>20:raise ReadError('bridge_busy','其他Agent正在恢复专用连接，请等待该任务结束。')
                    _pause(cancel,.2)
            first=self.status();state=first
            report={'initial_status':first,'daemon_starts':0,'browser_launches':0,'profile_pinned':True}
            if first=='ready':return report
            if first=='service_incompatible':raise ReadError('bridge_version_mismatch','本地服务与项目版本不匹配，已停止自动恢复；Agent需核查在途任务，不会强杀服务。')
            if first not in {'daemon_stopped','profile_disconnected'}:
                raise ReadError('bridge_unavailable','本地服务状态无法确认，已停止自动恢复。')
            if first=='daemon_stopped':
                self.start_daemon();report['daemon_starts']=1
                until=time.monotonic()+5
                while state=='daemon_stopped' and time.monotonic()<until:
                    _pause(cancel,.3);state=self.status()
            if state=='profile_disconnected':
                directory=self.binding()
                self.launch(directory);report['browser_launches']=1
                until=time.monotonic()+15
                while state=='profile_disconnected' and time.monotonic()<until:
                    _pause(cancel,.5);state=self.status()
            report.update(final_status=state,seconds=round(time.monotonic()-started,3))
            self.record(report)
            if state!='ready':
                raise ReadError('bridge_unavailable','已尝试唤起原专用资料，但扩展尚未连接；Agent需检查扩展是否启用，不会重放读取操作。')
            return report

    def record(self, report):
        path=self.state/'connection-recovery.jsonl'
        if path.is_symlink():return
        fd=os.open(str(path),os.O_WRONLY|os.O_CREAT|os.O_APPEND,0o600)
        try:os.write(fd,(json.dumps({'at':time.time(),**report})+'\n').encode())
        finally:os.close(fd)
