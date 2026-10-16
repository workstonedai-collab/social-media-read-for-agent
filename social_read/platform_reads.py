"""Fixed same-origin read requests with explicit long-text and comment coverage."""
import json
from datetime import datetime, timezone
from urllib.parse import urlsplit
from .article_text import BodyParser, html_text
from .browser_page import ReadPage
from .model import ReadError, content_item, digest, scrub, validate_url

FETCH_JS = """
const pause = ms => new Promise(r=>setTimeout(r,ms));
const requestDeadline = Date.now()+45000;
const requestKind = path => /^\/api\/v4\/comment_v5\/(?:answers|articles)\/\d+\/root_comment(?:\?|$)/.test(path)?'zhihu_root_comments':
  /^\/api\/v4\/comment_v5\/comment\/\d+\/child_comment(?:\?|$)/.test(path)?'zhihu_child_comments':
  /^\/api\/v4\/answers\/\d+(?:\?|$)/.test(path)?'zhihu_answer':
  /^\/ajax\/statuses\/show(?:\?|$)/.test(path)?'weibo_status':
  /^\/ajax\/statuses\/buildComments(?:\?|$)/.test(path)?'weibo_comments':
  /^\/ajax\/statuses\/longtext(?:\?|$)/.test(path)?'weibo_longtext':'other_read';
const platformCode = data => {
  const value=data?.error?.code ?? data?.error_code;
  return Number.isInteger(value) && value>=0 && value<=999999999 ? value:null;
};
const getOnce = async path => {
  const abort = new AbortController(); const timer=setTimeout(()=>abort.abort(),Math.max(500,Math.min(12000,requestDeadline-Date.now())));
  const diagnostics={request_kind:requestKind(path),request_attempts:1};
  const fail=(failure,origin)=>({failure,diagnostics:{...diagnostics,failure_origin:origin}});
  try {
    const response = await fetch(path,{credentials:'include',signal:abort.signal});
    diagnostics.http_status=response.status;
    const contentType=(response.headers?.get('content-type') || '').split(';')[0].trim().toLowerCase();
    diagnostics.response_kind=/^(?:application|text)\/(?:[\w.+-]+\+)?json$/.test(contentType)?'json':contentType==='text/html'?'html':contentType?'other':'unknown';
    if (!response.ok) {
      let failure=response.status===429?'rate_limited':response.status===401?'login_required':response.status===403?'access_denied':response.status===404?'content_unavailable':'adapter_error';
      // Inspect this response only, never replay a denial. Keep neither its
      // body/message nor headers/URLs: a 403 JSON challenge has a known code.
      if(diagnostics.response_kind==='json'){
        try {
          const raw=await response.text();
          if(raw.length<=1500000){const code=platformCode(JSON.parse(raw));if(code!==null)diagnostics.platform_error_code=code;}
        } catch {}
      }
      if(response.status===403 && diagnostics.platform_error_code===40362)failure='verification_required';
      return fail(failure,'http_response');
    }
    const raw = await response.text();
    if(raw.length>1500000) return fail('response_budget_exceeded','response_parse');
    try {
      const data = JSON.parse(raw.replace(/"(id|max_id|since_id|root_comment_id|reply_comment_id|reply_to_comment_id|comment_id|user_id)"\\s*:\\s*(\\d{16,})(?=[,}\\s])/g,'"$1":"$2"'));
      if(data.error || data.error_code || data.ok===0){
        const code=platformCode(data);if(code!==null)diagnostics.platform_error_code=code;
        return fail(code===40362?'verification_required':'adapter_error','platform_json');
      }
      return data;
    } catch {return fail('adapter_incompatible','response_parse');}
  } catch {return fail('timeout','network');}
  finally {clearTimeout(timer);}
};
const get = async path => {
  let value=await getOnce(path);
  if(value.failure==='timeout' && Date.now()+6000<requestDeadline){await pause(2500);value=await getOnce(path);if(value.diagnostics)value.diagnostics.request_attempts=2;}
  return value;
};
"""
ERROR_MESSAGES={"rate_limited":"平台限制频率，读取已停止。","login_required":"平台需要登录，请在专用资料完成登录。","access_denied":"平台拒绝此次读取，请到原页核对。","content_unavailable":"内容不可访问或不存在。","verification_required":"平台要求验证，读取已停止。","timeout":"本次平台读取超时。","adapter_incompatible":"平台返回结构发生变化。","response_budget_exceeded":"平台返回超出读取预算。","adapter_error":"平台没有完成此次读取。"}

def check_failure(data):
    if not isinstance(data,dict):
        raise ReadError("adapter_incompatible","读取结果结构发生变化。")
    if data.get("failure"):
        code=data["failure"]
        raise ReadError(code if code in ERROR_MESSAGES else "adapter_error", ERROR_MESSAGES.get(code,"平台读取未完成。"), diagnostics=data.get("diagnostics"))

