"""Bounded comments with stable root/reply relationships and explicit coverage."""
import json
from urllib.parse import urlsplit
from .article_text import html_text
from .browser_page import ReadPage
from .model import ReadError, digest, scrub, validate_url
from .platform_reads import FETCH_JS, check_failure

def normalize_rows(raw,site,limit,replies_limit):
    result=[];seen=set();top=0;counts={};current=None
    for row in raw:
        if not isinstance(row,dict):continue
        text=row.get("text") or row.get("content") or ""
        if not text:continue
        text=html_text(text) if row.get("format")=="html" else str(text)
        is_reply=bool(row.get("is_reply") or row.get("parent_id") or row.get("reply_rank",0))
        if not is_reply:
            if top>=limit:break
            top+=1;current=str(row.get("id") or "local_"+digest(str(row.get("author"))+"|"+text+"|"+str(row.get("time")))[:20]);parent=None
        else:
            parent=str(row.get("parent_id") or current or "")
            if not parent or counts.get(parent,0)>=replies_limit:continue
            counts[parent]=counts.get(parent,0)+1
        ident=str(row.get("id") or "local_"+digest(str(row.get("author"))+"|"+text+"|"+str(row.get("time"))+"|"+str(parent))[:20])
        if ident in seen:continue
        seen.add(ident)
        result.append({"id":ident,"id_source":"local_content_hash" if ident.startswith("local_") else "platform","parent_id":parent,
            "author":str(row.get("author") or "作者未提供"),"text":text,"is_reply":is_reply,"reply_to":row.get("reply_to") or None,
            "likes":row.get("likes"),"published_at_raw":row.get("time") or row.get("created_at") or None,
            "depth":None if is_reply else 0,"source":site,"content_kind":row.get("content_kind","text")})
    return scrub(result)

def finish(raw,site,limit,replies_limit):
    check_failure(raw)
    items=normalize_rows(raw.get("items",[]),site,limit,replies_limit)
    roots=sum(not i["is_reply"] for i in items)
    return {"items":items,"coverage":{"requested_top_level":limit,"replies_per_root_limit":replies_limit,
        "returned_top_level":roots,"returned_replies":len(items)-roots,"returned_rows":len(items),"non_text_rows":sum(i.get("content_kind")=="non_text" for i in items),
        "all_comments":False,"all_replies":False,"reply_relationship":"root_thread_and_reply_to_author; deeper_nesting_unverified","root_pages":raw.get("root_pages",1),
        "reply_requests":raw.get("reply_requests",0),"root_end_reached":raw.get("is_end"),
        "stop_reason":raw.get("stop_reason","bounded_sample"),"backend":raw.get("backend"),
        "reply_roots_not_expanded":raw.get("reply_roots_not_expanded",0)},
        "errors":raw.get("errors",[]),"notice":"本次最多读取 %s 条首层评论、每条最多 %s 条回复；只覆盖此次取得的范围，不代表全部评论。%s" % (limit,replies_limit,"此次未取得可见评论，不能据此判断没有评论。" if not items else "")}

def read_comments(runner,site,target,limit,replies_limit,cancel):
    target=validate_url(site,target)
    if site=="xiaohongshu":return xhs_comments(runner,target,limit,replies_limit,cancel)
    p=urlsplit(target);ident=p.path.rstrip("/").split("/")[-1]
    if site=="zhihu" and "/answer/" not in p.path and not p.path.startswith("/p/"):
        raise ReadError("unsupported","知乎问题页包含多条回答，请先选择具体回答或文章再读取评论。")
    kind="articles" if p.path.startswith("/p/") else "answers"
    config={"id":ident,"site":site,"kind":kind,"limit":limit,"replies":replies_limit}
    script="(async()=>{"+FETCH_JS+"const cfg="+json.dumps(config)+";"+"""
      let items=[], errors=[], reply_requests=0, reply_roots_not_expanded=0;
      const author=c=>c.author?.name || c.author?.member?.name || c.user?.screen_name || '';
      const row=(c,parent=null)=>({id:String(c.idstr || c.id || ''),parent_id:parent,
        author:author(c),text:c.content || c.text_raw || c.text || '',format:c.text_raw?'plain':'html',
        likes:c.like_count ?? c.vote_count ?? 0,time:c.created_at || c.created_time || '',
        is_reply:!!parent,reply_to:c.reply_to_author?.name || c.reply_comment?.user?.screen_name || ''});
      let data, roots, end;
      if(cfg.site==='weibo'){
        const status=await get('/ajax/statuses/show?id='+encodeURIComponent(cfg.id));
        if(status.failure)return status;
        if(!status.idstr)return {failure:'adapter_incompatible'};
        await pause(2500);
        data=await get('/ajax/statuses/buildComments?flow=0&is_reload=1&id='+encodeURIComponent(status.idstr)+'&is_show_bulletin=2&is_mix=0&count='+cfg.limit);
        if(data.failure)return data;
        if(!Array.isArray(data.data))return {failure:'adapter_incompatible'};
        roots=data.data.slice(0,cfg.limit);end=data.max_id===0;
        for(const root of roots){
          const rootRow=row(root);items.push(rootRow);
          let children=Array.isArray(root.comments)?root.comments:[];
          if(cfg.replies>0 && Number(root.total_number || root.comments_count || 0)>children.length){
            if(reply_requests<3 && rootRow.id){
              await pause(2500);reply_requests++;
              const child=await get('/ajax/statuses/buildComments?flow=1&is_reload=1&id='+encodeURIComponent(rootRow.id)+'&is_show_bulletin=2&is_mix=1&count='+cfg.replies);
              if(child.failure){errors.push({status:child.failure,message:'部分微博回复未取得。'});if(['rate_limited','verification_required','login_required','access_denied','timeout'].includes(child.failure))break;}
              else if(Array.isArray(child.data))children=child.data;
            }else reply_roots_not_expanded++;
          }
          for(const child of children.slice(0,cfg.replies))items.push(row(child,rootRow.id));
        }
      }else{
        // Open a www.zhihu.com page so both article and answer comment requests are same-origin.
        const path='/api/v4/comment_v5/'+cfg.kind+'/'+cfg.id+'/root_comment?order_by=score&limit='+cfg.limit+'&offset=';
        data=await get(path);
        if(data.failure)return data;
        if(!Array.isArray(data.data))return {failure:'adapter_incompatible'};
        roots=data.data.slice(0,cfg.limit);end=!!data.paging?.is_end;
        for(const root of roots){
          const rootRow=row(root);items.push(rootRow);
          let children=Array.isArray(root.child_comments)?root.child_comments:[];
          if(cfg.replies>0 && Number(root.child_comment_count || root.child_comment_total_count || 0)>children.length){
            if(reply_requests<3 && rootRow.id){
              await pause(2500);reply_requests++;
              const child=await get('/api/v4/comment_v5/comment/'+encodeURIComponent(rootRow.id)+'/child_comment?limit='+cfg.replies+'&offset=');
              if(child.failure){errors.push({status:child.failure,message:'部分知乎回复未取得。'});if(['rate_limited','verification_required','login_required','access_denied','timeout'].includes(child.failure))break;}
              else if(Array.isArray(child.data))children=child.data;
            }else reply_roots_not_expanded++;
          }
          for(const child of children.slice(0,cfg.replies))items.push(row(child,rootRow.id));
        }
      }
      return {items,errors,is_end:end,root_pages:1,reply_requests,reply_roots_not_expanded,
        stop_reason:errors.length?'reply_read_failed':end?'root_page_end':'root_sample_limit',backend:'page_context_comments'};
    })()"""
    open_target=target
    if site=="zhihu" and kind=="articles":open_target="https://www.zhihu.com"
    with ReadPage(runner,open_target,cancel,"comments") as page:
        raw=page.evaluate(script,55)
    return finish(raw,site,limit,replies_limit)

