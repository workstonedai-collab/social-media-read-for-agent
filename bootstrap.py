"""Agent-first, repeatable setup. No passwords, cookies or browser policy changes."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time
import tempfile

ROOT = Path(__file__).resolve().parent
STATE = ROOT / ".state"
SERVER = "social-media-read-for-agent"
EXTENSION = "https://chromewebstore.google.com/detail/opencli/ildkmabpimmkaediidaifkhjpohdnifk"
TOOLS = ["list_platforms", "connection_status", "search", "read_content", "read_comments", "get_task", "cancel_task"]
LOGIN = {"xiaohongshu":"https://www.xiaohongshu.com/explore", "weibo":"https://weibo.com/", "zhihu":"https://www.zhihu.com/", "weixin":"https://weixin.sogou.com/"}


def command(args, timeout=30, env=None):
    result = subprocess.run(args, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout, env=env)
    if result.returncode:
        raise RuntimeError("command_failed:" + Path(args[0]).name)
    return result.stdout


def private_write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    old = path.read_text() if path.exists() else None
    if old == text:
        return False
    if old is not None:
        backups = STATE / "setup-backups"; backups.mkdir(parents=True,exist_ok=True,mode=0o700)
        backup = backups / (path.name + "-" + str(time.time_ns()))
        backup.write_text(old); backup.chmod(0o600)
    fd, name = tempfile.mkstemp(prefix='.'+path.name+'.social-read-',dir=str(path.parent))
    temporary = Path(name)
    try:
        with os.fdopen(fd,"w") as out:out.write(text)
        os.replace(temporary,path)
    finally:
        if temporary.exists():temporary.unlink()
    return True


def merge_codex(text, python):
    # Replace only our section and its child tables; retain all other byte content.
    section = re.compile(r"(?m)^\[([^\]\n]+)\][^\n]*(?:\n|$)")
    spans = list(section.finditer(text)); pieces=[]; last=0
    for i, match in enumerate(spans):
        name = match.group(1).strip()
        prefix = r"mcp_servers\s*\.\s*(?:" + re.escape(SERVER) + r'|"' + re.escape(SERVER) + r'"|\'' + re.escape(SERVER) + r"\')\s*(?:\.|$)"
        if re.match(prefix,name):
            end=spans[i+1].start() if i+1<len(spans) else len(text)
            pieces.append(text[last:match.start()]);last=end
    pieces.append(text[last:]); base=''.join(pieces).rstrip()
    block = '\n\n[mcp_servers.'+SERVER+']\ncommand = '+json.dumps(python)+'\nargs = ['+json.dumps(str(ROOT/'start_mcp.py'))+']\nstartup_timeout_sec = 15\ntool_timeout_sec = 45\nenabled_tools = '+json.dumps(TOOLS)+'\n'
    return base + block


def connected_profiles(executable):
    try:
        output=command([executable,"profile","list"],20)
        # OpenCLI 1.8.6 profile list is human-readable (no -f flag).
        return re.findall(r"(?m)^\s*([\w.-]{1,80})\s+[—–-]\s+connected\b",output)
    except (RuntimeError,subprocess.TimeoutExpired):return []


def discover_profile(profiles, saved, baseline=None):
    if saved in profiles:return saved
    # Only auto-select a newly connected profile after we opened our owned browser.
    if baseline is not None:
        added=set(profiles)-set(baseline)
        if len(added)==1:return added.pop()
    return None


def yaml_runtime(isolated=False):
    """Use an existing YAML parser or install it privately, never in the Agent env."""
    candidates=[Path(sys.executable),STATE/'setup-env/bin/python',STATE/'rapidocr-env/bin/python']
    if not isolated:candidates.append(Path.home()/".hermes/hermes-agent/venv/bin/python")
    for runtime in candidates:
        if runtime.is_file():
            try:
                command([str(runtime),'-c','import yaml'],10)
                return str(runtime)
            except (RuntimeError,subprocess.TimeoutExpired):pass
    environment=STATE/'setup-env'
    runtime=environment/('Scripts/python.exe' if os.name=='nt' else 'bin/python')
    if not runtime.exists():command([sys.executable,'-m','venv',str(environment)],120)
    command([str(runtime),'-m','pip','install','PyYAML==6.0.3'],180)
    return str(runtime)


def configure_hermes(path, python, isolated=False):
    runtime=yaml_runtime(isolated)
    # Reuse Hermes' YAML parser, preserving settings semantically, no secret output.
    script='''import json,sys,yaml
from pathlib import Path
p=Path(sys.argv[1]);d=yaml.safe_load(p.read_text()) if p.exists() else {}
if d is None:d={}
if not isinstance(d,dict):raise ValueError('invalid_config')
r=json.loads(sys.stdin.read());servers=d.setdefault('mcp_servers',{})
if not isinstance(servers,dict):raise ValueError('invalid_servers')
servers[r['name']]=r['entry']
t=d.setdefault('platform_toolsets',{}).setdefault('cli',[])
if not isinstance(t,list):raise ValueError('invalid_toolset')
if r['name'] not in t:t.append(r['name'])
print(yaml.safe_dump(d,allow_unicode=True,sort_keys=False),end='')
'''
    data={"name":SERVER,"entry":{"command":python,"args":[str(ROOT/'start_mcp.py')],"timeout":45,"connect_timeout":15,"enabled":True,"tools":{"include":TOOLS}}}
    result=subprocess.run([str(runtime),"-c",script,str(path)],input=json.dumps(data),text=True,stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=10)
    if result.returncode:raise RuntimeError("hermes_config_merge_failed")
    return private_write(path,result.stdout)


def install_ocr():
    python=next((str(p) for p in [Path(sys.executable),Path.home()/'.hermes/hermes-agent/venv/bin/python',*[Path(shutil.which(name) or '/nonexistent') for name in ['python3.11','python3.12','python3.13','python3.10']]] if p.is_file() and command([str(p),'-c','import sys;print((3,10) <= sys.version_info < (3,14))']).strip()=='True'),None)
    if not python:raise RuntimeError('ocr_python_310_to_313_required')
    env=STATE/'rapidocr-env'; executable=env/('Scripts/python.exe' if os.name=='nt' else 'bin/python')
    if not executable.exists():command([python,'-m','venv',str(env)],120)
    command([str(executable),'-m','pip','install','-r',str(ROOT/'requirements-ocr.txt')],600)
    script='''import rapidocr,hashlib,importlib.metadata as m,json
from pathlib import Path
p=Path(rapidocr.__file__).parent
print(json.dumps({'rapidocr':m.version('rapidocr'),'onnxruntime':m.version('onnxruntime'),'models':{f.name:hashlib.sha256(f.read_bytes()).hexdigest() for f in p.rglob('*.onnx')}},sort_keys=True))'''
    lock = command([str(executable),'-c',script],30)
    private_write(STATE/'rapidocr-lock.json',lock)
    environment_id = hashlib.sha256(lock.encode()).hexdigest()
    warmup = STATE/'rapidocr-warmup.json'
    previous = json.loads(warmup.read_text()) if warmup.exists() else {}
    if previous.get('environment_id') != environment_id:
        # First model/runtime loading is part of setup, not the first user note.
        warm_script = "import contextlib,sys; from rapidocr import RapidOCR; from PIL import Image; import numpy as np; engine=RapidOCR(params={'Global.log_level':'critical','EngineConfig.onnxruntime.intra_op_num_threads':2,'EngineConfig.onnxruntime.inter_op_num_threads':2}); engine(np.asarray(Image.new('RGB',(64,64),'white')))"
        command([str(executable),'-c',warm_script],180)
        private_write(warmup,json.dumps({'environment_id':environment_id,'status':'success'}))


def verify_reading(engine, sites, query):
    """Check only requested platforms and require fresh, actual bodies.

    A saved login or old cached body is not today's reading acceptance.
    """
    sites=list(dict.fromkeys(sites))
    job=engine.wait(engine.submit('search',{'platforms':sites,'query':query,'limit':1})['task_id'],180)
    checks=[]
    for item in job.get('result',{}).get('items',[]):
        if item['platform'] not in sites:continue
        read=engine.wait(engine.submit('read',{'content_ref':item['content_ref'],'refresh':True})['task_id'],180)
        content=read.get('result',{}).get('content',{})
        checks.append({'platform':item['platform'],'status':read['status'],'body_chars':content.get('body_chars',0),'ref':item['content_ref'],'errors':read.get('result',{}).get('errors',[])})
    counts=job.get('result',{}).get('coverage',{}).get('returned_by_platform',{})
    accepted=job['status']=='success' and {c['platform'] for c in checks}==set(sites) and all(c['status']=='success' and c['body_chars']>0 for c in checks)
    return {'requested_platforms':sites,'search_status':job['status'],'counts':counts,'errors':job.get('result',{}).get('errors',[]),'reads':checks,'accepted':accepted}


def main():
    os.umask(0o077)
    p=argparse.ArgumentParser(description='Agent 自动设置；默认只检查，--apply 合并配置并安装缺失的 OpenCLI。')
    p.add_argument('--apply',action='store_true');p.add_argument('--clients',nargs='+',choices=['codex','hermes','generic'],default=None)
    p.add_argument('--profile');p.add_argument('--open-browser',action='store_true');p.add_argument('--open-login',choices=list(LOGIN));p.add_argument('--install-ocr',action='store_true')
    p.add_argument('--config-home',type=Path,help='隔离配置目录；默认使用当前用户/客户端配置位置')
    p.add_argument('--bridge',choices=['auto','project'],default='auto',help='project强制安装并使用本项目OpenCLI')
    p.add_argument('--verify',action='store_true');p.add_argument('--query',default='个人知识管理工具')
    p.add_argument('--platforms',nargs='+',choices=list(LOGIN),help='仅验收用户需要的平台；默认四个平台')
    a=p.parse_args();STATE.mkdir(exist_ok=True,mode=0o700)
    local=STATE/'opencli/node_modules/.bin/opencli'
    executable=shutil.which('opencli') or (str(local) if local.exists() else None)
    if not executable and (Path.home()/'.local/bin/opencli').exists():executable=str(Path.home()/'.local/bin/opencli')
    if a.bridge=='project':executable=str(local) if local.exists() else None
    result={'project':SERVER,'state_dir':str(STATE),'changes':[],'next_actions':[]}
    if not executable and a.apply:
        npm=shutil.which('npm');node=shutil.which('node')
        if not npm or not node:raise RuntimeError('node_20_18_required_install_node_then_rerun')
        version=tuple(map(int,command([node,'--version']).strip().lstrip('v').split('.')))
        if version<(20,18,1):raise RuntimeError('node_20_18_required')
        command([npm,'install','--prefix',str(STATE/'opencli'),'--no-audit','--no-fund','@jackwener/opencli@1.8.6'],300)
        executable=str(local);result['changes'].append('installed_project_local_opencli')
    result['opencli']=command([executable,'--version']).strip() if executable else 'missing'
    profiles=connected_profiles(executable) if executable else []
    config=STATE/'browser-profile.txt';saved=config.read_text().strip() if config.exists() else None
    marker=STATE/'onboarding-browser.json';baseline=json.loads(marker.read_text()).get('before') if marker.exists() else None
    selected=a.profile or discover_profile(profiles,saved,baseline)
    if a.profile and a.profile not in profiles:raise RuntimeError('selected_profile_not_connected')
    if selected and a.apply:private_write(config,selected+'\n')
    result.update(connected_profiles=profiles,selected_profile=selected)
    if not executable:result['next_actions'].append({'action':'install_opencli','message':'由 Agent 检查 Node 20.18.1+ 后运行 --apply，安装项目独立 OpenCLI。'})
    if not selected:result['next_actions'].append({'action':'dedicated_browser_extension','url':EXTENSION,'message':'让 Agent 用 --open-browser 打开项目专用 Chrome；用户确认一次扩展权限。然后重跑 --apply，自动识别新连接的资料。不要使用日常资料或导出 Cookie。'})
    if a.open_browser and selected:
        raise RuntimeError('dedicated_profile_already_connected_use_open_login')
    if a.open_browser:
        if sys.platform!='darwin':raise RuntimeError('automatic_browser_launch_macos_only')
        if not marker.exists():private_write(marker,json.dumps({'before':profiles}))
        command(['/usr/bin/open','-na','Google Chrome','--args','--user-data-dir='+str(STATE/'chrome-profile'),'--no-first-run',EXTENSION])
        result['changes'].append('opened_owned_chrome_profile')
    clients=a.clients or [name for name,location in [('codex',Path.home()/'.codex'),('hermes',Path.home()/'.hermes')] if location.is_dir()]
    result['clients']=clients
    result['configuration_targets']={}
    if a.apply:
        for client in clients:
            if client=='generic':
                path=STATE/'mcp-config.json'
                changed=private_write(path,json.dumps({'mcpServers':{SERVER:{'command':sys.executable,'args':[str(ROOT/'start_mcp.py')]}}},indent=2)+'\n')
            else:
                if a.config_home:
                    path=a.config_home/('.codex/config.toml' if client=='codex' else '.hermes/config.yaml')
                else:
                    base=Path(os.environ.get('CODEX_HOME',str(Path.home()/'.codex'))) if client=='codex' else Path(os.environ.get('HERMES_HOME',str(Path.home()/'.hermes')))
                    path=base/('config.toml' if client=='codex' else 'config.yaml')
                changed=private_write(path,merge_codex(path.read_text() if path.exists() else '',sys.executable)) if client=='codex' else configure_hermes(path,sys.executable,isolated=bool(a.config_home))
            result['configuration_targets'][client]=str(path)
            if changed:result['changes'].append('configured_'+client)
    if a.install_ocr:
        if not a.apply:raise RuntimeError('use_apply_to_install_ocr')
        before=(STATE/'rapidocr-lock.json').read_text() if (STATE/'rapidocr-lock.json').exists() else None
        install_ocr()
        if before!=(STATE/'rapidocr-lock.json').read_text():result['changes'].append('installed_optional_rapidocr')
    if a.open_login:
        if not selected or not executable:raise RuntimeError('dedicated_profile_required')
        env=dict(os.environ,OPENCLI_PROFILE=selected,OPENCLI_WINDOW='foreground')
        command([executable,'browser','social-read-login','tab','new',LOGIN[a.open_login]],30,env)
        result['next_actions'].append({'action':'user_login','platform':a.open_login,'message':'请用户在平台页面扫码或输入密码；不向对话、配置或日志提供密码。完成后 Agent 执行 --verify。'})
    if a.verify:
        if not selected:raise RuntimeError('dedicated_profile_required')
        sys.path.insert(0,str(ROOT));from social_read.engine import Engine
        engine=Engine(STATE,'live',selected)
        try:
            result['verification']=verify_reading(engine,a.platforms or list(LOGIN),a.query)
        finally:engine.shutdown()
    result['status']='needs_user_action' if result['next_actions'] else 'ready_for_verification' if selected else 'needs_setup'
    if 'verification' in result:result['status']='ready' if result['verification']['accepted'] else 'partial'
    # This progress describes evidence, not an inferred login state. It lets
    # agents explain one pending user action without exposing internal IDs.
    result['setup_progress']={
        'dependencies':'ready' if executable else 'missing',
        'dedicated_browser':'connected' if selected else 'authorization_needed',
        'client_configuration':'configured' if a.apply and clients else 'not_changed',
        'live_reading':'verified' if result.get('verification',{}).get('accepted') else 'partial' if 'verification' in result else 'not_checked'}
    result['user_message']='设置与读取验收已完成，可以直接提出调研问题。' if result['status']=='ready' else '已完成部分设置，请由 Agent 继续处理 next_actions；你只需在需要时确认扩展授权或在平台页面正常登录。'
    print(json.dumps(result,ensure_ascii=False,indent=2));return 0


if __name__=='__main__':
    try:raise SystemExit(main())
    except (RuntimeError,OSError,subprocess.TimeoutExpired) as e:
        print(json.dumps({'status':'setup_incomplete','reason':str(e).splitlines()[0][:180],'message':'已完成配置保留，请由 Agent 处理原因后重跑；不要索取 Cookie 或在对话收集密码。'},ensure_ascii=False));raise SystemExit(1)
