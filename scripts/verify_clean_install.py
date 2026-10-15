"""Account-free fresh-source installation acceptance; never copy private state."""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time


def run(args,cwd,timeout=300):
    result=subprocess.run([str(a) for a in args],cwd=cwd,text=True,stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=timeout)
    if result.returncode:raise RuntimeError(Path(str(args[0])).name+' failed: '+result.stderr[-800:]+result.stdout[-800:])
    return result.stdout


def accept(root,work,install_ocr=False):
    source=work/'fresh source with spaces';source.mkdir()
    paths=run(['git','ls-files','--cached','--others','--exclude-standard','-z'],root).split('\0')
    count=0
    for name in set(paths):
        if not name:continue
        if name.startswith(('.state/','.git/','.venv/')):raise AssertionError('private source listed')
        path=root/name
        if path.is_file():
            dest=source/name;dest.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(path,dest);count+=1
    assert not (source/'.state').exists()
    venv=work/'empty-python';run([sys.executable,'-m','venv',venv],work)
    python=venv/('Scripts/python.exe' if os.name=='nt' else 'bin/python')
    config=work/'isolated-client-configs';(config/'.codex').mkdir(parents=True);(config/'.hermes').mkdir()
    (config/'.codex/config.toml').write_text('model = "sentinel-preserved"\n[mcp_servers.existing]\ncommand = "unchanged"\n')
    (config/'.hermes/config.yaml').write_text('model:\n  default: sentinel-preserved\nmcp_servers:\n  existing:\n    command: unchanged\nplatform_toolsets:\n  cli: [existing]\n')
    args=[python,source/'bootstrap.py','--apply','--bridge','project','--config-home',config,'--clients','codex','hermes','generic']
    if install_ocr:args+=['--install-ocr']
    first=json.loads(run(args,source,900))
    assert first['opencli']=='1.8.6'
    assert first['selected_profile'] is None and first['status']=='needs_user_action'
    assert any(a['action']=='dedicated_browser_extension' for a in first['next_actions'])
    assert not (source/'.state/browser-profile.txt').exists()
    assert (source/'.state/opencli/node_modules/.bin/opencli').is_file()
    repeat=json.loads(run(args,source,900))
    # An already-present optional environment is not a new installation.
    assert not repeat['changes'],repeat['changes']
    assert 'model = "sentinel-preserved"' in (config/'.codex/config.toml').read_text()
    assert 'sentinel-preserved' in (config/'.hermes/config.yaml').read_text()
    assert 'command: unchanged' in (config/'.hermes/config.yaml').read_text()
    for path in [config/'.codex/config.toml',config/'.hermes/config.yaml',source/'.state/mcp-config.json']:
        if os.name!='nt':assert path.stat().st_mode&0o777==0o600
    generic=json.loads((source/'.state/mcp-config.json').read_text())['mcpServers']['social-media-read-for-agent']
    proc=subprocess.Popen([generic['command'],*generic['args'],'--mode','demo','mcp'],cwd=work,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
    serial=0
    def rpc(method,params=None,notification=False):
        nonlocal serial
        serial+=1;message={'jsonrpc':'2.0','method':method,'params':params or {}}
        if not notification:message['id']=serial
        proc.stdin.write(json.dumps(message)+'\n');proc.stdin.flush()
        if not notification:
            answer=json.loads(proc.stdout.readline());assert 'error' not in answer,answer;return answer['result']
    def tool(name,args=None):
        result=rpc('tools/call',{'name':name,'arguments':args or {}});assert not result.get('isError'),result
        return json.loads(result['content'][0]['text'])
    def task(name,args):
        job=tool(name,args)
        for _ in range(60):
            result=tool('get_task',{'task_id':job['task_id'],'wait_seconds':1})
            if result['status'] in {'success','partial','failed','cancelled','interrupted'}:return result
        raise AssertionError('task did not finish')
    try:
        rpc('initialize',{'protocolVersion':'2025-06-18','capabilities':{},'clientInfo':{'name':'fresh-install','version':'1'}})
        rpc('notifications/initialized',notification=True)
        platforms=tool('list_platforms');assert platforms['runtime']['connection']=='stable-stdio-gateway'
        assert len(rpc('tools/list')['tools'])==7
        searched=task('search',{'platforms':['xiaohongshu','weibo','zhihu','weixin'],'query':'测试','limit':1})
        assert searched['status']=='success' and len(searched['result']['items'])==4
        for item in searched['result']['items']:
            read=task('read_content',{'content_ref':item['content_ref']});assert read['status']=='success'
        comments=task('read_comments',{'content_ref':searched['result']['items'][0]['content_ref']});assert comments['status']=='success'
    finally:
        proc.stdin.close();proc.wait(timeout=10);proc.stdout.close();proc.stderr.close()
    ocr=None
    if install_ocr:
        runtime=source/'.state/rapidocr-env/bin/python'
        output=run([runtime,'-c',"from PIL import Image;Image.new('RGB',(64,64),'white').save('.state/blank.png')"],source)
        data=json.loads(run([runtime,source/'social_read/rapidocr_worker.py',source/'.state/blank.png'],source,90))
        assert data['status']=='no_text_detected'
        ocr={'installed':True,'warmup':json.loads((source/'.state/rapidocr-warmup.json').read_text())['status'],'blank_image_status':data['status']}
    return {'status':'passed','version':platforms['version'],'source_files':count,'fresh_source':True,'fresh_python_venv':True,'existing_state_copied':False,'account_credentials_copied':False,'project_local_opencli':first['opencli'],'client_configs':['codex','hermes','generic'],'existing_config_values_preserved':True,'second_run_changes':repeat['changes'],'unowned_browser_adopted':False,'stopped_for':'official extension authorization and normal platform login','mcp_transport':'stable-stdio-gateway','mcp_tools':7,'demo_search_platforms':4,'demo_reads':4,'demo_comments':'success','ocr':ocr,'live_logged_in_read_tested':False,'os':sys.platform}


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--output',type=Path);parser.add_argument('--work-dir',type=Path);parser.add_argument('--install-ocr',action='store_true');args=parser.parse_args()
    root=Path(__file__).resolve().parent.parent
    if args.work_dir:
        args.work_dir.mkdir(parents=True,exist_ok=True);result=accept(root,args.work_dir,args.install_ocr)
    else:
        with tempfile.TemporaryDirectory(prefix='social-read-clean-') as tmp:result=accept(root,Path(tmp),args.install_ocr)
    if args.output:args.output.parent.mkdir(parents=True,exist_ok=True);args.output.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps(result,ensure_ascii=False,indent=2))


if __name__=='__main__':main()
