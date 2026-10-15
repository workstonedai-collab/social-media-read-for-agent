"""Read the selected note, ordered images, and local image text in one owned page."""
import json
from urllib.parse import urlsplit
from .browser_page import ReadPage
from .images import enrich_images
from .model import ReadError, content_item, validate_url

def read_note(runner, target, cancel, context=None, progress=None):
    target = validate_url("xiaohongshu", target)
    note_id = urlsplit(target).path.rstrip("/").split("/")[-1]
    script = """(() => {
      const id = %s;
      const current = location.pathname.match(/\/(?:explore|search_result|discovery\/item)\/([a-f0-9]{24})/i);
      if (!current || current[1] !== id) return {error:'source_mismatch'};
      const pageText = document.body?.innerText || '';
      if (/安全限制|访问链接异常|验证码|安全验证/.test(pageText)) return {error:'verification_required'};
      if (/登录后查看|请登录/.test(pageText)) return {error:'login_required'};
      const state = window.__INITIAL_STATE__;
      const map = state?.note?.noteDetailMap || state?.note?.note || {};
      const entry = map[id];
      const note = entry?.note || entry;
      const clean = e => (e?.innerText || e?.textContent || '').trim();
      const title = clean(document.querySelector('#detail-title')) || note?.title || '';
      const content = clean(document.querySelector('#detail-desc')) || note?.desc || '';
      const author = clean(document.querySelector('.author-wrapper .username, .author-container .username, .username')) || note?.user?.nickname || '';
      let images = [], image_ids = [];
      let order = 'page_dom_unverified';
      if (Array.isArray(note?.imageList) && note.imageList.length) {
        order = 'note_state_carousel';
        image_ids = note.imageList.map(i=>String(i.fileId || i.file_id || ''));
        images = note.imageList.map(i => i.urlDefault || i.urlPre || i.url || i.infoList?.find(x=>x.imageScene==='WB_DFT')?.url || i.infoList?.[0]?.url || '');
      } else {
        const nodes = document.querySelectorAll('.note-slider .swiper-slide img, .note-slider-img, .carousel-image img, #noteContainer .media-container img');
        images = [...nodes].map(i=>i.currentSrc || i.src || i.getAttribute('data-src') || '');
      }
      return {note_id:id, title, content, author, images:images.slice(0,40),
        reported_count:Array.isArray(note?.imageList)?note.imageList.length:images.length,
        image_order:order, image_ids:image_ids.slice(0,40), type:note?.type || (document.querySelector('video')?'video':'unknown'),
        publish_time:clean(document.querySelector('.bottom-container .date, .note-content .date'))};
    })()""" % json.dumps(note_id)
    with ReadPage(runner, target, cancel, "xhs") as page:
        page.wait("#detail-title, #detail-desc, .note-container")
        data = page.evaluate(script)
        if not isinstance(data, dict):
            raise ReadError("adapter_incompatible", "笔记读取结果格式变化。")
        if data.get("error"):
            raise ReadError(data["error"] if data["error"] in {"verification_required", "login_required"} else "source_mismatch", "没有取得匹配的笔记，或平台要求登录、验证。")
        if not data.get("title") and not data.get("author"):
            raise ReadError("content_unavailable", "笔记页面没有提供可见内容。")
        # Image-only notes still have a named body and explicit empty caption.
        caption = data.get("content") or ""
        item = content_item("xiaohongshu", {**data, "content": caption or "（此笔记未提供可见配文）"}, target, "opencli-note-images")
        item["note_type"] = data.get("type")
        item = enrich_images(item, data.get("images", []), runner, cancel, data.get("image_order"), data.get("reported_count"), context=context, progress=progress, image_ids=data.get("image_ids"))
        item["caption"] = caption
        return item
