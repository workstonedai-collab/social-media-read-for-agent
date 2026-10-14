// Opt-in real platform run through the official MCP client. Never an offline test.
import assert from 'node:assert/strict';
import { pathToFileURL } from 'node:url';
import { join, resolve } from 'node:path';
import { writeFile } from 'node:fs/promises';
const sdk=process.env.MCP_SDK_ROOT;
if(process.env.SOCIAL_READ_LIVE!=='1'||!sdk)throw new Error('Set SOCIAL_READ_LIVE=1 and MCP_SDK_ROOT to opt into real account reads');
const {Client}=await import(pathToFileURL(join(sdk,'client/index.js')));
const {StdioClientTransport}=await import(pathToFileURL(join(sdk,'client/stdio.js')));
const client=new Client({name:'current-codex-social-read-research',version:'1.0.0'},{capabilities:{}});
const root=resolve('.');
const invoke=async(name,args)=>{
 const result=await client.callTool({name,arguments:args});
 assert.equal(result.isError,false);
 return JSON.parse(result.content[0].text);
};
const finish=async(taskId)=>{
 const deadline=Date.now()+180000;
 while(Date.now()<deadline){
  const job=await invoke('get_task',{task_id:taskId,wait_seconds:20});
  if(!['running','queued'].includes(job.status))return job;
 }
 throw new Error('Task did not reach terminal before the bounded research deadline');
};
try{
 await client.connect(new StdioClientTransport({command:process.env.PYTHON||'python3',args:[join(root,'start_mcp.py')],cwd:root,stderr:'pipe'}));
 const query=process.env.SOCIAL_READ_QUERY||'知识管理 软件 推荐';
 const submitted=await invoke('search',{platforms:['xiaohongshu','weibo','zhihu','weixin'],query,limit:1});
 const search=await finish(submitted.task_id), reads=[];
 console.log(JSON.stringify({stage:'search',task_id:search.task_id,status:search.status,returned:search.result.items.length}));
 for(const item of search.result.items){
  const task=await invoke('read_content',{content_ref:item.content_ref,refresh:true});
  const job=await finish(task.task_id);reads.push({search_item:item,job});
  const c=job.result.content||{};
  console.log(JSON.stringify({stage:'read',platform:item.platform,task_id:job.task_id,status:job.status,body_chars:c.body_chars||0,cache:c.cache,errors:job.result.errors}));
 }
 await writeFile(join(root,'.state/current-codex-mcp-research.json'),JSON.stringify({client:'official MCP SDK driven by current Codex',search,reads},null,2),{mode:0o600});
 const evidence={client:'official MCP SDK driven by current Codex',query,search_task_id:search.task_id,search_status:search.status,sources:reads.map(({search_item,job})=>{const c=job.result.content||{};return{platform:search_item.platform,task_id:job.task_id,status:job.status,title:c.title||search_item.title,content_ref:c.content_ref||search_item.content_ref,source_url:c.source_url||search_item.source_url,body_chars:c.body_chars||0,media_count:c.media_count||0,body_sha256:c.body_sha256,backend:c.backend,cache:c.cache,published_at_raw:c.published_at_raw,errors:job.result.errors};})};
 await writeFile(join(root,'docs/codex-mcp-research-evidence.json'),JSON.stringify(evidence,null,2)+'\n');
}finally{await client.close();}
