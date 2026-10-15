"""Public article fallback: no browser cookies, no script execution, bounded redirects."""
import re
from html.parser import HTMLParser
from urllib.parse import urlsplit, parse_qs
from urllib.request import Request, HTTPRedirectHandler, build_opener
from urllib.error import HTTPError, URLError
from .model import ReadError, validate_url

ARTICLE_RESPONSE_LIMIT = 8 * 1024 * 1024

VOID = {'area','base','br','col','embed','hr','img','input','link','meta','param','source','track','wbr'}

class ArticleParser(HTMLParser):
    """Parse visible article blocks without executing markup or page scripts."""
    BLOCKS = {'p', 'section', 'div', 'li', 'h1', 'h2', 'h3', 'h4', 'blockquote', 'tr'}
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack=[];self.parts={'title':[],'author':[],'body':[],'publish_time':[]}
        self.text=[];self.media=[];self.body_closed=False;self.code_blocks=0

    def handle_starttag(self, tag, attrs):
        a=dict(attrs);ident=a.get('id','');kind=None
        inherited=self.stack[-1][1] if self.stack else None
        if inherited == 'ignored':kind='ignored'
        elif ident in {'activity-name','js_text_title'}:kind='title'
        elif ident=='js_name':kind='author'
        elif ident=='publish_time':kind='publish_time'
        elif ident=='js_content':kind='body'
        kind=kind or inherited
        classes=a.get('class','').split()
        if tag in {'script','style','noscript'} or 'code-snippet__line-index' in classes or 'hidden' in a or re.search(r'display\s*:\s*none',a.get('style',''),re.I):kind='ignored'
        if kind=='body':
            if tag in self.BLOCKS or tag=='br':self.parts['body'].append('\n')
            if tag=='td':self.parts['body'].append('\t')
            if tag=='pre':
                self.code_blocks+=1
                language=re.sub(r'[^a-zA-Z0-9_+-]','',a.get('data-lang',''))[:30]
                self.parts['body'].append('\n```'+language+'\n')
            if tag=='img':
                src=a.get('data-src') or a.get('src','')
                if src.startswith('//'):src='https:'+src
                try:
                    parsed=urlsplit(src)
                    if parsed.scheme in {'http','https'} and parsed.hostname=='mmbiz.qpic.cn' and not parsed.username and not parsed.password and parsed.port in (None,443,80):
                        clean=src.replace('http://','https://',1)
                        if not any(x['url']==clean for x in self.media):self.media.append({'type':'image','url':clean,'alt':a.get('alt',''),'order':len(self.media)+1})
                except ValueError:pass
        if tag not in VOID:self.stack.append((tag,kind,ident))

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag,attrs)
        if tag not in VOID:self.handle_endtag(tag)

    def handle_endtag(self, tag):
        for i in range(len(self.stack)-1,-1,-1):
            if self.stack[i][0]==tag:
                _,kind,ident=self.stack[i]
                if kind=='body':
                    if tag=='pre':self.parts['body'].append('\n```\n')
                    elif tag in self.BLOCKS or tag=='code':self.parts['body'].append('\n')
                if ident=='js_content':self.body_closed=True
                del self.stack[i:];break

    def handle_data(self,data):
        kind=self.stack[-1][1] if self.stack else None
        if kind=='ignored':return
        self.text.append(data)
        if kind in self.parts:self.parts[kind].append(data)

    def article(self, raw):
        visible=' '.join(self.text)
        title=''.join(self.parts['title']).strip()
        body=re.sub(r'\n[ \t]*\n(?:[ \t]*\n)+','\n\n',''.join(self.parts['body'])).strip()
        if ('环境异常' in visible and ('验证' in visible or '去验证' in visible) and (title=='环境异常' or not (title and body and self.body_closed))) or 'secitptpage/verify.html' in raw:
            raise ReadError('verification_required','微信文章要求验证，请在平台页面完成后重试。')
        if not (title and body) and re.search('已被发布者删除|内容已删除|内容因违规无法查看',visible):
            raise ReadError('content_unavailable','此微信文章当前不可访问。')
        if not (title and body) and '参数错误' in visible:
            raise ReadError('source_resolution_required','微信没有接受此跳转地址。请从搜狗结果页面打开原文，或提供微信文章链接。')
        if not title or not body:raise ReadError('public_article_unavailable','文章响应未包含可识别的正文，需继续验证浏览器读取。')
        published=''.join(self.parts['publish_time']).strip() or None
        source='visible_publish_time' if published else None
        if not published:
            match=re.search(r"(?:var\s+)?(?:ct|create_time)\s*[:=]\s*[\"']?(\d{10})\b",raw)
            if match:
                from datetime import datetime,timezone,timedelta
                published=datetime.fromtimestamp(int(match[1]),timezone(timedelta(hours=8))).isoformat()
                source='page_script_timestamp'
        return {'title':title,'author':''.join(self.parts['author']).strip() or None,'content':body,'text_format':'plain',
                'media':self.media,'publish_time':published,'publish_time_source':source,
                'body_closed':self.body_closed,'code_block_count':self.code_blocks}


