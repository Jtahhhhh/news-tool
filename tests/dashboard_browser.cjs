const { chromium } = require('playwright');
const assert = require('node:assert/strict');
const path = require('node:path');
(async () => {
  const browser = await chromium.launch({headless:true,channel:'msedge'});
  const page = await browser.newPage({viewport:{width:1440,height:1080}});
  const errors=[];
  page.on('pageerror', e=>errors.push(e.message));
  await page.goto('http://127.0.0.1:18080/dashboard/');
  await page.getByRole('heading',{name:'Your stories. In motion.',exact:true}).waitFor();
  await page.getByText('Articles today (UTC)',{exact:true}).waitFor();
  await page.screenshot({path:path.resolve('results/dashboard-desktop.png'),fullPage:true});
  for (const name of ['News','Scripts','Videos','Publish','Jobs','Storage','Settings']) {
    await page.getByRole('link',{name,exact:true}).click();
    await page.locator('main h1').waitFor();
    await page.waitForFunction(()=>!document.querySelector('.skeleton'));
    assert.equal(await page.getByRole('alert').count(),0,`API error on ${name}`);
  }
  await page.setViewportSize({width:390,height:844});
  await page.goto('http://127.0.0.1:18080/dashboard/');
  await page.getByRole('heading',{name:'Your stories. In motion.',exact:true}).waitFor();
  await page.getByText('Articles today (UTC)',{exact:true}).waitFor();
  await page.waitForFunction(()=>!document.querySelector('.skeleton'));
  await page.screenshot({path:path.resolve('results/dashboard-mobile.png'),fullPage:true});
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false,'Page overflows on mobile');
  assert.deepEqual(errors,[]);
  await browser.close();
  console.log('Browser checks passed: 8 routes, desktop/mobile, no console exceptions.');
})().catch(e=>{console.error(e);process.exit(1)});
