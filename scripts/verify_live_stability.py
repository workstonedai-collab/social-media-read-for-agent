"""One bounded live reading sample per Shanghai calendar day; private receipts.

Default prints an existing summary. --run explicitly visits logged-in platforms.
No login/account mutation, automatic scheduling, or public uploads are performed.
"""
import argparse
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from social_read import __version__
from social_read.engine import Engine, TERMINAL
from social_read.model import ReadError

SHANGHAI = timezone(timedelta(hours=8))
SITES = ['xiaohongshu', 'weixin', 'weibo', 'zhihu']


def atomic_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, name = tempfile.mkstemp(prefix='.receipt-', dir=str(path.parent))
    try:
        with os.fdopen(fd, 'w') as stream:
            json.dump(data, stream, ensure_ascii=False, indent=2)
            stream.write('\n')
        os.replace(name, path)
    finally:
        if os.path.exists(name):os.unlink(name)


def summarize(directory):
    records=[]
    for path in sorted(Path(directory).glob('????-??-??.json')):
        try:
            data=json.loads(path.read_text())
            if data.get('mode')=='live' and data.get('finished_at') and data.get('date')==path.stem:
                records.append(data)
        except (ValueError,OSError):pass
    return {'timezone':'Asia/Shanghai','observed_dates':[r['date'] for r in records],
        'distinct_observed_days':len({r['date'] for r in records}),
        'dates_all_requested_reads_passed':[r['date'] for r in records if r.get('all_requested_reads_passed')],
        'dates_all_requested_comments_passed':[r['date'] for r in records if r.get('all_requested_comments_passed')],
        'days':[{k:r.get(k) for k in ('date','version','requested_platforms','query','all_requested_reads_passed','all_requested_comments_passed','results')} for r in records],
        'notice':'不同日期分别计数；同日重复不增加天数。登录、验证、拒绝访问及超时均保留，不能用同日重试代替跨日期验收。'}


def sample(engine, sites, query, stamp=None):
    start=stamp or datetime.now(SHANGHAI)
    record={'date':start.date().isoformat(),'started_at':start.isoformat(),'version':__version__,
        'mode':engine.mode,'requested_platforms':sites,'query':query,'results':[]}
    def task(action,args):
        job=engine.wait(engine.submit(action,args)['task_id'],240 if action=='search' else 180)
        if job['status'] not in TERMINAL:
            engine.cancel(job['task_id'])
            job=engine.wait(job['task_id'],10)
        return job
    for site in sites:
        row={'platform':site};record['results'].append(row)
        try:
            search=task('search',{'platforms':[site],'query':query,'limit':1})
            found=search.get('result',{});items=found.get('items',[])
            row['search']={'status':search['status'],'task_id':search['task_id'],'returned':found.get('coverage',{}).get('returned_by_platform',{}).get(site,0),'errors':found.get('errors',[]),'recovery':[i['recovery'] for i in items if i.get('recovery')]}
            if search['status']!='success' or not items:continue
            item=items[0]
            read=task('read',{'content_ref':item['content_ref'],'refresh':True})
            content=read.get('result',{}).get('content',{})
            row['read']={'status':read['status'],'task_id':read['task_id'],'body_chars':content.get('body_chars',0),'body_sha256':content.get('body_sha256'),'cache':content.get('cache'),'coverage':content.get('coverage'),'errors':read.get('result',{}).get('errors',[])}
            # Stop this platform on any unsuccessful/partial body. No extra
            # retries here: underlying readers have their own bounded policy.
            if read['status']!='success':continue
            if site=='weixin':row['comments']={'status':'unsupported','requested':False};continue
            comments=task('comments',{'content_ref':item['content_ref'],'limit':2,'replies_limit':1})
            result=comments.get('result',{})
            row['comments']={'status':comments['status'],'requested':True,'task_id':comments['task_id'],'coverage':result.get('coverage'),'errors':result.get('errors',[])}
        except ReadError as error:
            row['error']=error.result()
    record['all_requested_reads_passed']=len(record['results'])==len(sites) and all(r.get('search',{}).get('status')=='success' and r.get('read',{}).get('status')=='success' and r.get('read',{}).get('body_chars',0)>0 for r in record['results'])
    comments=[r for r in record['results'] if r['platform']!='weixin']
    record['all_requested_comments_passed']=all(r.get('comments',{}).get('status')=='success' for r in comments)
    record['finished_at']=datetime.now(SHANGHAI).isoformat()
    return record


def main():
    os.umask(0o077)
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run',action='store_true')
    parser.add_argument('--state-dir',type=Path,default=ROOT/'.state')
    parser.add_argument('--platforms',nargs='+',choices=SITES,default=SITES)
    parser.add_argument('--query',default='个人知识管理工具')
    args=parser.parse_args();directory=args.state_dir/'stability';today=datetime.now(SHANGHAI).date().isoformat()
    if not args.run:
        print(json.dumps(summarize(directory),ensure_ascii=False,indent=2));return
    directory.mkdir(parents=True,exist_ok=True,mode=0o700)
    import fcntl
    with (directory/'run.lock').open('a') as lock:
        try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:
            print(json.dumps({'status':'already_running','date':today}));return
        receipt=directory/(today+'.json')
        if receipt.exists():
            print(json.dumps({'status':'already_recorded','date':today,'summary':summarize(directory)},ensure_ascii=False,indent=2));return
        profile_file=args.state_dir/'browser-profile.txt'
        if not profile_file.is_file():raise ReadError('profile_required','请先连接已经授权的专用浏览器资料。')
        # Do not copy profiles or claim login from this marker; actual tasks
        # below prove which platform paths worked on this particular date.
        engine=Engine(args.state_dir,'live',profile_file.read_text().strip())
        try:record=sample(engine,list(dict.fromkeys(args.platforms)),args.query)
        finally:engine.shutdown()
        atomic_json(receipt,record)
        print(json.dumps({'status':'recorded','date':today,'summary':summarize(directory)},ensure_ascii=False,indent=2))


if __name__=='__main__':
    try:main()
    except ReadError as error:
        print(json.dumps(error.result(),ensure_ascii=False));raise SystemExit(1)