def article_item(article, target, backend, body_stable=None, restricted=False):
    from .model import content_item
    item=content_item('weixin',article,target,backend)
    item['media']=article['media']
    item['article_identity']=article.get('article_identity')
    item['source_link_kind']='temporary_signed; use local original-link button' if 'signature' in parse_qs(urlsplit(target).query) else 'article_url; longevity_unverified'
    item['published_at_source']=article.get('publish_time_source')
    item['coverage']={'visible_body':True,'body_closed':article.get('body_closed',False),
        'body_stable':body_stable,'local_truncation':False,'paid_or_collapsed':restricted,
        'code_block_count':article.get('code_block_count',0),'image_text_read':False,'all_article_content':False}
    item['completeness']='visible_body'
    item['read_errors']=[]
    if body_stable is False or not article.get('body_closed',False):
        item['read_errors'].append({'status':'content_unstable','message':'文章区域仍在变化或结束边界未确认，已保留可见正文。'})
    if restricted:
        item['read_errors'].append({'status':'restricted_content','message':'页面存在付费或展开提示，只保留可见部分。'})
    item['notice']='已取得文章可见正文、段落及图片来源；图片内文字未识别，付费或折叠内容不保证包含。'
    return item

class SafeRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        target=validate_url('weixin',newurl,allow_index=False)
        return super().redirect_request(req,fp,code,msg,headers,target)

def read_public_article(target, cancel=None):
    target=validate_url('weixin',target,allow_index=False)
    if cancel and cancel.is_set():raise ReadError('cancelled','任务已取消。')
    try:
        opener=build_opener(SafeRedirect())
        with opener.open(Request(target,headers={'User-Agent':'Mozilla/5.0 (compatible; SocialRead/0.1; public article reader)'}),timeout=15) as response:
            final=validate_url('weixin',response.geturl(),allow_index=False)
            raw=response.read(ARTICLE_RESPONSE_LIMIT+1)
        if len(raw)>ARTICLE_RESPONSE_LIMIT:raise ReadError('content_unavailable','文章响应超过读取预算。')
        if cancel and cancel.is_set():raise ReadError('cancelled','任务已取消。')
        text=raw.decode('utf-8','replace');parser=ArticleParser();parser.feed(text)
        return {**parser.article(text),'url':final}
    except (HTTPError,URLError,TimeoutError,OSError):
        raise ReadError('public_article_unavailable','公开文章连接未完成，需继续验证浏览器读取。')

