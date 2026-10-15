"""Search waits for usable cards, not skeleton placeholders."""
import json
import time
from datetime import datetime,timezone,timedelta
from urllib.parse import urlencode,urlsplit
from .browser_page import ReadPage, _pause
from .model import ReadError,search_item,validate_url

def search_notes(runner,query,limit,cancel):
    target="https://www.xiaohongshu.com/search_result?"+urlencode({"keyword":query,"source":"web_explore_feed"})
    script=r"""(()=>{
      const limit=%s, expectedQuery=%s;
      const gather=()=>{
        const found=[],seen=new Set();
        for(const a of document.querySelectorAll('section a[href*="/search_result/"], section a[href*="/explore/"]')){
          const section=a.closest('section');if(!section)continue;
          const u=new URL(a.getAttribute('href'),location.origin);
          if(u.hostname!=='www.xiaohongshu.com' || !u.searchParams.get('xsec_token') || !/\/(?:explore|search_result)\/[a-f0-9]{24}/i.test(u.pathname))continue;
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
      const current=new URL(location.href);
      if(current.origin!=='https://www.xiaohongshu.com')return {failure:'source_mismatch',items:[]};
      if(current.pathname.replace(/\/$/,'')!=='/search_result' || current.searchParams.get('keyword')!==expectedQuery)
        return {failure:'content_loading_timeout',stage:'search_navigation',items:[]};
      const items=gather(), text=document.body?.innerText || '';
      return {failure:stop() || (items.length?'':/没有找到相关|暂无搜索结果|未找到相关笔记/.test(text)?'search_empty_confirmed':document.querySelector('section.note-item')?'adapter_incompatible':'content_loading_timeout'),items};
    })()""" % (limit,json.dumps(query))
    started=time.monotonic()
    # A cold tabs.new(search_url) can leave a dispatched main-document
    # request pending in an empty document. Establish the normal site document
    # first and navigate within it once, with the browser supplying its normal
    # origin/referrer context. Do not replay a search on a second target.
    with ReadPage(runner,"https://www.xiaohongshu.com/explore",cancel,"xhs-search") as page:
        page.navigate_same_origin(target)
        deadline=time.monotonic()+25
        while True:
            data=page.evaluate(script,max(.1,min(12,deadline-time.monotonic())))
            if not isinstance(data,dict) or data.get("failure") not in {"content_loading_timeout","adapter_incompatible"}:break
            if time.monotonic()>=deadline:
                error=ReadError(data["failure"],"搜索页面未在预算内取得匹配关键词的标题与访问链接。")
                error.stage=data.get("stage","search_cards");raise error
            _pause(cancel,min(.5,max(0,deadline-time.monotonic())))
        navigation={"status":"ready","seconds":round(time.monotonic()-started,3),"search_entry":"same_origin_navigation","page_attempts":1}
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
        if isinstance(navigation,dict):item["navigation"]=navigation
        results.append(item)
    return results
