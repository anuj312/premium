import asyncio, json
from pathlib import Path
from playwright.async_api import async_playwright

async def main():
  async with async_playwright() as p:
    browser=await p.chromium.launch(headless=True,executable_path='/usr/bin/chromium',args=['--no-sandbox','--disable-dev-shm-usage'])
    for width,height,theme in [(1440,900,'light'),(1280,720,'dark'),(390,844,'light'),(320,700,'dark')]:
      page=await browser.new_page(viewport={'width':width,'height':height}, device_scale_factor=1)
      errors=[];page.on('pageerror',lambda e: errors.append(str(e)))
      await page.goto('http://127.0.0.1:8779/',wait_until='domcontentloaded',timeout=15000)
      await page.wait_for_selector('.stock-tile',timeout=15000)
      if theme=='dark':
        await page.evaluate("document.documentElement.dataset.theme='dark'")
      await page.wait_for_timeout(650)
      inf=await page.evaluate('''() => ({
        width:document.documentElement.scrollWidth,
        clientWidth:document.documentElement.clientWidth,
        height:document.documentElement.scrollHeight,
        clientHeight:document.documentElement.clientHeight,
        leaders:document.querySelectorAll('.leader-scored-card').length,
        scores:document.querySelectorAll('.leader-scored-card .momentum-rating').length,
        obs:document.querySelectorAll('.leader-scored-card .ob-quality-chip').length,
        violations:[...document.querySelectorAll('.leader-scored-card')].filter(el=>el.scrollHeight>el.clientHeight+2).slice(0,5).map(x=>[x.dataset.stock,x.scrollHeight,x.clientHeight]),
        first: document.querySelector('.leader-scored-card')?.innerText,
        firstCardHeight:document.querySelector('.leader-scored-card')?.getBoundingClientRect().height,
        pillBounds:[...document.querySelectorAll('.leader-scored-card .leader-tile-scores')].slice(0,3).map(e=>({scroll:e.scrollWidth,client:e.clientWidth,height:e.clientHeight}))
      })''')
      path=f'/mnt/data/OrderFlow_Top6_Scores_{width}_{theme}.png'
      await page.screenshot(path=path,full_page=(width<=600))
      print(width,height,theme, json.dumps(inf), 'errors',errors[:3], 'screenshot',path,flush=True)
      await page.close()
    await browser.close()
asyncio.run(main())
