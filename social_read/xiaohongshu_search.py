"""Search waits for usable cards, not skeleton placeholders."""
import json
import time
from datetime import datetime,timezone,timedelta
from urllib.parse import urlencode,urlsplit
from .browser_page import ReadPage, _pause
from .model import ReadError,search_item,validate_url

def search_notes(runner,query,limit,cancel):
    target="https://www.xiaohongshu.com/search_result?"+urlencode({"keyword":query,"source":"web_explore_feed"})
    script="""(()=>{
      const limit=%s;
      const gather=()=>{
        const found=[],seen=new Set();
        for(const a of document.querySelectorAll('section a[href*="/search_result/"], section a[href*="/explore/"]')){
          const section=a.closest('section');if(!section)continue;
          const u=new URL(a.getAttribute('href'),location.origin);
          if(u.hostname!=='www.xiaohongshu.com' || !u.searchParams.get('xsec_token') || !/\\/(?:explore|search_result)\\/[a-f0-9]{24}/i.test(u.pathname))continue;
          const title=(section.querySelector('.title, .note-title, .footer .title span')?.textContent || a.textContent || '').trim();
          if(!title || seen.has(u.pathname))continue;
          seen.add(u.pathname);
          found.push({title,author:(section.querySelector('a.author .name,.author-name,.nick-name,.name')?.textContent || '').trim(),url:u.href,type:'note'});
          if(found.length>=limit)break;
        }
        return found;
      };
      const stop=()=>{
        const text=document.body?.innerText || '';
        if(/验证码|安全验证|安全限制|访问链接异常/.test(text))return 'verification_required';
        if(/访问过于频繁|请求过于频繁/.test(text))return 'rate_limited';
        if(/登录后查看搜索结果/.test(text))return 'login_required';
        return '';
      };
      const blocked=stop();if(blocked)return {failure:blocked,items:[]};
      const items=gather(), text=document.body?.innerText || '';
      return {failure:stop() || (items.length?'':/没有找到相关|暂无搜索结果|未找到相关笔记/.test(text)?'search_empty_confirmed':document.querySelector('section.note-item')?'adapter_incompatible':'content_loading_timeout'),items};
    })()""" % limit
    recovery=None
    for attempt in (1,2):
        try:
            candidate=ReadPage(runner,target,cancel,"xhs-search")
            candidate.blank_navigation_grace=4 if attempt==1 else None
            candidate.foreground=attempt==2
            with candidate as page:
                deadline=time.monotonic()+20
                while True:
                    data=page.evaluate(script,max(.1,min(12,deadline-time.monotonic())))
                    if not isinstance(data,dict) or data.get("failure") not in {"content_loading_timeout","adapter_incompatible"}:break
                    if time.monotonic()>=deadline:
                        error=ReadError(data["failure"],"搜索卡片未在预算内取得可用标题与访问链接。")
                        error.stage="search_cards";raise error
                    _pause(cancel,min(.5,max(0,deadline-time.monotonic())))
                navigation=getattr(page,"navigation",None)
            break
        except ReadError as error:
            # A new target can recover an unrendered page. Never reload a
            # failed target indefinitely, replay account actions, or retry a
            # verification/rate-limit/access-denied response.
            if attempt==2 or error.code not in {"timeout","navigation_not_started","navigation_loading_timeout","content_loading_timeout","navigation_rejected"}:
                raise
            recovery={"attempts":2,"previous_status":error.code,"previous_stage":getattr(error,"stage",None),"method":"fresh_owned_page"}
            _pause(cancel,2.5)
    if not isinstance(data,dict):raise ReadError("adapter_incompatible","搜索结果结构变化。")
    if data.get("failure"):
        messages={"search_empty_confirmed":"平台明确显示本次搜索没有结果。", "adapter_incompatible":"搜索页有卡片，但未取得可用的标题和访问链接。", "content_loading_timeout":"搜索页未在预算内加载可读结果，不能判断没有相关内容。"}
        raise ReadError(data["failure"],messages.get(data["failure"],"平台要求登录或验证，搜索已停止。"))
    results=[]
    for row in data.get("items",[])[:limit]:
        target=validate_url("xiaohongshu",row["url"])
        note_id=urlsplit(target).path.rstrip("/").split("/")[-1]
        try:row["published_at"]=(datetime.fromtimestamp(int(note_id[:8],16),timezone.utc)+timedelta(hours=8)).date().isoformat()
        except (ValueError,OSError,OverflowError):pass
        item=search_item("xiaohongshu",row,query,"opencli")
        if recovery:item["recovery"]=recovery
        if isinstance(navigation,dict):item["navigation"]=navigation
        results.append(item)
    return results
