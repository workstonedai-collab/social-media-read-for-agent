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
const getOnce = async path => {
  const abort = new AbortController(); const timer=setTimeout(()=>abort.abort(),Math.max(500,Math.min(12000,requestDeadline-Date.now())));
  try {
    const response = await fetch(path,{credentials:'include',signal:abort.signal});
    if (!response.ok) return {failure:response.status===429?'rate_limited':response.status===401?'login_required':response.status===403?'access_denied':response.status===404?'content_unavailable':'adapter_error'};
    const raw = await response.text();
    if(raw.length>1500000) return {failure:'response_budget_exceeded'};
    try {
      const data = JSON.parse(raw.replace(/"(id|root_comment_id|reply_comment_id|reply_to_comment_id|comment_id|user_id)"\\s*:\\s*(\\d{16,})(?=[,}\\s])/g,'"$1":"$2"'));
      if(data.error || data.error_code || data.ok===0) return {failure:(data.error?.code===40362 || data.error_code===40362)?'verification_required':'adapter_error'};
      return data;
    } catch {return {failure:'adapter_incompatible'};}
  } catch {return {failure:'timeout'};}
  finally {clearTimeout(timer);}
};
const get = async path => {
  let value=await getOnce(path);
  if(value.failure==='timeout' && Date.now()+6000<requestDeadline){await pause(2500);value=await getOnce(path);}
  return value;
};
"""
ERROR_MESSAGES={"rate_limited":"平台限制频率，读取已停止。","login_required":"平台需要登录，请在专用资料完成登录。","access_denied":"平台拒绝此次读取，请到原页核对。","content_unavailable":"内容不可访问或不存在。","verification_required":"平台要求验证，读取已停止。","timeout":"本次平台读取超时。","adapter_incompatible":"平台返回结构发生变化。","response_budget_exceeded":"平台返回超出读取预算。","adapter_error":"平台没有完成此次读取。"}

def check_failure(data):
    if not isinstance(data,dict):
        raise ReadError("adapter_incompatible","读取结果结构发生变化。")
    if data.get("failure"):
        code=data["failure"]
        raise ReadError(code if code in ERROR_MESSAGES else "adapter_error", ERROR_MESSAGES.get(code,"平台读取未完成。"))

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
        data=page.evaluate(script,20);check_failure(data)
    parser=BodyParser();parser.feed(data["content"])
    stamp=data.get("created_at")
    if isinstance(stamp,(int,float)):stamp=datetime.fromtimestamp(stamp,timezone.utc).isoformat()
    item=content_item("zhihu",{**data,"content":parser.text(),"created_at":stamp,"text_format":"plain"},target,"opencli-zhihu-answer-html")
    item["media"]=[{"type":"image","order":i+1,"url":u} for i,u in enumerate(parser.images)]
    item["coverage"]={"text_source":"answer_api_content","local_truncation":False,"all_answers":False,"images_ocr":False}
    item["notice"]="已取得该回答接口提供的正文，保留段落及图片链接，没有本地字符截断；付费或平台不可见部分不在读取范围。"
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
