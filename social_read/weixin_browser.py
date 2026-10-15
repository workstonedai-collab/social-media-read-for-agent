"""Normal Sogou article click in one owned tab, pinned to the chosen profile."""
import json
import re
import uuid
from urllib.parse import urlencode

from .model import ReadError, classify_error, content_item, keyword, validate_url
from .weixin_public import ArticleParser, article_item
from .browser_page import wait_selector


def read_search_article(runner, query, title, cancel):
    if not runner.profile or not isinstance(title, str) or not title or len(title) > 1000:
        raise ReadError("source_resolution_required", "需要搜索记录和专用浏览器资料才能打开原文。")
    query = keyword(query)
    session = "socialread-article-" + uuid.uuid4().hex
    tab = None

    def command(args, timeout=15):
        code, out, err = runner.run(["browser", session] + args, cancel, timeout)
        if code:
            raise classify_error(out + err, code)
        return out.strip()

    def page_text():
        return json.JSONDecoder().raw_decode(command(["get", "text", "body", "--tab", tab]))[0].get("value", "")

    def check_page():
        text = str(page_text())
        if re.search(r"验证码|安全验证|环境异常.*验证|异常访问|去验证", text):
            raise ReadError("verification_required", "平台页面要求验证，读取已停止。请在专用浏览器完成后重试。")
        if "参数错误" in text:
            raise ReadError("source_resolution_required", "正常点击后微信仍未接受文章地址，请到原文核对。")

    try:
        url = "https://weixin.sogou.com/weixin?" + urlencode({"query": query, "type": 2, "page": 1, "ie": "utf8"})
        opened = json.JSONDecoder().raw_decode(command(["tab", "new", url], 20))[0]
        tab = opened.get("page")
        if not isinstance(tab, str) or not re.fullmatch(r"[\w-]{1,100}", tab):
            raise ReadError("adapter_incompatible", "浏览器没有返回本次创建的页面标识。")
        try:
            wait_selector(runner, session, tab, ".news-list h3 a", cancel, 15)
        except ReadError:
            check_page()
            raise
        # The title is a JSON string, never executable source. Only a known result
        # link gets a local target change; the site's normal click handler runs.
        select = """(() => {
          const norm = s => String(s || '').normalize('NFKC').replace(/\\s+/g, '');
          const wanted = %s;
          const links = Array.from(document.querySelectorAll('.news-list h3 a'));
          const matched = links.filter(a => norm(a.textContent) === norm(wanted));
          if (matched.length !== 1) return {selected:false};
          const a = matched[0];
          const u = new URL(a.href, location.href);
          if (u.protocol !== 'https:' || u.hostname !== 'weixin.sogou.com' || u.pathname !== '/link' || u.username || u.password) return {selected:false};
          a.target = '_self'; a.setAttribute('data-social-read-selected', '1');
          return {selected:true};
        })()""" % json.dumps(title, ensure_ascii=True)
        selected = json.JSONDecoder().raw_decode(command(["eval", select, "--tab", tab]))[0]
        if not selected.get("selected"):
            raise ReadError("source_context_changed", "搜索页没有唯一匹配这篇文章。请重新搜索后读取，不能替换成另一篇文章。")
        command(["click", '.news-list h3 a[data-social-read-selected="1"]', "--tab", tab])
        try:
            wait_selector(runner, session, tab, "#js_content", cancel, 15)
        except ReadError:
            check_page()
            raise
        return extract_article(command,session,tab)
    except (ValueError, TypeError, AttributeError):
        raise ReadError("adapter_incompatible", "文章读取组件的返回格式发生变化。")
    finally:
        if tab and re.fullmatch(r"[\w-]{1,100}", tab):
            try:
                runner.run(["browser", session, "tab", "close", tab], timeout=5)
            except ReadError:
                pass


def extract_article(command, session, tab):
    stable_script = """(async()=>{
      let last='', same=0;const deadline=Date.now()+10000;
      while(Date.now()<deadline){
        const el=document.querySelector('#js_content');
        const text=el?.textContent || '';
        const signature=text.length+'|'+el?.querySelectorAll('img').length+'|'+text.slice(-160);
        same=text.trim() && signature===last?same+1:0;last=signature;
        if(same>=3)return {stable:true};
        await new Promise(r=>setTimeout(r,400));
      }
      return {stable:false};
    })()"""
    state=json.JSONDecoder().raw_decode(command(['eval',stable_script,'--tab',tab],15))[0]
    current=validate_url('weixin',command(['get','url','--tab',tab]),allow_index=False)
    html=command(['get','html','--selector','.rich_media_area_primary','--max','1500000','--tab',tab])
    if len(html)>=1500000:
        raise ReadError('content_unavailable','文章区域超过读取预算，未将截断内容当作全文。')
    parser=ArticleParser();parser.feed(html);article=parser.article(html)
    metadata_script="""(() => {
      const link=document.querySelector('link[rel="canonical"]');
      const meta=document.querySelector('meta[property="og:url"]');
      let url=link?.href || meta?.content || location.href, timestamp=null;
      for(const s of document.querySelectorAll('script:not([src])')){
        const source=s.textContent || '';
        const m=source.match(/(?:var\\s+)?msg_link\\s*=\\s*["']([^"']+)["']/);
        if(m && !link && !meta)url=m[1];
        const t=source.match(/(?:var\\s+)?(?:ct|create_time)\\s*[:=]\\s*["']?(\\d{10})\\b/);
        if(t)timestamp=Number(t[1]);
      }
      return {url,timestamp,article_identity:{biz:typeof window.biz==='string'?window.biz:null,mid:typeof window.mid==='string'?window.mid:null,idx:typeof window.idx==='string'?window.idx:null},restricted:!!document.querySelector('.paywall, #js_pay_content, .js_expand_btn, #js_content .read-more')};
    })()"""
    meta=json.JSONDecoder().raw_decode(command(['eval',metadata_script,'--tab',tab]))[0]
    try:
        current=validate_url('weixin',meta.get('url'),allow_index=False)
    except ReadError:pass
    if not article.get('publish_time') and isinstance(meta.get('timestamp'),(int,float)):
        from datetime import datetime,timezone,timedelta
        article['publish_time']=datetime.fromtimestamp(meta['timestamp'],timezone(timedelta(hours=8))).isoformat()
        article['publish_time_source']='page_script_timestamp'
    article['url']=current
    article['article_identity']=meta.get('article_identity')
    return article_item(article,current,'opencli-visible-article',state.get('stable'),bool(meta.get('restricted')))


def read_article(runner, target, cancel):
    from .browser_page import ReadPage
    target=validate_url('weixin',target,allow_index=False)
    with ReadPage(runner,target,cancel,'weixin') as page:
        page.wait('#js_content',15)
        return extract_article(page.command,page.session,page.tab)
