"""Agent-first, repeatable setup. No passwords, cookies or browser policy changes."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import subprocess
import sys
import time
import tempfile

ROOT = Path(__file__).resolve().parent
STATE = ROOT / ".state"
from social_read.runtime import command_env
from social_read.install_support import SetupError, download_env, install_command, install_opencli, opencli_dependencies_ready
os.environ["PATH"] = command_env()["PATH"]
SERVER = "social-media-read-for-agent"
EXTENSION_ID = "ildkmabpimmkaediidaifkhjpohdnifk"
EXTENSION = "https://chromewebstore.google.com/detail/opencli/ildkmabpimmkaediidaifkhjpohdnifk"
TOOLS = ["list_platforms", "connection_status", "search", "read_content", "read_comments", "get_task", "cancel_task"]
LOGIN = {"xiaohongshu":"https://www.xiaohongshu.com/explore", "weibo":"https://weibo.com/", "zhihu":"https://www.zhihu.com/", "weixin":"https://weixin.sogou.com/"}


def progress(message):
    print(message,file=sys.stderr,flush=True)


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
    if saved:return None # An offline saved identity must never become a new blank profile.
    # Only auto-select a newly connected profile after we opened our owned browser.
    if baseline is not None:
        added=set(profiles)-set(baseline)
        if len(added)==1:return added.pop()
    return None


def prepare_extension(browser_dir):
    """Chrome's documented per-user Web Store registration; no forced enable.

    Only the project-owned user-data-dir is eligible. Never touch the normal
    Chrome profile, policies, secure preferences or downloaded CRX packages.
    """
    browser_dir=Path(browser_dir)
    if browser_dir != STATE/'chrome-profile' or browser_dir.is_symlink():
        raise RuntimeError('owned_browser_directory_required')
    parent=browser_dir/'External Extensions'
    if parent.is_symlink():raise RuntimeError('extension_directory_symlink')
    parent.mkdir(parents=True,exist_ok=True,mode=0o700)
    target=parent/(EXTENSION_ID+'.json')
    expected=json.dumps({'external_update_url':'https://clients2.google.com/service/update2/crx'},indent=2)+'\n'
    if target.is_symlink():raise RuntimeError('extension_registration_symlink')
    if target.exists() and target.read_text()!=expected:raise RuntimeError('extension_registration_conflict')
    return private_write(target,expected)


def browser_plan(marker, profiles, layout=None):
    old=json.loads(marker.read_text()) if marker.exists() else None
    if old:
        saved_layout=old.get('layout','isolated')
        if layout and layout!=saved_layout:raise RuntimeError('browser_layout_already_saved')
        return {**old,'layout':saved_layout}
    layout=layout or 'chrome-profile'
    plan={'before':profiles,'layout':layout}
    if layout=='chrome-profile':plan['profile_directory']='SocialRead-'+secrets.token_hex(6)
    private_write(marker,json.dumps(plan))
    return plan


def launch_browser(plan, extension_install='auto'):
    if plan['layout']=='isolated':
        prepared=extension_install=='auto'
        if prepared:prepare_extension(STATE/'chrome-profile')
        command(['/usr/bin/open','-na','Google Chrome','--args','--user-data-dir='+str(STATE/'chrome-profile'),'--no-first-run',EXTENSION])
        return {'layout':'isolated','source':'chrome_web_store','prepared':prepared,'enabled':'awaiting_user_confirmation'}
    directory=plan.get('profile_directory','')
    if not re.fullmatch(r'SocialRead[A-Za-z0-9-]{1,64}',directory):raise RuntimeError('owned_profile_directory_invalid')
    # A separate normal Chrome profile shares the app process, allowing hosts
    # that select apps by bundle id to assist through the official store UI.
    # No global External Extensions registration: it could affect daily profiles.
    candidates=[Path('/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'),Path.home()/'Applications/Google Chrome.app/Contents/MacOS/Google Chrome']
    binary=next((str(p) for p in candidates if p.is_file()),None)
    if not binary:raise RuntimeError('google_chrome_not_installed')
    proc=subprocess.Popen([binary,'--profile-directory='+directory,'--new-window',EXTENSION],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,start_new_session=True)
    try:
        if proc.wait(timeout=2)!=0:raise RuntimeError('owned_browser_launch_failed')
    except subprocess.TimeoutExpired:pass # A newly launched Chrome stays open.
    return {'layout':'chrome-profile','source':'chrome_web_store','prepared':False,'enabled':'agent_opens_store_install_user_confirms_permissions'}


def wait_for_profile(executable,saved,baseline,seconds):
    if not executable:raise RuntimeError('opencli_required_before_wait')
    progress('等待专用 Chrome 的扩展权限确认；连接出现后将自动继续。')
    deadline=time.monotonic()+seconds
    while True:
        profiles=connected_profiles(executable)
        selected=discover_profile(profiles,saved,baseline)
        if selected:
            progress('专用浏览器已连接，Agent 自动继续配置与登录引导。')
            return profiles,selected
        added=set(profiles)-set(baseline or [])
        if len(added)>1:raise RuntimeError('multiple_new_profiles_need_window_confirmation')
        if time.monotonic()>=deadline:return profiles,None
        time.sleep(min(2,max(0,deadline-time.monotonic())))


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
    progress('正在准备可选本机图片识别；首次需要下载组件和模型。')
    install_command([str(executable),'-m','pip','install','--disable-pip-version-check','--timeout','20','--retries','1','--cache-dir',str(STATE/'download-cache/pip'),'-r',str(ROOT/'requirements-ocr.txt')],STATE,'ocr_dependencies',600,download_env('ocr','https://pypi.org/simple/rapidocr/',STATE))
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


def verify_with_pending_logins(engine,sites,query,pending):
    waiting=[site for site in sites if site in pending]
    readable=[site for site in sites if site not in pending]
    result=verify_reading(engine,readable,query) if readable else {'requested_platforms':[],'search_status':'not_run','counts':{},'errors':[],'reads':[],'accepted':False}
    result['requested_platforms']=sites
    if waiting:
        result['accepted']=False
        result['awaiting_login']=waiting
        result['errors']+=[{'platform':site,'status':'login_pending','message':'用户尚未完成本次登录，本平台未发起搜索；可先使用其他平台。'} for site in waiting]
    return result


def main():
    os.umask(0o077)
    p=argparse.ArgumentParser(description='Agent 自动设置；默认只检查，--apply 合并配置并安装缺失的 OpenCLI。')
    p.add_argument('--apply',action='store_true');p.add_argument('--clients',nargs='+',choices=['codex','hermes','generic'],default=None)
    p.add_argument('--profile');p.add_argument('--open-browser',action='store_true');p.add_argument('--open-login',choices=list(LOGIN));p.add_argument('--install-ocr',action='store_true')
    p.add_argument('--config-home',type=Path,help='隔离配置目录；默认使用当前用户/客户端配置位置')
    p.add_argument('--bridge',choices=['auto','project'],default='auto',help='project强制安装并使用本项目OpenCLI')
    p.add_argument('--wait-for-browser',type=int,default=0,metavar='SECONDS',help='授权期间由Agent等待专用连接，0至300秒；无需用户回报编号')
    p.add_argument('--browser-layout',choices=['chrome-profile','isolated'],help='默认新建Chrome资料，便于Agent操作官方商店；isolated使用项目独立浏览器进程')
    p.add_argument('--extension-install',choices=['auto','store'],default='auto',help='auto在macOS专用资料中准备官方商店扩展，用户仍确认启用；store仅打开商店')
    p.add_argument('--login-completed',nargs='+',choices=list(LOGIN),help='用户已明确说完成登录后由Agent恢复；此标记本身不代表读取验收通过')
    p.add_argument('--verify',action='store_true');p.add_argument('--query',default='个人知识管理工具')
    p.add_argument('--platforms',nargs='+',choices=list(LOGIN),help='仅验收用户需要的平台；默认四个平台')
    a=p.parse_args()
    if not 0 <= a.wait_for_browser <= 300:p.error("--wait-for-browser must be between 0 and 300")
    STATE.mkdir(exist_ok=True,mode=0o700)
    local=STATE/'opencli/node_modules/.bin/opencli'
    executable=str(local) if local.exists() else shutil.which('opencli')
    if not executable and (Path.home()/'.local/bin/opencli').exists():executable=str(Path.home()/'.local/bin/opencli')
    if a.bridge=='project':executable=str(local) if local.exists() else None
    result={'project':SERVER,'state_dir':str(STATE),'changes':[],'next_actions':[]}
    if a.apply and (not executable or (a.bridge=='project' and not opencli_dependencies_ready(ROOT,STATE))):
        npm=shutil.which('npm');node=shutil.which('node')
        if not npm or not node:raise RuntimeError('node_20_18_required_install_node_then_rerun')
        version=tuple(map(int,command([node,'--version']).strip().lstrip('v').split('.')))
        if version<(20,18,1):raise RuntimeError('node_20_18_required')
        progress('正在安装项目专用读取组件；不会修改 Agent 自己的环境。')
        install_opencli(ROOT,STATE,npm)
        executable=str(local);result['changes'].append('installed_project_local_opencli')
    result['opencli']=command([executable,'--version']).strip() if executable else 'missing'
    profiles=connected_profiles(executable) if executable else []
    config=STATE/'browser-profile.txt';saved=config.read_text().strip() if config.exists() else None
    marker=STATE/'onboarding-browser.json';baseline=json.loads(marker.read_text()).get('before') if marker.exists() else None
    selected=a.profile or discover_profile(profiles,saved,baseline)
    if a.wait_for_browser and not selected and not a.open_browser:
        if baseline is None:raise RuntimeError('owned_browser_must_be_opened_first')
        profiles,selected=wait_for_profile(executable,saved,baseline,a.wait_for_browser)
    if a.profile and a.profile not in profiles:raise RuntimeError('selected_profile_not_connected')
    if selected and a.apply:private_write(config,selected+'\n')
    result.update(connected_profiles=profiles,selected_profile=selected)
    if not executable:result['next_actions'].append({'action':'install_opencli','message':'由 Agent 检查 Node 20.18.1+ 后运行 --apply，安装项目独立 OpenCLI。'})
    if not selected and saved:result['next_actions'].append({'action':'reconnect_saved_browser','message':'已保存专用连接但当前离线。Agent直接执行读取即可先恢复原资料；不要重新安装、采用新连接或覆盖现有选择。'})
    if not selected and not saved:result['next_actions'].append({'action':'dedicated_browser_extension','url':EXTENSION,'message':'由 Agent 在空白专用 Chrome 资料打开官方商店并点击添加至 Chrome；用户只确认最终权限。用 --apply --wait-for-browser 180 自动继续连接，不让用户找资料编号或复制 Cookie。'})
    if a.open_browser and selected:
        raise RuntimeError('dedicated_profile_already_connected_use_open_login')
    if a.open_browser:
        if saved:raise RuntimeError('saved_browser_offline_reconnect_instead_of_onboarding')
        if sys.platform!='darwin':raise RuntimeError('automatic_browser_launch_macos_only')
        plan=browser_plan(marker,profiles,a.browser_layout)
        result['extension_setup']=launch_browser(plan,a.extension_install)
        progress('已打开空白专用Chrome资料。由Agent操作官方商店安装，用户只确认最终权限。' if plan['layout']=='chrome-profile' else '已打开独立Chrome资料；自动准备后仍需用户确认启用权限。')
        result['changes'].append('opened_owned_chrome_profile')
    clients=a.clients or [name for name,location in [('codex',Path(os.environ.get('CODEX_HOME',str(Path.home()/'.codex')))),('hermes',Path(os.environ.get('HERMES_HOME',str(Path.home()/'.hermes'))))] if location.is_dir()]
    if a.apply and not clients:clients=['generic']
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
    if a.open_browser and a.wait_for_browser:
        profiles,selected=wait_for_profile(executable,saved,baseline if baseline is not None else result['connected_profiles'],a.wait_for_browser)
        if selected:
            private_write(config,selected+'\n');result.update(selected_profile=selected,connected_profiles=profiles)
            result['next_actions']=[x for x in result['next_actions'] if x['action']!='dedicated_browser_extension']
    login_marker=STATE/'pending-logins.json'
    pending=json.loads(login_marker.read_text()) if login_marker.exists() else []
    if not isinstance(pending,list) or any(site not in LOGIN for site in pending):raise RuntimeError('pending_login_state_invalid')
    if a.login_completed:
        pending=[site for site in pending if site not in a.login_completed]
        private_write(login_marker,json.dumps(pending))
    if a.open_login and selected and executable:
        env=dict(os.environ,OPENCLI_PROFILE=selected,OPENCLI_WINDOW='foreground')
        command([executable,'browser','social-read-login','tab','new',LOGIN[a.open_login]],30,env)
        pending=list(dict.fromkeys(pending+[a.open_login]));private_write(login_marker,json.dumps(pending))
        progress('已打开平台登录页；用户直接在浏览器扫码或输入密码。')
        result['next_actions'].append({'action':'user_login','platform':a.open_login,'message':'请用户在平台页面扫码或输入密码；不向对话、配置或日志提供密码。完成后 Agent 执行 --verify。'})
    if a.verify:
        if not selected:raise RuntimeError('dedicated_profile_required')
        sys.path.insert(0,str(ROOT));from social_read.engine import Engine
        sites=a.platforms or list(LOGIN)
        engine=Engine(STATE,'live',selected) if any(site not in pending for site in sites) else None
        try:
            result['verification']=verify_with_pending_logins(engine,sites,a.query,pending)
        finally:
            if engine:engine.shutdown()
    result['status']='needs_user_action' if result['next_actions'] else 'ready_for_verification' if selected else 'needs_setup'
    if 'verification' in result:result['status']='ready' if result['verification']['accepted'] else 'needs_user_action' if result['verification']['search_status']=='not_run' else 'partial'
    # This progress describes evidence, not an inferred login state. It lets
    # agents explain one pending user action without exposing internal IDs.
    result['setup_progress']={
        'dependencies':'ready' if executable else 'missing',
        'dedicated_browser':'connected' if selected else 'authorization_needed',
        'client_configuration':'configured' if a.apply and clients else 'not_changed',
        'live_reading':'verified' if result.get('verification',{}).get('accepted') else 'awaiting_login' if result.get('verification',{}).get('search_status')=='not_run' else 'partial' if 'verification' in result else 'not_checked'}
    result['pending_logins']=pending
    result['user_message']=('已保留待登录步骤，当前未访问这些平台。可先使用其他平台，方便时登录并告诉Agent继续。' if result.get('verification',{}).get('awaiting_login') else '设置与读取验收已完成，可以直接提出调研问题。' if result['status']=='ready' else '专用浏览器已连接，请直接在打开的平台页面完成登录。' if any(x['action']=='user_login' for x in result['next_actions']) else '环境和连接已就绪，由 Agent 继续实际搜索和阅读验收。' if selected else '环境已就绪；请在专用 Chrome 窗口确认 OpenCLI 扩展权限，Agent 会继续处理连接。')
    print(json.dumps(result,ensure_ascii=False,indent=2));return 0


if __name__=='__main__':
    try:raise SystemExit(main())
    except (RuntimeError,OSError,subprocess.TimeoutExpired) as e:
        detail=e.result() if isinstance(e,SetupError) else {'reason':str(e).splitlines()[0][:180]}
        print(json.dumps({'status':'setup_incomplete',**detail,'message':'已完成配置保留，请由 Agent 处理失败步骤后重跑；用户无需重装全部环境，不要索取 Cookie 或在对话收集密码。'},ensure_ascii=False));raise SystemExit(1)
