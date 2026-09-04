/* Isolated UI regression: <compiled-dir> <screenshots-dir>; Playwright supplied by test runtime. */
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const {chromium}=require('playwright');
async function main(){
 const [compiled,output]=process.argv.slice(2);fs.mkdirSync(output,{recursive:true});
 const browser=await chromium.launch({headless:true,executablePath:process.env.CHROMIUM_EXECUTABLE||undefined});
 try{for(const width of [390,1280]){
  const page=await browser.newPage({viewport:{width,height:1000},isMobile:width===390,hasTouch:width===390});
  const requests=[],errors=[];page.on('pageerror',e=>errors.push(e.message));
  const c=(id,extra={})=>({id,author:'读者',kind:'user_comment',text:`研究问题${id}是否需要继续观察？`,like_count:0,reply_count:0,reply_depth:0,thread_key:`t${id}`,author_liked:false,published_at:'2026-09-01',...extra});
  const comments=[c(1,{text:'这里是原始问题，需要保留作为作者回复的上下文。'}),c(2,{author:'模型先生',kind:'author_reply',text:'作者回复：请先核对资料来源，再观察风险边界。',reply_depth:1,thread_key:'t1'}),c(3,{text:'这是作者点赞过的评论。',author_liked:true}),c(4,{author:'模型先生',text:'同名但没有作者标记的评论。'})];
  comments.push(...Array.from({length:41},(_,i)=>c(200+i,{reply_depth:1,thread_key:'t1'})));
  comments.push(...Array.from({length:15},(_,i)=>c(10+i,{like_count:100-i})));
  comments.push(c(99,{like_count:1,text:'较完整的讨论：先说明自己的研究假设，然后列出事实与不确定因素，最后提出希望作者解释的具体问题。'}));
  const work={id:1,title:'评论显示回归样例',published_at:'2026-09-01',keywords:[],has_video_text:true,has_interpretation:false,media_available:false,comment_count:comments.length};
  const stock=(name,code,count,ids)=>({name,code,rank:9,comment_count:count,mention_count:count,fan_comment_count:count-1,author_comment_count:1,comment_ids:ids,examples:[]});
  const detail={work,video_text:{text:'已有原文'},interpretation:{text:''},transcripts:[],comments,comment_total:comments.length,capabilities:{},stock_mentions:{method:'local-security-master',total_comments:80,stock_count:3,items:[stock('股票乙','000002',5,[99]),stock('股票甲','000001',9,[2,3]),stock('股票丙','000003',4,[999])],uncertain:[{text:'不确定简称',comment_count:2,candidates:['候选甲','候选乙']}],api_used:false}};
  await page.route('http://127.0.0.1:19848/**',async route=>{
   const u=new URL(route.request().url()),p=u.pathname;requests.push(p);
   const json=x=>route.fulfill({contentType:'application/json',body:JSON.stringify(x)});
   if(p==='/')return route.fulfill({contentType:'text/html; charset=utf-8',body:'<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><link rel="stylesheet" href="/styles.css"><main></main><script type="module">import{ModelMrPanel}from"/ModelMrPanel.js";const p=new ModelMrPanel();document.querySelector("main").append(p.element);p.element.hidden=false;await p.refresh();window.ready=true;</script>'});
   if(['/ModelMrPanel.js','/ModelMrComments.js','/api.js','/styles.css'].includes(p))return route.fulfill({contentType:p.endsWith('.css')?'text/css':'text/javascript',body:fs.readFileSync(path.join(compiled,p.slice(1)),'utf8')});
   if(p.endsWith('/status'))return json({available:true,counts:{works:1}});
   if(p.endsWith('/thoughts'))return json({categories:[]});
   if(p.endsWith('/chat/config'))return json({enabled:false,models:[]});
   if(p.endsWith('/works'))return json({items:[work],count:1,total:1,offset:0,has_more:false});
   if(p.endsWith('/works/1'))return json(detail);
   throw Error('unexpected endpoint '+p);
  });
  await page.goto('http://127.0.0.1:19848/');await page.waitForFunction(()=>window.ready);
  await page.locator('[data-model-action="open-detail"][data-detail-tab="comments"]').click();
  await page.locator('.model-comment-tabs').waitFor();
  assert.equal(await page.locator('[data-comment-id="2"] .model-author-badge').count(),1);
  assert.equal(await page.locator('[data-comment-id="3"] .model-author-liked-badge').count(),1);
  assert.equal(await page.locator('[data-comment-id="4"] .model-author-badge').count(),0);
  assert.equal(await page.locator('.model-author-badge').first().evaluate(e=>getComputedStyle(e).backgroundColor),'rgb(216, 36, 70)');
  assert(await page.locator('[data-comment-id="1"]').count());
  const count=await page.locator('.model-comment').count();assert(count<20);
  await page.locator('.model-thread-more summary').click();
  await page.waitForFunction(()=>document.querySelectorAll('.model-comment').length>20);
  await page.locator('.model-thread-more summary').click();
  await page.locator('.model-comments-panel').screenshot({path:path.join(output,`作者互动-${width}.png`)});
  await page.locator('[data-comment-tab="ranking"]').click();
  assert.equal(await page.locator('.model-high-liked > .model-comment-thread').count(),10);
  assert.equal(await page.locator('.model-high-liked > .model-comment-thread').first().getAttribute('data-thread-key'),'t10');
  assert.equal(await page.locator('.model-quality-comments > .model-comment-thread').first().getAttribute('data-thread-key'),'t99');
  await page.locator('.model-comment-tabs').scrollIntoViewIfNeeded();await page.screenshot({path:path.join(output,`评论排行-${width}.png`)});
  await page.locator('[data-comment-tab="stocks"]').click();
  assert.equal(await page.locator('.model-stock-row .model-comment-thread').count(),0,'stock comments lazy until expanded');
  assert.match(await page.locator('.model-stock-row summary').first().innerText(),/股票甲/);
  await page.locator('.model-stock-row summary').first().click();
  await page.locator('.model-stock-row [data-comment-id="2"]').waitFor();
  assert(await page.locator('.model-stock-row [data-comment-id="1"]').count(),'question retained for author reply');
  await page.locator('.model-stock-row summary').last().click();
  await page.locator('.model-stock-row').last().getByText('关联正文尚未同步，请勿把缺少正文当成零提及。').waitFor();
  assert.match(await page.locator('.model-stock-row').last().innerText(),/关联正文尚未同步/);
  assert.match(await page.locator('.model-stock-uncertain').innerText(),/不计入股票排名/);
  await page.locator('.model-stock-row summary').first().click();
  await page.locator('.model-comments-panel').screenshot({path:path.join(output,`评股-${width}.png`)});
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
  assert(!requests.some(p=>/transcribe|collect|\/chat$/.test(p)));assert.deepEqual(errors,[]);
  console.log(JSON.stringify({width,authorBadges:'pass',topTen:'pass',qualityOrder:'pass',stockContext:'pass',noPaidCalls:true}));
  await page.close();
 }}finally{await browser.close();}
}
main().catch(e=>{console.error(e);process.exitCode=1;});