def xhs_comments(runner,target,limit,replies_limit,cancel):
    script="(async()=>{const limit="+str(limit)+";const replies="+str(replies_limit)+";"+"""
      const pause=ms=>new Promise(r=>setTimeout(r,ms));
      const body=document.body?.innerText || '';
      if(/安全限制|访问链接异常|验证码|安全验证/.test(body))return {failure:'verification_required'};
      if(/登录后查看|请登录/.test(body))return {failure:'login_required'};
      const scroller=document.querySelector('.note-scroller');
      let rounds=0;
      if(scroller)for(let i=0;i<3;i++){
        const before=document.querySelectorAll('.parent-comment').length;
        scroller.scrollTo(0,scroller.scrollHeight);rounds++;await pause(1100);
        const after=document.querySelectorAll('.parent-comment').length;
        if(after>=limit || (after<=before && after>0))break;
      }
      const clean=e=>(e?.innerText || e?.textContent || '').trim();
      const stable=async s=>[...new Uint8Array(await crypto.subtle.digest('SHA-256',new TextEncoder().encode(s)))].map(x=>x.toString(16).padStart(2,'0')).join('').slice(0,20);
      const deadline=Date.now()+10000;
      while(!document.querySelector('.parent-comment .content, .parent-comment .note-text') && Date.now()<deadline)await pause(750);
      const parents=[...document.querySelectorAll('.parent-comment')].slice(0,limit);
      const items=[];let clicks=0;
      for(const parent of parents){
        const item=parent.querySelector('.comment-item');if(!item)continue;
        const root={id:(item.id || parent.id || '').replace(/^comment-/,''),
          author:clean(item.querySelector('.author-wrapper .name, .user-name')),
          text:clean(item.querySelector('.content, .note-text')),time:clean(item.querySelector('.date, .time')),is_reply:false};
        if(!root.text){root.text='（此评论没有可见文字，图片或表情未识别）';root.content_kind='non_text';}
        // A content hash is assigned in Python when the DOM has no platform id.
        const parentKey=root.id || ('local_'+await stable(root.author+'|'+root.text+'|'+root.time));
        root.id=root.id || parentKey;
        items.push(root);
        if(replies>0 && clicks<3){
          const candidates=[...parent.querySelectorAll('span,button,div')].filter(el=>{
            const t=clean(el);
            return t.length<30 && /^(展开.*回复|查看.*回复|更多回复|共\\d+条回复)$/.test(t) &&
              ![...el.children].some(c=>clean(c)===t);
          });
          if(candidates.length){candidates[0].click();clicks++;await pause(700);}
        }
        for(const child of [...parent.querySelectorAll('.reply-container .comment-item-sub, .sub-comment-list .comment-item')].slice(0,replies)){
          const text=clean(child.querySelector('.content, .note-text'));if(!text)continue;
          items.push({id:(child.id || '').replace(/^comment-/,''),parent_id:root.id,is_reply:true,
            author:clean(child.querySelector('.name, .user-name')),text,
            time:clean(child.querySelector('.date, .time')),reply_to:text.match(/^回复\s+([^:：]+)\s*[:：]/)?.[1]?.trim() || root.author});
        }
      }
      return {items,root_pages:rounds,reply_requests:clicks,is_end:null,stop_reason:'bounded_visible_dom',backend:'note_dom_comments'};
    })()"""
    with ReadPage(runner,target,cancel,"xhs-comments") as page:
        page.wait("#detail-title, #detail-desc, .note-container")
        raw=page.evaluate(script,30)
    return finish(raw,"xiaohongshu",limit,replies_limit)
