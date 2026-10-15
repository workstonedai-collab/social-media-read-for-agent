"""Read normal logged-in search cards instead of conflating API 403 with logout."""
import json
from urllib.parse import urlencode
from .browser_page import ReadPage
from .model import ReadError, search_item


def search_zhihu(runner, query, limit, cancel):
    target="https://www.zhihu.com/search?"+urlencode({"q":query,"type":"content"})
    script="(async()=>{const limit="+json.dumps(limit)+";"+r"""
      const clean=e=>(e?.innerText || e?.textContent || '').trim();
      const gather=()=>{
        const rows=[],seen=new Set();
        for(const a of document.querySelectorAll('.SearchResult-Card .ContentItem-title a, .List-item .ContentItem-title a')){
          const u=new URL(a.getAttribute('href'),location.href);
          if(!['www.zhihu.com','zhuanlan.zhihu.com'].includes(u.hostname) || !/^\/(question\/\d+(\/answer\/\d+)?|p\/\d+)\/?$/.test(u.pathname))continue;
          if(seen.has(u.pathname) || !clean(a))continue;seen.add(u.pathname);
          const card=a.closest('.SearchResult-Card, .List-item');
          rows.push({title:clean(a),url:u.href,author:clean(card?.querySelector('.AuthorInfo-name')),
            summary:clean(card?.querySelector('.RichContent-inner')).slice(0,500),
            type:u.pathname.includes('/answer/')?'answer':u.pathname.startsWith('/p/')?'article':'question',rank:rows.length+1});
          if(rows.length>=limit)break;
        }
        return rows;
      };
      const deadline=Date.now()+15000;
      while(Date.now()<deadline){
        const items=gather();if(items.length>=limit)return {items};
        const text=clean(document.body);
        if(!items.length && /没有找到相关|暂无搜索结果/.test(text))return {failure:'search_empty_confirmed'};
        if(!items.length && (/unhuman|account\/signin/.test(location.pathname) || /登录后查看|请登录后/.test(text)))return {failure:'login_required'};
        if(!items.length && /安全验证|异常请求|访问过于频繁/.test(text))return {failure:'verification_required'};
        await new Promise(r=>setTimeout(r,600));
      }
      const items=gather();return items.length?{items}:{failure:'content_loading_timeout'};
    })()"""
    with ReadPage(runner,target,cancel,'zhihu-search') as page:
        raw=page.evaluate(script,22)
    if not isinstance(raw,dict):raise ReadError('adapter_incompatible','知乎搜索页面结构发生变化。')
    if raw.get('failure'):
        messages={'search_empty_confirmed':'平台明确显示本次搜索没有结果。','login_required':'知乎搜索页明确要求登录。','verification_required':'知乎搜索页要求验证或限制访问，读取停止。','content_loading_timeout':'知乎搜索页没有在预算内出现可读结果。'}
        raise ReadError(raw['failure'],messages.get(raw['failure'],'知乎搜索没有完成。'))
    return [search_item('zhihu',row,query,'opencli-owned-search-dom') for row in raw.get('items',[])[:limit]]
