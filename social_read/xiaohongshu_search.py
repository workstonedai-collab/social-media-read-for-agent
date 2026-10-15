"""Search waits for usable cards, not skeleton placeholders."""
import json
from datetime import datetime,timezone,timedelta
from urllib.parse import urlencode,urlsplit
from .browser_page import ReadPage
from .model import ReadError,search_item,validate_url

def search_notes(runner,query,limit,cancel):
    target="https://www.xiaohongshu.com/search_result?"+urlencode({"keyword":query,"source":"web_explore_feed"})
    script="""(async()=>{
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
        if(/登录后查看搜索结果/.test(text))return 'login_required';
        return '';
      };
      const ready=()=>gather().length>=limit || stop();
      const deadline=Date.now()+15000;
      while(!ready() && Date.now()<deadline)await new Promise(resolve=>setTimeout(resolve,750));
      const items=gather(), text=document.body?.innerText || '';
      return {failure:stop() || (items.length?'':/没有找到相关|暂无搜索结果|未找到相关笔记/.test(text)?'search_empty_confirmed':document.querySelector('section.note-item')?'adapter_incompatible':'content_loading_timeout'),items};
    })()""" % limit
    with ReadPage(runner,target,cancel,"xhs-search") as page:
        page.wait('section a[href*="/search_result/"], section a[href*="/explore/"], .login-container, .search-empty', 12)
        data=page.evaluate(script,22)
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
        results.append(search_item("xiaohongshu",row,query,"opencli"))
    return results