def read_weibo(runner,target,cancel):
    target=validate_url("weibo",target)
    ident=urlsplit(target).path.rstrip("/").split("/")[-1]
    script="(async()=>{"+FETCH_JS+"""
      const requested=%s;
      const s=await get('/ajax/statuses/show?id='+encodeURIComponent(requested));
      if(s.failure)return s;
      if(!s.idstr || !s.user)return {failure:'adapter_incompatible'};
      const readText=async post=>{
        const initial=post.text_raw || post.text || '';
        const isLong=!!(post.isLongText || post.is_long_text);
        if(!isLong)return {content:initial,long_text:false,expanded:false,status:'not_long',format:post.text_raw?'plain':'html'};
        await pause(2500);
        const lt=await get('/ajax/statuses/longtext?id='+encodeURIComponent(post.idstr));
        if(lt.failure)return {content:initial,long_text:true,expanded:false,status:lt.failure,format:post.text_raw?'plain':'html'};
        if(!lt.data?.longTextContent)return {content:initial,long_text:true,expanded:false,status:'long_text_unavailable',format:post.text_raw?'plain':'html'};
        return {content:lt.data.longTextContent,long_text:true,expanded:true,status:'expanded',format:'html'};
      };
      const text=await readText(s);
      const rt=s.retweeted_status;
      const retweet=rt?{author:rt.user?.screen_name || '[原转发不可见]',id:rt.idstr || '',...await readText(rt)}:null;
      return {id:s.idstr,title:'',author:s.user.screen_name,created_at:s.created_at,url:location.href,
        comments:s.comments_count,text,retweet};
    })()""" % json.dumps(ident)
    with ReadPage(runner,target,cancel,"weibo") as page:
        data=page.evaluate(script,55)
        check_failure(data)
    text=data.get("text",{})
    body=text.get("content","")
    if text.get("format")=="html":body=html_text(body)
    item=content_item("weibo",{**data,"content":body,"text_format":"plain"},target,"opencli-weibo-expanded")
    item["title"]=body[:80]+("…" if len(body)>80 else "")
    item["title_is_excerpt"]=True
    item["coverage"]={"long_text":text.get("long_text"),"long_text_expanded":text.get("expanded"),"text_status":text.get("status"),"all_media_read":False}
    item["read_errors"]=[]
    if text.get("long_text") and not text.get("expanded"):
        item["read_errors"].append({"status":text.get("status"),"message":"本条长微博未取得展开正文，仅保留当前文字。"})
    rt=data.get("retweet")
    if rt:
        rt_body=rt.get("content","")
        if rt.get("format")=="html":rt_body=html_text(rt_body)
        item["retweet"]={"author":rt.get("author"),"body":rt_body,"long_text":rt.get("long_text"),"expanded":rt.get("expanded"),"status":rt.get("status")}
        if rt.get("long_text") and not rt.get("expanded"):item["read_errors"].append({"status":rt.get("status"),"message":"转发原文是长微博，未取得展开正文。"})
    item["notice"]="已读取微博文字，并记录长文是否展开及转发原文的读取状态；图片和视频未识别。"
    return item

