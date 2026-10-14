// Explicit opt-in. May access the real platform again when the 30-minute cache expires.
import assert from 'node:assert/strict';
import { pathToFileURL } from 'node:url';
import { join,resolve } from 'node:path';
const sdk=process.env.MCP_SDK_ROOT, ref=process.env.SOCIAL_READ_CONTENT_REF;
if(!sdk||!ref)throw new Error('Set MCP_SDK_ROOT and SOCIAL_READ_CONTENT_REF for an existing real item');
const {Client}=await import(pathToFileURL(join(sdk,'client/index.js')));
const {StdioClientTransport}=await import(pathToFileURL(join(sdk,'client/stdio.js')));
const client=new Client({name:'social-read-live-validation',version:'1.0.0'},{capabilities:{}});
try{
 await client.connect(new StdioClientTransport({command:process.env.PYTHON||'python3',args:['-m','social_read','mcp'],cwd:resolve('.'),stderr:'pipe'}));
 const decode=r=>{assert.equal(r.isError,false);return JSON.parse(r.content[0].text);};
 const submitted=decode(await client.callTool({name:'read_content',arguments:{content_ref:ref}}));
 let job;
 for(let i=0;i<900;i++){
  job=decode(await client.callTool({name:'get_task',arguments:{task_id:submitted.task_id}}));
  if(!['queued','running'].includes(job.status))break;
  await new Promise(r=>setTimeout(r,100));
 }
 assert.ok(['success','partial'].includes(job.status));assert.equal(job.result.mode,'live');if(job.status==='partial')assert.ok(job.result.errors.length>0);
 const c=job.result.content;assert.ok(c.body.length>0);
 console.log(JSON.stringify({status:'passed',client:'official MCP SDK',mode:'live',platform:c.platform,task_status:job.status,body_chars:c.body.length,cache:c.cache,title:c.title,task_id:job.task_id}));
}finally{await client.close();}