class SearchParser(HTMLParser):
    """Parse only Sogou's public article cards; never execute site JavaScript."""
    def __init__(self):
        super().__init__(convert_charrefs=True);self.stack=[];self.rows=[];self.card=None;self.visible=[]
    def handle_starttag(self,tag,attrs):
        a=dict(attrs);kind=self.stack[-1][1] if self.stack else None
        if tag=='li' and re.fullmatch(r'sogou_vr_11002601_box_\d+',a.get('id','')):
            self.card={'title':[],'summary':[],'publish_time':[],'date_script':[],'url':''};kind=None
        if self.card is not None:
            if tag=='h3':kind='title'
            elif 'txt-info' in a.get('class','').split():kind='summary'
            elif 's2' in a.get('class','').split():kind='publish_time'
            elif tag=='script':kind='date_script' if kind=='publish_time' else 'ignored'
            if tag=='a' and kind=='title':self.card['url']=a.get('href','')
        elif tag in {'script','style'}:kind='ignored'
        if tag not in VOID:self.stack.append((tag,kind))
    def handle_endtag(self,tag):
        if tag=='li' and self.card is not None:
            row={k:''.join(v).strip() if isinstance(v,list) else v for k,v in self.card.items()}
            if not row['publish_time']:
                m=re.search(r'timeConvert\(["\'](\d+)["\']\)',row['date_script'])
                if m:
                    from datetime import datetime,timezone,timedelta
                    row['publish_time']=datetime.fromtimestamp(int(m[1]),timezone(timedelta(hours=8))).isoformat()
            row.pop('date_script',None)
            self.rows.append(row);self.card=None
        for i in range(len(self.stack)-1,-1,-1):
            if self.stack[i][0]==tag:del self.stack[i:];break
    def handle_data(self,data):
        kind=self.stack[-1][1] if self.stack else None
        if kind!='ignored':self.visible.append(data)
        if self.card is not None and kind in self.card and isinstance(self.card[kind],list):self.card[kind].append(data)


def search_public_articles(query,limit,cancel=None):
    from urllib.parse import urlencode,urljoin
    from .model import keyword,bounded_int
    import secrets
    query=keyword(query);limit=bounded_int(limit)
    if cancel and cancel.is_set():raise ReadError('cancelled','任务已取消。')
    url='https://weixin.sogou.com/weixin?'+urlencode({'query':query,'type':2,'page':1,'ie':'utf8'})
    try:
        with build_opener().open(Request(url,headers={'User-Agent':'Mozilla/5.0 (compatible; SocialRead/0.1; public index reader)'}),timeout=15) as response:
            if urlsplit(response.geturl()).hostname!='weixin.sogou.com':raise ReadError('public_index_unavailable','公开索引返回了未支持的地址。')
            raw=response.read(2*1024*1024+1)
        if len(raw)>2*1024*1024:raise ReadError('adapter_incompatible','索引响应超过读取预算。')
        if cancel and cancel.is_set():raise ReadError('cancelled','任务已取消。')
        raw=raw.decode('utf-8','replace');parser=SearchParser();parser.feed(raw)
        visible=' '.join(parser.visible)
        if re.search('验证码|异常访问|访问过于频繁',visible):raise ReadError('verification_required','搜狗索引要求验证，读取已停止。')
        if not parser.rows:
            if re.search('没有找到相关|未找到相关',visible):return []
            raise ReadError('public_index_unavailable','公开索引结构未能识别，需验证浏览器适配器。')
        pattern=r'href\.substr\(a\+4\+parseInt\(["\'](\d+)["\']\)\+b,1\)'
        offset=re.search(pattern,raw)
        if not offset or not 0<=int(offset[1])<=500:raise ReadError('adapter_incompatible','索引点击参数格式改变，任务已停止。')
        result=[]
        for row in parser.rows[:limit]:
            target=validate_url('weixin',urljoin('https://weixin.sogou.com',row['url']))
            at=target.find('url=');k=secrets.randbelow(100)+1
            if at<0:raise ReadError('adapter_incompatible','索引文章链接缺少跳转目标。')
            pos=at+4+int(offset[1])+k;h=target[pos:pos+1]
            result.append({**row,'url':target+'&k=%s&h=%s'%(k,h),'rank':len(result)+1})
        return result
    except (HTTPError,URLError,TimeoutError,OSError):raise ReadError('public_index_unavailable','公开索引连接未完成，需验证浏览器适配器。')
