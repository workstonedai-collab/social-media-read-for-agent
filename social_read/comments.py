"""Bounded comments with stable root/reply relationships and explicit coverage."""
# SPDX-License-Identifier: Apache-2.0
import json
import re
import time
from urllib.parse import urlsplit

# Comment loading/end markers adapted from xpzouying/xiaohongshu-mcp
# (Apache-2.0), commit 7797fd3. See THIRD_PARTY_NOTICES.md.
from .article_text import html_text
from .browser_page import ReadPage, _pause
from .model import ReadError, digest, scrub, validate_url
from .platform_reads import FETCH_JS, check_failure

def normalize_rows(raw, site, limit, replies_limit):
    result, seen, roots, counts = [], set(), set(), {}
    current = None
    for row in raw:
        if not isinstance(row, dict):
            continue
        text = row.get("text") or row.get("content") or ""
        if not text:
            continue
        text = html_text(text) if row.get("format") == "html" else str(text)
        is_reply = bool(row.get("is_reply") or row.get("parent_id") or row.get("reply_rank", 0))
        parent = str(row.get("parent_id") or current or "") if is_reply else None
        ident = str(row.get("id") or "local_" + digest(str(row.get("author")) + "|" + text + "|" + str(row.get("time")) + ("|" + str(parent) if is_reply else ""))[:20])
        if ident in seen:
            continue
        if is_reply:
            if parent not in roots or counts.get(parent, 0) >= replies_limit:
                continue
            counts[parent] = counts.get(parent, 0) + 1
        else:
            if len(roots) >= limit:
                continue
            roots.add(ident)
            current = ident
        seen.add(ident)
        result.append({"id": ident, "id_source": "local_content_hash" if ident.startswith("local_") else "platform", "parent_id": parent,
            "author": str(row.get("author") or "作者未提供"), "text": text, "is_reply": is_reply, "reply_to": row.get("reply_to") or None,
            "likes": row.get("likes"), "published_at_raw": row.get("time") or row.get("created_at") or None,
            "depth": None if is_reply else 0, "source": site, "content_kind": "non_text" if re.fullmatch(r"图片评论(?:\s+https?://\S+)?",text.strip()) else row.get("content_kind", "text")})
    return scrub(result)

def finish(raw,site,limit,replies_limit):
    check_failure(raw)
    items=normalize_rows(raw.get("items",[]),site,limit,replies_limit)
    roots=sum(not i["is_reply"] for i in items)
    errors=list(raw.get("errors",[]))
    if raw.get("is_end") is not True and raw.get("stop_reason") in {"loading_stalled", "time_budget"} and roots<limit:
        errors.append({"status":"comments_loading_incomplete", "message":"评论尚未完成加载，保留本次已读内容；不能判断平台没有评论。"})
    return {"items":items,"coverage":{"requested_top_level":limit,"replies_per_root_limit":replies_limit,
        "returned_top_level":roots,"returned_replies":len(items)-roots,"returned_rows":len(items),"non_text_rows":sum(i.get("content_kind")=="non_text" for i in items),
        "all_comments":False,"all_replies":False,"reply_relationship":"root_thread_and_reply_to_author; deeper_nesting_unverified","root_pages":raw.get("root_pages",1),
        "reply_requests":raw.get("reply_requests",0),"root_end_reached":raw.get("is_end"),
        "stop_reason":raw.get("stop_reason","bounded_sample"),"backend":raw.get("backend"),
        "reply_roots_not_expanded":raw.get("reply_roots_not_expanded",0)},
        "_continuation":raw.get("next_state"),"errors":errors,"notice":"本次最多读取 %s 条首层评论、每条最多 %s 条回复；只覆盖此次取得的范围，不代表全部评论。%s" % (limit,replies_limit,"此次未取得可见评论，不能据此判断没有评论。" if not items else "")}