def read_zhihu_answer(runner,target,cancel):
    target=validate_url("zhihu",target)
    aid=urlsplit(target).path.rstrip("/").split("/")[-1]
    script="(async()=>{"+FETCH_JS+"""
      const d=await get('/api/v4/answers/'+%s+'?include=content,voteup_count,comment_count,author,created_time,updated_time,question');
      if(d.failure)return d;
      if(typeof d.content!=='string')return {failure:'adapter_incompatible'};
      return {title:d.question?.title || '',author:d.author?.name || '',content:d.content,
        created_at:d.created_time,comments:d.comment_count,excerpt:d.excerpt || ''};
    })()""" % json.dumps(aid)
    with ReadPage(runner,target,cancel,"zh-answer") as page:
        data=page.evaluate(script,30)
        if isinstance(data,dict) and data.get("failure") == "access_denied":
            # No repeat of the denied API. The normal target page is already
            # open: use only its visible, source-bound primary answer area.
            page.check()
            visible_script = "(async()=>{const aid=" + json.dumps(aid) + ";" + r"""
              if(!location.pathname.replace(/\/$/,'').endsWith('/answer/'+aid))return {failure:'source_mismatch'};
              const candidates=[...document.querySelectorAll('.AnswerItem')];
              const root=candidates.find(e=>e.getAttribute('data-aid')===aid || new RegExp('"itemId"\\s*:\\s*"?'+aid+'"?(?=[,}])').test(e.getAttribute('data-zop') || '')) || document.querySelector('.QuestionAnswer-content .AnswerItem');
              if(!root)return {failure:'access_denied'};
              const area=root.querySelector('.RichContent-inner .RichText, .RichText');
              if(!area || !area.getClientRects().length || !(area.innerText || '').trim())return {failure:'access_denied'};
              const expand=root.querySelector('.ContentItem-expandButton');
              if(expand && /展开|阅读全文/.test(expand.innerText || ''))expand.click();
              let last='',same=0;
              for(let i=0;i<12 && same<3;i++){
                await new Promise(r=>setTimeout(r,400));
                const current=area.innerHTML;same=current===last?same+1:0;last=current;
              }
              const more=root.querySelector('.ContentItem-expandButton');
              return {title:document.querySelector('.QuestionHeader-title')?.innerText || '',author:root.querySelector('.AuthorInfo-name')?.innerText || '',
                content:area.innerHTML,created_at:root.querySelector('.ContentItem-time')?.innerText || '',
                visible_fallback:true,dom_stable:same>=3,collapsed:!!more?.getClientRects().length,
                paywall:/开通会员|购买后|付费阅读|解锁全文/.test(root.innerText || '')};
            })()"""
            data=page.evaluate(visible_script,12)
        check_failure(data)
    parser=BodyParser();parser.feed(data["content"])
    stamp=data.get("created_at")
    if isinstance(stamp,(int,float)):stamp=datetime.fromtimestamp(stamp,timezone.utc).isoformat()
    item=content_item("zhihu",{**data,"content":parser.text(),"created_at":stamp,"text_format":"plain"},target,"opencli-zhihu-answer-html")
    item["media"]=[{"type":"image","order":i+1,"url":u} for i,u in enumerate(parser.images)]
    item["coverage"]={"text_source":"answer_visible_dom" if data.get("visible_fallback") else "answer_api_content","local_truncation":False,"all_answers":False,"images_ocr":False}
    if data.get("visible_fallback"):
        item["coverage"].update(api_status="access_denied",dom_stable=data.get("dom_stable"),collapsed=data.get("collapsed"),paywall_detected=data.get("paywall"))
        item["completeness"]="visible_body"
        item["backend"]="opencli-zhihu-answer-visible"
        item["date_semantics"]="visible_time; may be edited time"
        if not data.get("dom_stable") or data.get("collapsed") or data.get("paywall"):
            item["read_errors"]=[{"status":"restricted_or_collapsed","message":"只保留页面已提供的可见回答，付费、折叠或仍在变化的部分未确认。"}]
    item["notice"]="已取得该回答接口提供的正文，保留段落及图片链接，没有本地字符截断；付费或平台不可见部分不在读取范围。"
    if data.get("visible_fallback"):
        item["notice"]="回答接口拒绝访问，未重试接口；从已经打开的同一回答页面取得可见正文，完整性和时间含义需对照原页。"
    return item

def read_zhihu_article(runner,target,cancel):
    target=validate_url("zhihu",target)
    script="""(async () => {
      const selector='.Post-RichTextContainer .RichText, .Post-RichText, article .RichText';
      let previous=-1, stable=0, rounds=0;
      while(rounds++<12 && stable<3){
        await new Promise(r=>setTimeout(r,750));
        const current=document.querySelector(selector)?.innerHTML.length || 0;
        stable=current>0 && current===previous?stable+1:0;previous=current;
      }
      const area=document.querySelector('.Post-RichTextContainer .RichText, .Post-RichText, article .RichText');
      if(!area)return {failure:'content_unavailable'};
      const collapsed=!!area.closest('.is-collapsed') || !!document.querySelector('.ContentItem-expandButton');
      const text=area.innerText || '';
      return {title:document.querySelector('h1.Post-Title,h1')?.innerText || '',author:document.querySelector('.AuthorInfo-name')?.innerText || '',
        content:area.innerHTML,collapsed,paywall:/开通会员|购买后|付费阅读|解锁全文/.test(text),
        dom_stable:stable>=3,created_at:document.querySelector('.ContentItem-time')?.innerText || ''};
    })()"""
    with ReadPage(runner,target,cancel,"zh-article") as page:
        page.wait(".Post-RichTextContainer .RichText, .Post-RichText, article .RichText")
        page.check();data=page.evaluate(script);check_failure(data)
    parser=BodyParser();parser.feed(data["content"])
    item=content_item("zhihu",{**data,"content":parser.text(),"text_format":"plain"},target,"opencli-zhihu-article-dom")
    item["media"]=[{"type":"image","order":i+1,"url":u} for i,u in enumerate(parser.images)]
    item["coverage"]={"text_source":"article_dom","local_truncation":False,"collapsed":data["collapsed"],"paywall_detected":data["paywall"],"images_ocr":False,"dom_stable":data.get("dom_stable",False)}
    item["read_errors"]=[]
    if data["collapsed"] or data["paywall"] or not data.get("dom_stable"):
        item["read_errors"].append({"status":"restricted_or_collapsed","message":"文章可能仍在加载，或显示折叠、付费提示；本次只取得当前可见部分。"})
    item["notice"]="已读取文章正文区域，保留段落及图片链接；如有折叠或付费提示会标为部分完成。图片内文字未识别。"
    return item
