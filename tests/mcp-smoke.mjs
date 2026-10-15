import assert from 'node:assert/strict';
import { pathToFileURL } from 'node:url';
import { mkdtemp, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join, resolve } from 'node:path';
const sdk = process.env.MCP_SDK_ROOT;
if (!sdk) throw new Error('Set MCP_SDK_ROOT to the official SDK dist/esm directory');
const { Client } = await import(pathToFileURL(join(sdk, 'client/index.js')));
const { StdioClientTransport } = await import(pathToFileURL(join(sdk, 'client/stdio.js')));
const state = await mkdtemp(join(tmpdir(), 'social-read-mcp-'));
const client = new Client({name:'social-read-validation', version:'1.0.0'}, {capabilities:{}});
const transport = new StdioClientTransport({command:process.env.PYTHON || 'python3', args:[resolve('start_mcp.py'),'--mode','demo','--state-dir',state,'mcp'], cwd:resolve('.'), stderr:'pipe'});
const value = result => { assert.equal(result.isError, false); return JSON.parse(result.content[0].text); };
const terminal = new Set(['success','partial','failed','cancelled','interrupted']);
async function task(name, args) {
  const submitted = value(await client.callTool({name, arguments:args}));
  for (let i=0;i<80;i++) {
    const job = value(await client.callTool({name:'get_task', arguments:{task_id:submitted.task_id}}));
    if (terminal.has(job.status)) return job;
    await new Promise(r=>setTimeout(r,50));
  }
  throw new Error('Task did not terminate');
}
try {
  await client.connect(transport);
  const runtime=value(await client.callTool({name:'list_platforms',arguments:{}}));
  assert.equal(runtime.runtime.connection,'stable-stdio-gateway');
  const listed = await client.listTools();
  assert.equal(listed.tools.length,7);
  assert.ok(listed.tools.every(t=>t.annotations.readOnlyHint));
  const search = await task('search',{platforms:['xiaohongshu','weibo','zhihu','weixin'],query:'知识管理工具'});
  assert.equal(search.status,'success'); assert.equal(search.result.items.length,4);
  for (const item of search.result.items) {
    const read = await task('read_content',{content_ref:item.content_ref});
    assert.equal(read.status,'success'); assert.equal(read.result.content.mode,'demo');
    assert.ok(read.result.content.body.includes('不是社交平台真实帖子'));
  }
  const comments = await task('read_comments',{content_ref:search.result.items[0].content_ref});
  assert.equal(comments.status,'success'); assert.equal(comments.result.coverage.all_comments,false);
  assert.equal((await client.callTool({name:'publish',arguments:{}})).isError,true);
  assert.equal((await client.callTool({name:'search',arguments:{platforms:['weibo'],query:'主题',limit:true}})).isError,true);
  console.log(JSON.stringify({status:'passed',transport:'stdio',client:'official MCP SDK',tool_count:7,platforms:4,search_read_comments:'fictional demo only',write_action:'rejected'}));
} finally { await client.close(); await rm(state,{recursive:true,force:true}); }