def read_comments(runner,site,target,limit,replies_limit,cancel,cursor=None):
    target=validate_url(site,target)
    if site=="xiaohongshu":return xhs_comments(runner,target,limit,replies_limit,cancel,cursor)
    p=urlsplit(target);ident=p.path.rstrip("/").split("/")[-1]
    if site=="zhihu" and "/answer/" not in p.path and not p.path.startswith("/p/"):
        raise ReadError("unsupported","知乎问题页包含多条回答，请先选择具体回答或文章再读取评论。")
    kind="articles" if p.path.startswith("/p/") else "answers"
    config={"id":ident,"site":site,"kind":kind,"limit":limit,"replies":replies_limit,"cursor":cursor or {}}
    script="(async()=>{"+FETCH_JS+"const cfg="+json.dumps(config)+";"+"""
      let items=[], errors=[], reply_requests=0, reply_roots_not_expanded=0;
      const author=c=>c.author?.name || c.author?.member?.name || c.user?.screen_name || '';
      const row=(c,parent=null)=>({id:String(c.idstr || c.id || ''),parent_id:parent,
        author:author(c),text:c.content || c.text_raw || c.text || '',format:c.text_raw?'plain':'html',
        likes:c.like_count ?? c.vote_count ?? 0,time:c.created_at || c.created_time || '',
        is_reply:!!parent,reply_to:c.reply_to_author?.name || c.reply_comment?.user?.screen_name || ''});
      let data, roots, end, nextOffset='', buffer=[];
      let replyBlocked=false;
      const seen=new Set(cfg.cursor.seen || []);
      const buffered=cfg.cursor.buffer || [];
      const offset=cfg.cursor.offset || '';

      if(cfg.site==='weibo'){
        const status=await get('/ajax/statuses/show?id='+encodeURIComponent(cfg.id));
        if(status.failure)return status;
        if(!status.idstr)return {failure:'adapter_incompatible'};
        await pause(2500);
        data=buffered.length ? {data:buffered,max_id:offset,max_id_type:cfg.cursor.max_id_type} : await get('/ajax/statuses/buildComments?flow=0&is_reload=1&id='+encodeURIComponent(status.idstr)+'&is_show_bulletin=2&is_mix=0&count='+cfg.limit+(offset?'&max_id='+encodeURIComponent(offset)+'&max_id_type='+encodeURIComponent(cfg.cursor.max_id_type || '0'):''));
        if(data.failure)return data;
        if(!Array.isArray(data.data))return {failure:'adapter_incompatible'};
        buffer=data.data.filter(c=>!seen.has(String(c.idstr || c.id || '')));roots=buffer.splice(0,cfg.limit);nextOffset=String(data.max_id ?? '');end=(!nextOffset || nextOffset==='0') && !buffer.length;
        for(const root of roots){
          const rootRow=row(root);items.push(rootRow);seen.add(rootRow.id);
          let children=Array.isArray(root.comments)?root.comments:[];
          if(!replyBlocked && cfg.replies>0 && Number(root.total_number || root.comments_count || 0)>children.length){
            if(reply_requests<3 && rootRow.id){
              await pause(2500);reply_requests++;
              const child=await get('/ajax/statuses/buildComments?flow=1&is_reload=1&id='+encodeURIComponent(rootRow.id)+'&is_show_bulletin=2&is_mix=1&count='+cfg.replies);
              if(child.failure){errors.push({status:child.failure,message:'部分微博回复未取得。'});if(['rate_limited','verification_required','login_required','access_denied','timeout'].includes(child.failure))replyBlocked=true;}
              else if(Array.isArray(child.data))children=child.data;
            }else reply_roots_not_expanded++;
          }
          for(const child of children.slice(0,cfg.replies))items.push(row(child,rootRow.id));
        }
      }else{
        // Open a www.zhihu.com page so both article and answer comment requests are same-origin.
        const path='/api/v4/comment_v5/'+cfg.kind+'/'+cfg.id+'/root_comment?order_by=score&limit='+cfg.limit+'&offset='+encodeURIComponent(offset);
        data=buffered.length ? {data:buffered,paging:{is_end:cfg.cursor.is_end,next:cfg.cursor.next}} : await get(path);
        if(data.failure)return data;
        if(!Array.isArray(data.data))return {failure:'adapter_incompatible'};
        buffer=data.data.filter(c=>!seen.has(String(c.id || '')));roots=buffer.splice(0,cfg.limit);
        if(data.paging?.next){
          const next=new URL(data.paging.next,location.href);
          if(next.origin!==location.origin || next.pathname!==path.split('?')[0])return {failure:'adapter_incompatible'};
          nextOffset=next.searchParams.get('offset') || '';
        }
        end=!!data.paging?.is_end && !buffer.length;
        for(const root of roots){
          const rootRow=row(root);items.push(rootRow);seen.add(rootRow.id);
          let children=Array.isArray(root.child_comments)?root.child_comments:[];
          if(!replyBlocked && cfg.replies>0 && Number(root.child_comment_count || root.child_comment_total_count || 0)>children.length){
            if(reply_requests<3 && rootRow.id){
              await pause(2500);reply_requests++;
              const child=await get('/api/v4/comment_v5/comment/'+encodeURIComponent(rootRow.id)+'/child_comment?limit='+cfg.replies+'&offset=');
              if(child.failure){errors.push({status:child.failure,message:'部分知乎回复未取得。'});if(['rate_limited','verification_required','login_required','access_denied','timeout'].includes(child.failure))replyBlocked=true;}
              else if(Array.isArray(child.data))children=child.data;
            }else reply_roots_not_expanded++;
          }
          for(const child of children.slice(0,cfg.replies))items.push(row(child,rootRow.id));
        }
      }
      const next_state=!end && (buffer.length || nextOffset) && seen.size<2000 ? {
        offset:nextOffset,buffer,seen:[...seen],max_id_type:data.max_id_type || '0',
        is_end:!!data.paging?.is_end,next:data.paging?.next || ''}:null;
      return {items,errors,next_state,is_end:end,root_pages:1,reply_requests,reply_roots_not_expanded,
        stop_reason:errors.length?'reply_read_failed':end?'root_page_end':'root_sample_limit',backend:'page_context_comments'};
    })()"""
    open_target=target
    if site=="zhihu" and kind=="articles":open_target="https://www.zhihu.com"
    with ReadPage(runner,open_target,cancel,"comments") as page:
        raw=page.evaluate(script,55)
    return finish(raw,site,limit,replies_limit)

