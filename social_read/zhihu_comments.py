"""Read source-bound comments rendered by the normal answer page.

Never construct comment API requests or fall back after a denial. Only the
answer's own comment-panel button and bounded scrolling are visibility actions;
reply expansion, sort changes and publication controls are deliberately unused.
"""
import json
import time
import uuid
from urllib.parse import urlsplit

from .browser_page import ReadPage, _pause
from .model import ReadError, validate_url

CURSOR_READER = 'zhihu_visible_dom_v1'


def snapshot(page, answer_id, operation, replies_limit, action=None, timeout=10):
    cfg={'id':answer_id,'operation':operation,'replies':replies_limit,'action':action}
    script=r"""(()=>{
      const cfg=CONFIG;
      const clean=e=>(e?.innerText || e?.textContent || '').replace(/\u200b/g,'').trim();
      const visible=e=>!!e?.getClientRects().length && getComputedStyle(e).visibility!=='hidden';
      if(location.hostname!=='www.zhihu.com' || !new RegExp('/answer/'+cfg.id+'/?$').test(location.pathname))return {failure:'source_mismatch'};
      const roots=[...document.querySelectorAll('.AnswerItem')].filter(e=>e.getAttribute('name')===cfg.id || e.getAttribute('data-aid')===cfg.id || new RegExp('"itemId"\\s*:\\s*"?'+cfg.id+'"?(?=[,}])').test(e.getAttribute('data-zop') || ''));
      const dialogs=[...document.querySelectorAll('.Modal-content,[role="dialog"]')].filter(visible);
      const signals=[document.title,...dialogs.filter(e=>!e.querySelector('.CommentContent') && !/^\d+\s*条评论/.test(clean(e))).map(clean)];
      if(roots.length===0 && clean(document.body).length<2000)signals.push(clean(document.body));
      for(const text of signals){
        if(/验证码|安全验证|异常访问|访问异常|安全限制|去验证/.test(text))return {failure:'verification_required'};
        if(/访问过于频繁|请求过于频繁/.test(text))return {failure:'rate_limited'};
        if(/拒绝访问|无权访问|访问被拒绝/.test(text))return {failure:'access_denied'};
        if(/登录后查看|请登录后|登录后阅读全文|登录知乎/.test(text))return {failure:'login_required'};
      }
      if(roots.length>1)return {failure:'source_mismatch'};
      if(!roots.length)return {ready:false,phase:'answer_loading',roots:[]};
      const answer=roots[0];
      let state=window.__socialReadZhihuComments;
      if(!state || state.operation!==cfg.operation)state=window.__socialReadZhihuComments={operation:cfg.operation,id:cfg.id,open_dispatched:false};
      if(state.id!==cfg.id)return {failure:'source_mismatch'};
      const buttons=[...answer.querySelectorAll('button')].filter(e=>e.closest('.AnswerItem')===answer && visible(e) && /^(?:\d+\s*条评论|添加评论|评论|收起评论)$/.test(clean(e)));
      if(buttons.length!==1)return {ready:false,phase:'comment_button_loading',roots:[]};
      let clicked=false;
      if(cfg.action==='open'){
        if(state.open_dispatched)return {failure:'comment_action_repeated'};
        // The operation owns a fresh tab. An already open/unowned modal is
        // ambiguous, so never attach a different answer's discussion.
        if(dialogs.length || clean(buttons[0])==='收起评论')return {failure:'source_mismatch'};
        state.open_dispatched=true;
        buttons[0].click();clicked=true;
      }
      const panels=dialogs.filter(e=>e.classList.contains('Modal-content') && (/^\d+\s*条评论/.test(clean(e)) || !!e.querySelector('.CommentContent')));
      if(!state.open_dispatched)return {ready:true,panel_open:false,clicked,roots:[]};
      if(panels.length>1)return {failure:'source_mismatch'};
      if(!panels.length)return {ready:true,panel_open:false,clicked,roots:[]};
      const panel=panels[0];
      if(clean(buttons[0])!=='收起评论')return {failure:'source_mismatch'};
      if(panel.dataset.socialReadSource && panel.dataset.socialReadSource!==cfg.id)return {failure:'source_mismatch'};
      panel.dataset.socialReadSource=cfg.id;
      const own=(node,selector)=>[...node.querySelectorAll(selector)].filter(e=>e.closest('[data-id]')===node);
      const nodes=[...panel.querySelectorAll('[data-id]')].filter(e=>visible(e) && own(e,'.CommentContent').length===1);
      if(nodes.length>500)return {failure:'response_budget_exceeded'};
      const row=node=>{
        const content=own(node,'.CommentContent')[0], copy=content.cloneNode(true);
        for(const image of copy.querySelectorAll('img'))image.replaceWith(document.createTextNode(image.getAttribute('alt') || ''));
        let text=(copy.textContent || '').trim(), kind='text';
        if(!text && content.querySelector('img')){text='（此评论只有图片或表情，未识别）';kind='non_text';}
        const author=own(node,'a[href*="/people/"]').map(clean).find(Boolean) || '';
        const footer=content.nextElementSibling;
        const date=[...(footer?.querySelectorAll('span,time') || [])].map(clean).find(t=>/^(?:\d{4}[-年/])?\d{1,2}[-月/]\d{1,2}(?:日)?(?:\s|$)/.test(t)) || null;
        return {id:node.getAttribute('data-id'),author,text,time:date,content_kind:kind};
      };
      const selected=[];
      for(const node of nodes){
        const ancestor=node.parentElement?.closest('[data-id]');
        if(ancestor && panel.contains(ancestor))continue;
        const root=row(node);
        if(!root.id || !root.text)continue;
        const children=nodes.filter(e=>e!==node && node.contains(e) && e.parentElement?.closest('[data-id]')===node)
          .slice(0,cfg.replies).map(e=>({...row(e),parent_id:root.id,is_reply:true})).filter(e=>e.id && e.text);
        selected.push({root:{...root,is_reply:false},children,
          expandable:own(node,'button').some(e=>/^展开.*回复|^查看.*回复/.test(clean(e)))});
      }
      const panelText=clean(panel), empty=selected.length===0 && /^0\s*条评论/.test(panelText);
      const ended=empty || [...panel.querySelectorAll('span,div,p')].some(e=>!e.children.length && !e.closest('.CommentContent') && /^(?:没有更多了|没有更多评论|已显示全部评论)$/.test(clean(e)));
      if(!selected.length && /加载失败|请求失败|网络异常/.test(panelText))return {failure:'comments_loading_incomplete'};
      let scrolled=false;
      if(cfg.action==='scroll'){
        const content=panel.querySelector('.CommentContent');
        let scroller=content?.parentElement;
        while(scroller && panel.contains(scroller)){
          if(/auto|scroll/.test(getComputedStyle(scroller).overflowY) && scroller.scrollHeight>scroller.clientHeight+4){
            const before=scroller.scrollTop;
            scroller.scrollTop+=Math.min(600,Math.max(100,scroller.clientHeight*.8));
            scrolled=scroller.scrollTop>before;break;
          }
          scroller=scroller.parentElement;
        }
      }
      return {ready:true,panel_open:true,clicked,roots:selected,ended,empty,scrolled};
    })()""".replace('CONFIG',json.dumps(cfg))
    data=page.evaluate(script,timeout)
    if not isinstance(data,dict):raise ReadError('adapter_incompatible','网页评论结构没有返回可识别结果。')
    if data.get('failure'):
        code=data['failure']
        messages={'source_mismatch':'评论面板与当前回答的对应关系未确认，已停止。',
            'verification_required':'平台要求验证，已停止网页评论读取。',
            'login_required':'平台要求登录，已停止网页评论读取。',
            'rate_limited':'平台限制了访问频率，已停止网页评论读取。',
            'access_denied':'平台拒绝此次网页读取，已停止。',
            'comments_loading_incomplete':'网页评论加载未完成，没有重复点击或请求接口。',
            'response_budget_exceeded':'网页评论超出本次读取预算。',
            'comment_action_repeated':'本次评论面板已打开过，已停止重复操作。'}
        raise ReadError(code if code in messages else 'adapter_incompatible',messages.get(code,'网页评论结构发生变化，已停止。'))
    return data


