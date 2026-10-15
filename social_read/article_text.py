"""Readable block text without site chrome; retain links and image references."""
import re
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit
class BodyParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts=[]; self.images=[]; self.hidden=0
    def handle_starttag(self, tag, attrs):
        a=dict(attrs)
        if tag in {"script","style","noscript"}: self.hidden+=1
        if self.hidden:return
        if tag in {"p","div","li","h1","h2","h3","h4","blockquote","pre","br","tr"}:self.parts.append("\n")
        if tag=="img":
            src=a.get("data-original") or a.get("data-actualsrc") or a.get("data-src") or a.get("src")
            if src and src.startswith("//"):src="https:"+src
            if src and urlsplit(src).scheme=="https" and src not in self.images:self.images.append(src)
    def handle_endtag(self,tag):
        if tag in {"script","style","noscript"}:self.hidden=max(0,self.hidden-1)
        if not self.hidden and tag in {"p","div","li","h1","h2","h3","h4","blockquote","pre","tr"}:self.parts.append("\n")
    def handle_data(self,data):
        if not self.hidden:self.parts.append(data)
    def text(self):return re.sub(r"\n[ \t]*\n[ \t]*\n+","\n\n","".join(self.parts)).strip()
def html_text(value):
    p=BodyParser();p.feed(str(value or ""));return p.text()