def _xhs_snapshot(page, note_id, seen, replies_limit, action=None, root_id=None, timeout=10):
    """A bounded DOM snapshot or one visibility action; no page-side timers."""
    cfg={"note_id":note_id,"seen":list(seen),"replies":replies_limit,"action":action,"root_id":root_id}
    script="(async()=>{const cfg="+json.dumps(cfg)+";"+r"""
      const current=location.pathname.match(/\/(?:explore|search_result|discovery\/item)\/([a-f0-9]{24})/i);
      if(location.origin!=='https://www.xiaohongshu.com' || !current || current[1]!==cfg.note_id)return {failure:'source_mismatch'};
      const clean=e=>(e?.innerText || e?.textContent || '').trim();
      const body=document.body?.innerText || '';
      if(/安全限制|访问链接异常|验证码|安全验证/.test(body))return {failure:'verification_required'};
      if(/访问过于频繁|请求过于频繁/.test(body))return {failure:'rate_limited'};
      if(/登录后查看|请登录/.test(body))return {failure:'login_required'};
      const stable=async s=>[...new Uint8Array(await crypto.subtle.digest('SHA-256',new TextEncoder().encode(s)))].map(x=>x.toString(16).padStart(2,'0')).join('').slice(0,20);
      const seen=new Set(cfg.seen), roots=[];let available=0,clicked=false;
      for(const parent of document.querySelectorAll('.parent-comment')){
        const item=parent.querySelector('.comment-item');if(!item)continue;
        const root={id:(item.id || parent.id || '').replace(/^comment-/,''),
          author:clean(item.querySelector('.author-wrapper .name, .user-name')),
          text:clean(item.querySelector('.content, .note-text')),time:clean(item.querySelector('.date, .time')),is_reply:false};
        if(!root.text){root.text='（此评论没有可见文字，图片或表情未识别）';root.content_kind='non_text';}
        root.id=root.id || ('local_'+await stable(root.author+'|'+root.text+'|'+root.time));
        if(seen.has(root.id))continue;
        available++;
        const children=[];
        for(const child of [...parent.querySelectorAll('.reply-container .comment-item-sub, .sub-comment-list .comment-item')].slice(0,cfg.replies)){
          let text=clean(child.querySelector('.content, .note-text'));const nontext=!text;
          if(nontext)text='（此回复没有可见文字，图片或表情未识别）';
          children.push({id:(child.id || '').replace(/^comment-/,''),parent_id:root.id,is_reply:true,
            author:clean(child.querySelector('.name, .user-name')),text,content_kind:nontext?'non_text':'text',
            time:clean(child.querySelector('.date, .time')),reply_to:text.match(/^回复\s+([^:：]+)\s*[:：]/)?.[1]?.trim() || root.author});
        }
        const expand=[...parent.querySelectorAll('span,button,div')].find(el=>{
          const text=clean(el);
          return text.length<30 && /^(展开.*回复|查看.*回复|更多回复|共\d+条回复)$/.test(text) && ![...el.children].some(c=>clean(c)===text);
        });
        if(cfg.action==='expand' && root.id===cfg.root_id && children.length<cfg.replies && expand){expand.click();clicked=true;}
        if(roots.length<21)roots.push({root,children,expandable:!!expand});
      }
      if(cfg.action==='scroll'){
        const scroller=document.querySelector('.note-scroller');
        if(scroller)scroller.scrollTo(0,scroller.scrollHeight);
        else document.querySelector('.comments-container')?.scrollIntoView({block:'end'});
      }
      const empty=/这是一片荒地|暂无评论|还没有评论/.test(clean(document.querySelector('.no-comments-text')));
      const ended=empty || /THE\s*END|没有更多评论|没有更多了/i.test(clean(document.querySelector('.end-container')));
      return {roots,available,count:document.querySelectorAll('.parent-comment').length,empty,ended,clicked};
    })()"""
    raw=page.evaluate(script,timeout)
    if not isinstance(raw,dict):raise ReadError("adapter_incompatible","评论快照格式发生变化。")
    if raw.get("failure")=="source_mismatch":raise ReadError("source_mismatch","评论页与请求的笔记不匹配，已停止。")
    check_failure(raw)
    if not isinstance(raw.get("roots"),list):raise ReadError("adapter_incompatible","评论快照没有返回首层结构。")
    return raw


