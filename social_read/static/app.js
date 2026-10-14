/* All remote content is rendered with textContent. No HTML from posts is executed. */
const $ = id => document.getElementById(id);
const labels = {xiaohongshu:'小红书',weibo:'微博',zhihu:'知乎',weixin:'微信公众号'};
const statusNames = {queued:'等待中',running:'读取中',success:'已完成',partial:'部分完成',failed:'未完成',cancelled:'已取消',interrupted:'已中断'};
let token='', mode='live', active=null, currentRef=null;
const filePreview=location.protocol==='file:';
const demoJobs=new Map(), demoItems=new Map();
function node(tag,text,cls){const n=document.createElement(tag);if(text!==undefined)n.textContent=String(text);if(cls)n.className=cls;return n;}
function message(text,error=false){$('notice').replaceChildren(node('div',text,'message'+(error?' error':'')));}
function sourceLink(url,text){try{const p=new URL(url);if(p.protocol!=='https:'||p.username||p.password||!['www.xiaohongshu.com','xiaohongshu.com','weibo.com','www.weibo.com','m.weibo.cn','www.zhihu.com','zhihu.com','zhuanlan.zhihu.com','mp.weixin.qq.com','weixin.sogou.com','mmbiz.qpic.cn'].includes(p.hostname))return node('span','来源链接暂不可用','meta');const a=node('a',text);a.href=p.href;a.target='_blank';a.rel='noopener noreferrer';return a;}catch{return node('span','来源链接暂不可用','meta');}}
function originalButton(item,text){
 const button=node('button',text,'quiet');
 button.onclick=async()=>{
  const opened=window.open('about:blank','_blank');
  if(!opened){message('浏览器阻止了新窗口，请允许本站打开来源。',true);return;}
  opened.opener=null;
  try{
   const data=await api('/api/source',{content_ref:item.content_ref});
   const safe=sourceLink(data.url,text);
   if(safe.tagName!=='A')throw new Error('来源链接暂不可用');
   opened.location.replace(safe.href);
  }catch(e){opened.close();message(e.message,true);}
 };
 return button;
}
function stamp(s){
 if(!s)return '日期未提供';
 if(/^\d{4}-\d{2}-\d{2}T/.test(String(s))){const d=new Date(s);if(!Number.isNaN(d.getTime()))return new Intl.DateTimeFormat('zh-CN',{timeZone:'Asia/Shanghai',year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',second:'2-digit',hour12:false}).format(d)+'（北京时间）';}
 return String(s);
}
async function api(path,body){
 if(filePreview)return previewApi(path,body);
 const response=await fetch(path,{method:body?'POST':'GET',headers:{'Content-Type':'application/json','X-Social-Read-Token':token},body:body?JSON.stringify(body):undefined});
 const data=await response.json();if(!response.ok)throw new Error(data.message||'本机服务未完成请求');return data;
}
function previewApi(path,body){
 if(path==='/api/bootstrap')return {mode:'demo',token:'',platforms:Object.entries(labels).map(([id,label])=>({id,label}))};
 if(path==='/api/history')return {jobs:Array.from(demoJobs.values()).reverse()};
 if(path.endsWith('/cancel'))return {status:'cancelled'};
 if(path.startsWith('/api/tasks/'))return demoJobs.get(path.split('/').pop());
 const id='preview_'+Date.now(),{action,request}=body;
 let result={mode:'demo',items:[],errors:[]};
 if(action==='search')result.items=request.platforms.map(s=>{const ref='preview_'+s,item={content_ref:ref,platform:s,title:'虚构样例｜'+request.query+'的使用反馈',author:'演示作者',source:s==='weixin'?'搜狗微信索引':'平台网页',published_at_raw:null,notice:'界面演示，不是平台真实内容。',mode:'demo'};demoItems.set(ref,item);return item;});
 if(action==='read'){const item=demoItems.get(request.content_ref)||{platform:request.platform,title:'虚构样例',content_ref:'preview_direct'};result.content={...item,author:'演示作者',body:'这是虚构资料，用于演示搜索与阅读界面。\n\n样例观点：用户希望更方便地搜索，也希望每个判断都能回到来源。\n\n这里没有连接真实社交媒体，不能作为调研证据。',notice:'界面预览，未运行读取服务。',cache:{used:false}};currentRef=item.content_ref;demoItems.set(currentRef,item);}
 if(action==='comments'){const item=demoItems.get(request.content_ref);if(item?.platform==='weixin'){result.errors=[{message:'公众号留言暂不支持。'}];}else{result.items=[{author:'虚构用户',text:'这是一条演示评论，不来自真实平台。'}];result.coverage={returned_rows:1,all_comments:false};result.notice='虚构评论，未连接平台。';}}
 if(action==='connection')result.connection={status:'demo',message:'界面预览不检查浏览器和真实账号。'};
 demoJobs.set(id,{task_id:id,action,request,status:result.errors.length?'failed':'success',created_at:new Date().toISOString(),result});return {task_id:id,status:'queued'};
}
function showErrors(errors){$('errors').replaceChildren(...(errors||[]).map(e=>node('div',(e.platform?labels[e.platform]+'：':'')+(e.message||e.status),'message error')));}
async function run(action,request){
 if(active){message('请等待当前任务完成，或先取消。');return null;}
 $('search-submit').disabled=true;$('cancel').hidden=false;showErrors([]);
 try{
  const submitted=await api('/api/tasks',{action,request});active=submitted.task_id;
  $('result-status').textContent='正在读取，已完成的结果会保留。';
  let job;
  while(active){job=await api('/api/tasks/'+active);if(!['queued','running'].includes(job.status))break;if(action==='search'&&job.result?.items?.length)renderResults(job.result.items);await new Promise(r=>setTimeout(r,900));}
  if(!job)return null;
  showErrors(job.result?.errors);$('result-status').textContent=statusNames[job.status]||job.status;
  if(job.status==='cancelled')message('任务已取消，已完成结果保留。');
  return job;
 }catch(e){message(e.message,true);return null;}
 finally{active=null;$('cancel').hidden=true;$('search-submit').disabled=false;await history();}
}
function renderResults(items){
 $('results').replaceChildren();
 if(!items?.length){$('results').append(node('p','本次没有取得内容。请查看提示或换一个关键词。','hint'));return;}
 for(const item of items){const card=node('article',undefined,'card');card.append(node('span',labels[item.platform]||item.platform,'badge'),node('p',item.title,'title'),node('p',(item.author||'作者未提供')+' · '+(item.date_estimate?'约 '+item.date_estimate+'（ID 推算）':stamp(item.published_at_raw)),'meta'),node('p',item.source||'来源未提供','meta'));if(item.summary)card.append(node('p',item.summary,'meta'));const actions=node('div',undefined,'card-actions'),button=node('button','阅读');button.onclick=()=>read({content_ref:item.content_ref});actions.append(button);if(mode==='live')actions.append(originalButton(item,'打开原始结果'));card.append(actions);$('results').append(card);}
}
function renderContent(content){
 currentRef=content.content_ref;$('refresh').hidden=!currentRef;
 const box=$('content');box.replaceChildren(node('span',labels[content.platform]||content.platform,'badge'),node('h3',content.title),node('p',(content.author||'作者未提供')+' · '+stamp(content.published_at_raw),'meta'),node('div',content.notice||'请核对正文范围。','message'));
 box.append(node('p','取得 '+(content.body_chars??Array.from(content.body||'').length)+' 个文字字符 · '+(content.media_count??content.media?.length??0)+' 个图片链接','meta'));
 if(content.cache?.used)box.append(node('p','使用本机缓存 · 获取于 '+stamp(content.fetched_at),'meta'));
 if(mode==='live')box.append(originalButton(content,'到原帖核对'));
 box.append(node('div',content.body||'未取得正文。','body'));
 if(content.media?.length){const media=node('details');media.append(node('summary','图片链接（未读取图内文字）'));for(const item of content.media){if(item.type==='image'){const row=node('p');row.append(sourceLink(item.url,'第 '+item.order+' 张图片'));media.append(row);}}box.append(media);}
 if(content.retweet)box.append(node('p','转发来源：'+content.retweet.author,'meta'),node('div',content.retweet.body||'转发正文未提供','body'));
 const comments=node('button','查看评论','quiet');comments.disabled=content.platform==='weixin';comments.onclick=async()=>{const job=await run('comments',{content_ref:content.content_ref,limit:10});if(job&&job.status==='success'){const section=node('section');section.append(node('h3','此次取得的评论'),node('p',job.result.notice||'仅代表本次读取范围。','hint'));for(const c of job.result.items||[]){const row=node('div',undefined,'comment');row.append(node('strong',c.author||'作者未提供'));if(c.is_reply)row.append(node('p','回复 '+(c.reply_to||'上一层评论'),'meta'));row.append(node('p',c.text||c.content||''));section.append(row);}const coverage=job.result.coverage||{};section.append(node('p','取得 '+(coverage.returned_rows??job.result.items?.length??0)+' 条记录 · 不是全部评论','hint'));box.querySelector('#comments-view')?.remove();section.id='comments-view';box.append(section);}};
 box.append(comments);if(content.platform==='weixin')box.append(node('p','公众号留言查看暂未支持。','hint'));
}
async function read(request){const job=await run('read',request);if(job?.result?.content){renderContent(job.result.content);if(window.innerWidth<760)$('reader-title').scrollIntoView({behavior:'smooth'});}}
async function history(){try{const data=await api('/api/history');$('history').replaceChildren();for(const job of data.jobs||[]){const row=node('div',undefined,'history-row');row.append(node('span',({search:'搜索',read:'阅读',comments:'评论查看',connection:'连接检查'})[job.action]||job.action),node('span',statusNames[job.status]||job.status,'badge'),node('span',(job.request?.query||'')+' '+(job.request?.platform?labels[job.request.platform]:(job.request?.platforms||[]).map(s=>labels[s]).join('、')),'meta'));const button=node('button','查看记录','quiet');button.onclick=()=>{showErrors(job.result?.errors);if(job.action==='search')renderResults(job.result?.items);if(job.result?.content)renderContent(job.result.content);if(job.result?.connection)message(job.result.connection.message);$('result-status').textContent='历史记录 · '+stamp(job.updated_at||job.created_at);};row.append(button);$('history').append(row);}if(!data.jobs?.length)$('history').append(node('p','暂无任务，开始搜索后会保存在这里。','hint'));}catch(e){message('任务记录暂不可用，请确认本机服务正在运行。',true);}}
$('search-form').onsubmit=async e=>{e.preventDefault();const platforms=Array.from($('platforms').querySelectorAll('input:checked')).map(x=>x.value);if(!platforms.length){message('请选择至少一个平台。');return;}const job=await run('search',{platforms,query:$('query').value,limit:5});if(job)renderResults(job.result?.items);};
$('read-form').onsubmit=e=>{e.preventDefault();read({platform:$('direct-platform').value,url:$('url').value});};
$('check').onclick=async()=>{const job=await run('connection',{});if(job?.result?.connection){message(job.result.connection.message);const identities=job.result.platforms||[];for(const i of identities){const n=node('div',(labels[i.platform]||i.platform)+'：'+(i.message||i.status),'message');$('errors').append(n);}}};
$('cancel').onclick=async()=>{if(active){try{await api('/api/tasks/'+active+'/cancel',{});message('正在停止当前任务。');}catch(e){message(e.message,true);}}};
$('refresh').onclick=()=>currentRef&&read({content_ref:currentRef,refresh:true});
$('history-refresh').onclick=history;
(async()=>{try{const boot=await api('/api/bootstrap');token=boot.token;mode=boot.mode;$('mode').textContent=mode==='demo'?'离线演示 · 虚构样例':'本机读取 · 试用版';for(const p of boot.platforms){const label=node('label'),input=node('input');input.type='checkbox';input.value=p.id;input.checked=true;label.append(input,node('span',p.label));$('platforms').append(label);}if(mode==='demo')message(filePreview?'这是界面预览，未运行读取服务；所有样例均为虚构。':'这是离线演示，所有内容均为虚构；不代表平台读取成功。');await history();}catch(e){$('mode').textContent='本机服务未连接';message('请从本机启动器运行 Social Read，再打开此页面。',true);}})();
