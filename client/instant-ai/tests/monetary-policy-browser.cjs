/* Synthetic policy clues only: compiled ESM directory, dist, output directory. */
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const {chromium}=require('playwright');
async function main(){
  const [compiled,dist,output]=process.argv.slice(2);
  assert(compiled&&dist&&output);fs.mkdirSync(output,{recursive:true});
  const browser=await chromium.launch({headless:true,executablePath:process.env.CHROMIUM_EXECUTABLE});
  let checks=0;
  try{
    for(const width of [390,1280]){
      const page=await browser.newPage({viewport:{width,height:1000},timezoneId:'America/New_York'});
      const errors=[];page.on('pageerror',e=>errors.push(e.message));
      await page.route('**/*',route=>{
        const u=new URL(route.request().url());assert.equal(u.origin,'http://policy.test');
        if(u.pathname==='/')return route.fulfill({contentType:'text/html',body:'<!doctype html><meta name="viewport" content="width=device-width,initial-scale=1"><link rel="stylesheet" href="/styles.css"><main></main>'});
        if(u.pathname==='/styles.css')return route.fulfill({contentType:'text/css',body:fs.readFileSync(path.join(dist,'styles.css'))});
        return route.fulfill({contentType:'text/javascript',body:fs.readFileSync(path.join(compiled,path.basename(u.pathname)))});
      });
      await page.goto('http://policy.test/');
      await page.evaluate(async()=>{
        const {WatchEventsPanel}=await import('/WatchEventsPanel.js');window.panel=new WatchEventsPanel();
        const media={key:'synthetic',title:'沃勒讨论12月与1月的有条件利率路径',bankName:'美联储',kindLabel:'官员表态线索',speaker:'Christopher Waller',itemId:1,
          publishedAt:'2026-11-18T12:42:36Z',detectedAt:'2026-11-18T12:45:00Z',status:'delivered',pipelineStatus:'罗盘已收到，等待既有GPT任务核验',
          firstObservedSource:{publisher:'CNBC',url:'https://www.cnbc.com/synthetic-policy.html',sourceRole:'publisher-report',originality:'original-channel-verification-required'}};
        window.response={events:[],counts:{total:0,home:0,zijin:0,configured:0,official_reachable:0,signals_detected:0,signals_delivered:0},sync:null,
          monetaryPolicy:{coverage:'覆盖已接入来源，不代表全部采访。',signals:[media,{...media,key:'forecast',title:'合成机构预测',kindLabel:'机构预测',speaker:'',status:'failed',pipelineStatus:'投递未确认，自动重试'},
            {...media,key:'official',title:'合成央行利率决议线索',kindLabel:'利率决议线索',firstObservedSource:{...media.firstObservedSource,publisher:'美联储',url:'https://www.federalreserve.gov/synthetic.htm',sourceRole:'official-original'}}]}};
        window.panel.render(window.response);document.querySelector('main').append(window.panel.element);
      });
      const section=page.locator('.monetary-policy-signals');
      assert((await section.innerText()).includes('官员表态线索'));checks++;
      assert((await section.innerText()).includes('机构预测'));checks++;
      assert((await section.innerText()).includes('已读报道渠道（首发待核验）：CNBC'));checks++;
      assert((await section.innerText()).includes('官方原始渠道：美联储'));checks++;
      assert((await section.innerText()).includes('20:42'));checks++;
      assert(!(await section.innerText()).includes('07:42'));checks++;
      assert((await section.innerText()).includes('等待既有GPT任务核验'));checks++;
      assert.equal(await section.locator('a[data-reading="original"][rel="noopener noreferrer"][target="_blank"]').count(),3);checks++;
      assert((await section.locator('.has-error').innerText()).includes('自动重试'));checks++;
      assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth),width);checks++;
      await page.screenshot({path:path.join(output,`monetary-policy-${width}.png`),fullPage:true});
      await page.evaluate(()=>{
        window.response.monetaryPolicy.signals=[{...window.response.monetaryPolicy.signals[0],title:'<img src=x onerror=alert(1)>',publishedAt:'',
          status:'date_review',pipelineStatus:'日期或时效待核验，尚未自动输送',firstObservedSource:{publisher:'<script>danger()</script>',url:'javascript:alert(1)',sourceRole:'publisher-report'}}];
        window.panel.render(window.response);
      });
      assert.equal(await section.locator('img,script,a').count(),0);checks++;
      assert((await section.innerText()).includes('<img src=x'));checks++;
      assert((await section.innerText()).includes('来源标注时间：待核实'));checks++;
      assert.deepEqual(errors,[]);checks++;
      await page.close();
    }
  }finally{await browser.close();}
  console.log(`${checks} monetary policy browser checks passed at 390/1280px`);
}
main().catch(e=>{console.error(e);process.exitCode=1;});