def xhs_comments(runner, target, limit, replies_limit, cancel, cursor=None):
    note_id=urlsplit(target).path.rstrip('/').split('/')[-1]
    seen=set((cursor or {}).get("seen",[]))
    selected={};rounds=0;stagnant=0;previous=-1;clicks=0;not_expanded=0
    errors=[];raw={};stop_reason="time_budget"
    started=time.monotonic()
    # Caption/title nodes are optional on image/video notes. Wait for the
    # comment widget itself, then validate the note ID on every snapshot.
    with ReadPage(runner,"https://www.xiaohongshu.com/explore",cancel,"xhs-comments") as page:
        page.navigate_same_origin(target)
        page.wait(".comments-container, .parent-comment, .no-comments-text",20,phase="comments_ready")
        deadline=time.monotonic()+22
        def snapshot(action=None,root_id=None):
            remaining=deadline-time.monotonic()
            if remaining<=0:raise ReadError("timeout","评论读取预算已耗尽，已保留观察到的评论。")
            return _xhs_snapshot(page,note_id,frozenset(seen),replies_limit,action,root_id,timeout=min(10,remaining))
        try:
            while time.monotonic()<deadline:
                if cancel and cancel.is_set():raise ReadError("cancelled","任务已取消。")
                raw=snapshot()
                for entry in raw["roots"]:
                    root=entry.get("root",{})
                    if root.get("id") and (root["id"] in selected or len(selected)<limit):selected[root["id"]]=entry
                if len(selected)>=limit:
                    stop_reason="root_sample_limit";break
                if raw.get("ended"):
                    stop_reason="visible_empty" if raw.get("empty") else "visible_end";break
                count=raw.get("count",0);stagnant=stagnant+1 if count==previous else 0;previous=count
                if stagnant>=3 and count>0:
                    stop_reason="loading_stalled";break
                if rounds>=12:break
                snapshot("scroll")
                rounds+=1;_pause(cancel,min(.8,max(0,deadline-time.monotonic())))
            # Only expand when fewer than the requested replies are visible.
            # Each root gets at most one click; waits live on the cancellable
            # host, where background-tab timer throttling cannot stretch them.
            for ident,entry in list(selected.items()):
                if replies_limit<=len(entry.get("children",[])) or not entry.get("expandable"):continue
                if clicks>=3 or time.monotonic()>=deadline:
                    not_expanded+=1;continue
                changed=snapshot("expand",ident)
                if changed.get("clicked"):
                    clicks+=1
                    _pause(cancel,min(.7,max(0,deadline-time.monotonic())))
                    fresh=snapshot()
                    for updated in fresh["roots"]:
                        if updated.get("root",{}).get("id")==ident:selected[ident]=updated
        except ReadError as error:
            if error.code=="cancelled" or not selected:raise
            # Preserve already observed rows; no click, scroll or navigation
            # is replayed after timeout, a gate, or an unknown command outcome.
            errors.append({**error.result(),"stage":"comments_walk"})
            stop_reason="read_interrupted"
    items=[]
    for ident,entry in selected.items():
        items.append(entry["root"]);items.extend(entry.get("children",[])[:replies_limit]);seen.add(ident)
    exhausted=bool(raw.get("ended")) and raw.get("available",0)<=len(selected)
    result=finish({"items":items,"errors":errors,"root_pages":rounds,"reply_requests":clicks,
        "reply_roots_not_expanded":not_expanded,"is_end":True if exhausted else None,
        "next_state":{"seen":list(seen)} if not exhausted and selected and len(seen)<2000 else None,
        "stop_reason":stop_reason,"backend":"note_dom_host_steps"},"xiaohongshu",limit,replies_limit)
    result["coverage"]["loading"]={"page_attempts":1,"search_entry":"same_origin_navigation","seconds":round(time.monotonic()-started,3),"wait_driver":"host","optional_caption_required":False}
    return result
