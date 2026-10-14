"""Public article fallback: no browser cookies, no script execution, bounded redirects."""
import re
from html.parser import HTMLParser
from urllib.parse import urlsplit
from urllib.request import Request, HTTPRedirectHandler, build_opener
from urllib.error import HTTPError, URLError
from .model import ReadError, validate_url

VOID = {'area','base','br','col','embed','hr','img','input','link','meta','param','source','track','wbr'}

class ArticleParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack=[];self.parts={'title':[],'author':[],'body':[]};self.text=[];self.media=[]
    def handle_starttag(self, tag, attrs):
        a=dict(attrs);ident=a.get('id','');kind=None
        if ident in {'activity-name','js_text_title'}:kind='title'
        elif ident=='js_name':kind='author'
        elif ident=='js_content':kind='body'
        inherited=self.stack[-1][1] if self.stack else None
        kind=kind or inherited
        if tag in {'script','style'}:kind='ignored'
        if tag in {'p','section','div','br','li'} and kind=='body':self.parts['body'].append('\n')
        if tag=='img' and kind=='body':
            src=a.get('data-src') or a.get('src','')
            try:
                p=urlsplit(src)
                if p.scheme in {'http','https'} and p.hostname=='mmbiz.qpic.cn' and not p.username and not p.password and p.port in (None,443,80):
                    clean=src.replace('http://','https://',1)
                    if not any(x['url']==clean for x in self.media):self.media.append({'type':'image','url':clean,'alt':a.get('alt',''),'order':len(self.media)+1})
            except ValueError:pass
        if tag not in VOID:self.stack.append((tag,kind))
    def handle_startendtag(self, tag, attrs):self.handle_starttag(tag,attrs)
    def handle_endtag(self, tag):
        for i in range(len(self.stack)-1,-1,-1):
            if self.stack[i][0]==tag:
                if self.stack[i][1]=='body' and tag in {'p','section','div','li'}:self.parts['body'].append('\n')
                del self.stack[i:];break
    def handle_data(self,data):
        kind=self.stack[-1][1] if self.stack else None
        if kind=='ignored':return
        self.text.append(data)
        if kind in self.parts:self.parts[kind].append(data)
    def article(self, raw):
        visible=' '.join(self.text)
        if ('环境异常' in visible and ('验证' in visible or '去验证' in visible)) or 'secitptpage/verify.html' in raw:
            raise ReadError('verification_required','微信文章要求验证，请在平台页面完成后重试。')
        if re.search('已被发布者删除|内容已删除|内容因违规无法查看',visible):
            raise ReadError('content_unavailable','此微信文章当前不可访问。')
        title=''.join(self.parts['title']).strip();body=re.sub(r'\n\s*\n+', '\n\n', ''.join(self.parts['body'])).strip()
        if not title or not body:raise ReadError('public_article_unavailable','公开文章响应未包含可识别的正文，需继续验证浏览器读取。')
        return {'title':title,'author':''.join(self.parts['author']).strip() or None,'content':body,'media':self.media}

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
            raw=response.read(2*1024*1024+1)
        if len(raw)>2*1024*1024:raise ReadError('content_unavailable','文章响应超过读取预算。')
        if cancel and cancel.is_set():raise ReadError('cancelled','任务已取消。')
        text=raw.decode('utf-8','replace');parser=ArticleParser();parser.feed(text)
        return {**parser.article(text),'url':final}
    except (HTTPError,URLError,TimeoutError,OSError):
        raise ReadError('public_article_unavailable','公开文章连接未完成，需继续验证浏览器读取。')