def read_visible_comments(runner,target,limit,replies_limit,cancel,cursor=None):
    target=validate_url('zhihu',target)
    answer_id=urlsplit(target).path.rstrip('/').split('/')[-1]
    if '/answer/' not in urlsplit(target).path:
        raise ReadError('unsupported','此网页评论读取路径仅支持具体知乎回答。')
    if cursor and cursor.get('reader')!=CURSOR_READER:
        raise ReadError('cursor_expired','旧接口的评论续读标识不适用于网页路径，请从原回答重新取样。')
    seen=set((cursor or {}).get('seen',[]));selected={};errors=[]
    started=time.monotonic();operation=uuid.uuid4().hex;clicks=scrolls=probes=0
    stop_reason='time_budget';raw={};previous=None;stable=0
    with ReadPage(runner,target,cancel,'zhihu-comments-dom') as page:
        deadline=time.monotonic()+30
        def probe(action=None):
            nonlocal probes
            remaining=deadline-time.monotonic()
            if remaining<=0:raise ReadError('timeout','本次网页评论读取时间预算已用完。')
            probes+=1
            return snapshot(page,answer_id,operation,replies_limit,action,min(10,remaining))
        try:
            opened=False
            for _ in range(24):
                if cancel and cancel.is_set():raise ReadError('cancelled','任务已取消。')
                raw=probe()
                if not raw.get('ready'):
                    _pause(cancel,.5);continue
                if not opened:
                    # Set before dispatch. Unknown outcomes terminate; no
                    # click, navigation or alternate API is replayed.
                    opened=True;probe('open');clicks=1
                    _pause(cancel,.5);continue
                for entry in raw.get('roots',[]):
                    ident=entry.get('root',{}).get('id')
                    if ident and ident not in seen and (ident in selected or len(selected)<limit):selected[ident]=entry
                fingerprint=json.dumps(list(selected.values()),sort_keys=True)
                stable=stable+1 if fingerprint==previous else 0;previous=fingerprint
                if len(selected)>=limit and stable>=1:
                    stop_reason='root_sample_limit';break
                if raw.get('ended'):
                    stop_reason='visible_empty' if raw.get('empty') else 'visible_end';break
                if raw.get('panel_open') and stable>=2 and len(selected)<limit:
                    if scrolls>=3:
                        stop_reason='loading_stalled';break
                    changed=probe('scroll');scrolls+=1
                    if not changed.get('scrolled'):
                        stop_reason='loading_stalled';break
                _pause(cancel,.5)
        except ReadError as error:
            if error.code=='cancelled' or not selected:raise
            errors.append({**error.result(),'stage':'comments_walk'});stop_reason='read_interrupted'
    items=[];not_expanded=0
    for ident,entry in selected.items():
        items.append(entry['root']);items.extend(entry.get('children',[])[:replies_limit]);seen.add(ident)
        if entry.get('expandable') and len(entry.get('children',[]))<replies_limit:not_expanded+=1
    exhausted=bool(raw.get('ended')) and all(e.get('root',{}).get('id') in seen for e in raw.get('roots',[]))
    from .comments import finish
    result=finish({'items':items,'errors':errors,'root_pages':1,'reply_requests':0,
        'reply_roots_not_expanded':not_expanded,'is_end':True if exhausted else None,
        'next_state':{'reader':CURSOR_READER,'seen':list(seen)} if selected and not exhausted and not errors and len(seen)<2000 else None,
        'stop_reason':stop_reason,'backend':'zhihu_answer_visible_dom'},'zhihu',limit,replies_limit)
    result['coverage'].update(sort='platform_default',visible_replies_only=True,
        loading={'page_attempts':1,'comment_panel_clicks':clicks,'scroll_actions':scrolls,
                 'reply_expansions':0,'direct_api_requests':0,'dom_probes':probes,
                 'seconds':round(time.monotonic()-started,3),'wait_driver':'host'})
    result['notice']+=' 使用正常回答页评论面板，仅取已渲染的评论和可见回复；未展开的回复不计入已读范围，平台页面自身的网络请求由平台管理。'
    return result
