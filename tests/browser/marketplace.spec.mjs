import {test,expect} from '@playwright/test';
import AxeBuilder from '@axe-core/playwright';
import {readFileSync} from 'node:fs';
import {execFileSync} from 'node:child_process';
import {randomBytes} from 'node:crypto';
const PY=process.env.PYTHON||'.venv/bin/python';
const credentials=()=>JSON.parse(readFileSync('data/browser-tests/credentials.json','utf8'));
async function ready(page){await page.locator('.hero-copy').waitFor();await page.evaluate(()=>document.fonts.ready);}
async function login(page,role,{destination='/account'}={}){
 const u=credentials()[role];await page.goto('/?lang=ar');await ready(page);
 await page.locator('.account-link').click();
 await page.locator('#email').fill(u.email);await page.locator('#password').fill(u.password);
 if(role==='admin'){
  await page.locator('#auth-form details summary').click();
  const code=execFileSync(PY,['-c','import pyotp,sys;print(pyotp.TOTP(sys.stdin.read()).now())'],{input:u.secret,encoding:'utf8'}).trim();
  await page.locator('#otp').fill(code);
 }
 await page.locator('#auth-form button[type=submit]').click();
 await expect(page).toHaveURL(/\/account$/);
 if(destination!=='/account')await page.goto(destination);
 return u;
}
async function assertNoOverflow(page){
 const metrics=await page.evaluate(()=>{
  const viewport=innerWidth;
  const offenders=[...document.querySelectorAll('body *')].map(el=>{
   const r=el.getBoundingClientRect(),style=getComputedStyle(el);
   return {tag:el.tagName,selector:(el.id?'#'+el.id:'')+(typeof el.className==='string'&&el.className?'.'+el.className.trim().split(/\\s+/).join('.'):''),left:Math.round(r.left),right:Math.round(r.right),width:Math.round(r.width),position:style.position};
  }).filter(x=>x.right>viewport+1||x.left< -1).sort((a,b)=>b.right-a.right).slice(0,8);
  return {viewport,documentWidth:document.documentElement.scrollWidth,offenders};
 });
 expect(metrics.documentWidth<=metrics.viewport,JSON.stringify(metrics)).toBe(true);
}
async function accessible(page){
 const results=await new AxeBuilder({page}).withTags(['wcag2a','wcag2aa','wcag21a','wcag21aa','wcag22aa']).analyze();
 expect(results.violations.map(v=>({id:v.id,nodes:v.nodes.map(n=>({target:n.target,summary:n.failureSummary}))}))).toEqual([]);
}

test('Arabic/English storefront, real search, stores, saved items and keyboard dialog',async({page})=>{
 const errors=[];page.on('pageerror',e=>errors.push(e.message));
 await page.goto('/?lang=ar');await ready(page);
 await expect(page.locator('html')).toHaveAttribute('dir','rtl');
 await expect(page.locator('.product-card')).toHaveCount(8);
 await page.locator('.product-card').first().scrollIntoViewIfNeeded();
 await page.evaluate(async()=>{
  const images=[...document.querySelectorAll('.product-card img')].slice(0,4);
  for(const image of images){
   image.loading='eager';
   if(!image.complete)await new Promise((resolve,reject)=>{image.addEventListener('load',resolve,{once:true});image.addEventListener('error',()=>reject(new Error('Could not load '+image.currentSrc)),{once:true});});
   if(!image.naturalWidth)throw new Error('Image has no decoded pixels: '+image.currentSrc);
   await image.decode();
  }
 });
 await accessible(page);
 await page.locator('.product-card [data-action=favorite]').first().click();
 await page.goto('/favorites');await expect(page.locator('.product-card')).toHaveCount(1);
 await page.goto('/search?q='+encodeURIComponent('سماعات'));
 await expect(page.locator('.product-card')).toHaveCount(1);
 await page.locator('#sort-select').selectOption('price_asc');
 await expect(page).toHaveURL(/sort=price_asc/);
 await page.goto('/stores');await expect(page.locator('.store-card')).toHaveCount(3);
 await page.locator('.store-card').first().click();await expect(page.locator('.store-cover h1')).toBeVisible();
 await page.goto('/');await ready(page);
 await page.locator('.strip-links [data-action=language]').click();
 await expect(page.locator('html')).toHaveAttribute('lang','en');
 await expect(page.locator('html')).toHaveAttribute('dir','ltr');
 await expect(page.locator('.hero-copy h1')).toHaveText('Good finds. Great beginnings.');
 await accessible(page);
 await page.locator('.account-link').click();
 await expect(page.locator('dialog')).toBeVisible();
 await accessible(page);
 await page.keyboard.press('Escape');await expect(page.locator('dialog')).not.toBeVisible();
 expect(errors).toEqual([]);await assertNoOverflow(page);
});

test('phone layout has no overflow and the two-store guest bag becomes a persisted COD order',async({page})=>{
 await page.setViewportSize({width:390,height:844});await page.goto('/?lang=ar');await ready(page);await assertNoOverflow(page);
 await page.locator('[data-action=add-cart][data-id="1"]').click();
 await expect(page.locator('#cart-count')).toHaveText('١');
 await page.locator('[data-action=add-cart][data-id="4"]').click();
 await expect(page.locator('#cart-count')).toHaveText('٢');
 await page.goto('/cart');await expect(page.locator('.cart-group')).toHaveCount(2);await assertNoOverflow(page);
 await page.reload();await expect(page.locator('.cart-group')).toHaveCount(2);
 await page.locator('[data-action=checkout]').click();
 const u=credentials().buyer;
 await page.locator('#email').fill(u.email);await page.locator('#password').fill(u.password);
 await page.locator('#auth-form button[type=submit]').click();
 await expect(page).toHaveURL(/\/checkout$/);await page.locator('#checkout-form').waitFor();await assertNoOverflow(page);
 await accessible(page);
 await page.locator('#recipient').fill('Generated recipient');await page.locator('#phone').fill('0791234567');
 await page.locator('#city').selectOption('irbid');await page.locator('#address').fill('Generated test address, building 2, floor 1');
 const response=page.waitForResponse(r=>r.url().endsWith('/api/orders')&&r.request().method()==='POST');
 await page.locator('button[form=checkout-form]').click();const result=await(await response).json();
 expect(result.order.shipments).toHaveLength(2);expect(result.order.total_fils).toBe(75000);
 await expect(page).toHaveURL(/\/orders$/);await expect(page.locator('.order-card')).toHaveCount(1);
 await page.reload();await expect(page.locator('.order-card')).toHaveCount(1);await assertNoOverflow(page);
 await page.locator('[data-action=message-shipment]').first().click();
 const messageBody='<script>window.__shamaa_message_xss=1</script> استفسار حول الشحنة';
 await page.locator('#conversation-start-body').fill(messageBody);
 const messageResponse=page.waitForResponse(r=>r.url().endsWith('/api/conversations')&&r.request().method()==='POST');
 await page.locator('#conversation-start-form button[type=submit]').click();
 const messageResult=await messageResponse;
 expect(messageResult.status()).toBe(201);
 await expect(page).toHaveURL(/\/messages\?conversation=\d+/);
 await expect(page.locator('.message-body')).toHaveText(messageBody);
 await expect(page.locator('#message-feed script')).toHaveCount(0);
 expect(await page.evaluate(()=>window.__shamaa_message_xss)).toBeUndefined();
 await assertNoOverflow(page);await accessible(page);
 await page.goto('/orders');await expect(page.locator('.order-card')).toHaveCount(1);
 await page.locator('[data-action=cancel-order]').first().click();await page.locator('#confirm-action').click();
 await expect(page.locator('.shipment .pill.cancelled')).toHaveCount(1);
 await accessible(page);
});

test('store onboarding, actual admin MFA review, image upload, product approval and stock editing',async({browser})=>{
 const context=await browser.newContext({locale:'ar-JO',viewport:{width:1280,height:900}});const seller=await context.newPage();
 await login(seller,'seller',{destination:'/seller'});await seller.locator('#store-form').waitFor();
 const storeName='متجر الاختبار المعزول';
 await seller.locator('#name_ar').fill(storeName);await seller.locator('#name_en').fill('Isolated browser store');
 await seller.locator('#slug').fill('isolated-browser-store');await seller.locator('#description_ar').fill('متجر مولد لاختبارات المتصفح فقط.');
 await seller.locator('#description_en').fill('A generated store for isolated browser tests only.');
 await seller.locator('#city').selectOption('irbid');await seller.locator('#store-form button[type=submit]').click();
 await expect(seller.locator('.notice.warn strong')).toContainText('قيد المراجعة');
 const adminContext=await browser.newContext({locale:'ar-JO'});const admin=await adminContext.newPage();
 await login(admin,'admin',{destination:'/admin?tab=stores'});
 const storeCard=admin.locator('.review-card').filter({hasText:storeName});
 await storeCard.locator('button[value=approve]').click();await expect(storeCard.locator('.pill')).toHaveText('نشط');
 await seller.goto('/seller?tab=products');await seller.locator('[data-action=new-product]').first().click();
 await seller.locator('#title_ar').fill('مصباح الاختبار المعزول');await seller.locator('#title_en').fill('Isolated test lamp');
 await seller.locator('#description_ar').fill('وصف منتج مولد لغرض اختبار التدفق الفعلي.');
 await seller.locator('#description_en').fill('Generated product details for testing a real application flow.');
 await seller.locator('#sku').fill('GENERATED-LAMP');await seller.locator('#price').fill('17.125');await seller.locator('#stock').fill('4');
 await seller.locator('#product-image').setInputFiles('data/browser-tests/product.png');
 const extraImage=await seller.evaluate(()=>{
  const canvas=document.createElement('canvas');canvas.width=48;canvas.height=48;
  const ctx=canvas.getContext('2d');ctx.fillStyle='#315b42';ctx.fillRect(0,0,48,48);
  ctx.fillStyle='#e8c78d';ctx.fillRect(8,8,32,32);
  return canvas.toDataURL('image/png').split(',')[1];
 });
 await seller.locator('#gallery-images').setInputFiles([{
  name:'extra-gallery-image.png',mimeType:'image/png',buffer:Buffer.from(extraImage,'base64')
 }]);
 await seller.locator('#product-form button[type=submit]').click();
 await expect(seller.locator('tbody .pill')).toHaveText('قيد المراجعة');
 await admin.goto('/admin?tab=products');const product=admin.locator('.review-card').filter({hasText:'Isolated test lamp'});
 await expect(product.locator('.review-gallery a')).toHaveCount(2);
 await product.locator('button[value=approve]').click();await expect(product.locator('.pill')).toHaveText('نشط');
 await seller.reload();await expect(seller.locator('tbody .pill')).toHaveText('نشط');
 await seller.locator('[data-action=stock]').click();await seller.locator('#new-stock').fill('7');await seller.locator('#stock-form button[type=submit]').click();
 await expect(seller.locator('tbody tr td').nth(3)).toHaveText('٧');

 // Configure two physical variants with independent SKUs, prices and stock.
 await seller.locator('button[data-action=variants]').click();
 await seller.locator('#variants-form [data-action=add-variant-row]').click();
 await seller.locator('#variants-form [data-action=add-variant-row]').click();
 const variantRows=seller.locator('.variant-editor-row');
 await expect(variantRows).toHaveCount(2);
 await variantRows.nth(0).locator('[name=variant_sku]').fill('BROWSER-LAMP-WARM');
 await variantRows.nth(0).locator('[name=variant_attributes]').fill('اللون=دافئ; المقاس=صغير');
 await variantRows.nth(0).locator('[name=variant_price]').fill('17.125');
 await variantRows.nth(0).locator('[name=variant_stock]').fill('3');
 await variantRows.nth(1).locator('[name=variant_sku]').fill('BROWSER-LAMP-COOL');
 await variantRows.nth(1).locator('[name=variant_attributes]').fill('اللون=بارد; المقاس=صغير');
 await variantRows.nth(1).locator('[name=variant_price]').fill('18.125');
 await variantRows.nth(1).locator('[name=variant_stock]').fill('4');
 const variantSave=seller.waitForResponse(r=>r.request().method()==='PUT'&&new URL(r.url()).pathname.includes('/variants'),{timeout:15000});
 await seller.locator('#variants-form button[type=submit]').click();
 const variantResult=await(await variantSave).json();
 expect(variantResult.status).toBe('pending');
 const sellerVariantRow=seller.locator('tbody tr').filter({hasText:'GENERATED-LAMP'});
 await expect(sellerVariantRow.locator('.pill')).toHaveText('قيد المراجعة');

 await admin.goto('/admin?tab=products');
 const variantProduct=admin.locator('.review-card').filter({hasText:'Isolated test lamp'});
 await expect(variantProduct).toContainText('BROWSER-LAMP-WARM');
 await expect(variantProduct).toContainText('BROWSER-LAMP-COOL');
 await variantProduct.locator('button[value=approve]').click();
 await expect(variantProduct.locator('.pill')).toHaveText('نشط');

 const buyerContext=await browser.newContext({locale:'ar-JO'});
 const buyer=await buyerContext.newPage();
 await login(buyer,'buyer',{destination:'/store/isolated-browser-store'});
 await buyer.locator('.product-card .product-name').click();
 await buyer.locator('#detail-variant').selectOption({index:1});
 await expect(buyer.locator('#variant-price-info')).toContainText('١٧٫١٢٥');
 const firstCartSave=buyer.waitForResponse(r=>r.request().method()==='PUT'&&new URL(r.url()).pathname==='/api/cart');
 await buyer.locator('[data-action=detail-add]').click();
 await firstCartSave;
 await expect(buyer.locator('#cart-count')).toHaveText('١');
 await buyer.locator('#detail-variant').selectOption({index:2});
 await expect(buyer.locator('#variant-price-info')).toContainText('١٨٫١٢٥');
 const secondCartSave=buyer.waitForResponse(r=>r.request().method()==='PUT'&&new URL(r.url()).pathname==='/api/cart');
 await buyer.locator('[data-action=detail-add]').click();
 await secondCartSave;
 await expect(buyer.locator('#cart-count')).toHaveText('٢');
 await buyer.goto('/cart');
 await expect(buyer.locator('.cart-item')).toHaveCount(2);
 await expect(buyer.locator('.cart-item').filter({hasText:'دافئ'})).toHaveCount(1);
 await expect(buyer.locator('.cart-item').filter({hasText:'بارد'})).toHaveCount(1);
 await accessible(buyer);
 await buyerContext.close();

 await seller.goto('/store/isolated-browser-store');await expect(seller.locator('.product-card')).toHaveCount(1);
 await seller.locator('.product-card .product-name').click();
 await expect(seller.locator('.detail-thumbnails button')).toHaveCount(2);
 const primarySrc=await seller.locator('#detail-image').getAttribute('src');
 await seller.locator('.detail-thumbnails button').nth(1).click();
 await expect(seller.locator('#detail-image')).not.toHaveAttribute('src',primarySrc);
 await accessible(seller);
 await admin.goto('/admin?tab=settings');await expect(admin.locator('#commission-percent')).toHaveValue('5');await accessible(admin);
 await admin.locator('#category-commission-id').selectOption('home');await expect(admin.locator('#category-percent')).toHaveValue('');
 await admin.locator('#category-percent').fill('7.25');await admin.locator('#category-commission-form button[type=submit]').click();
 await admin.locator('.toast').filter({hasText:'حُفظت قاعدة العمولة'}).waitFor();
 await admin.reload();await admin.locator('#category-commission-id').selectOption('home');await expect(admin.locator('#category-percent')).toHaveValue('7.25');
 await admin.locator('#category-commission-id').selectOption('audio');await expect(admin.locator('#category-percent')).toHaveValue('');
 await admin.locator('#category-commission-id').selectOption('home');await expect(admin.locator('#category-percent')).toHaveValue('7.25');
 await context.close();await adminContext.close();
});

test('email verification link is real and not consumed just by opening it',async({page})=>{
 await page.goto('/?lang=ar');await ready(page);await page.locator('.account-link').click();
 await page.locator('.auth-tabs [data-action=register]').click();
 const email='browser-'+randomBytes(6).toString('hex')+'@example.com',password=randomBytes(18).toString('hex')+'A9';
 await page.locator('#name').fill('Generated new user');await page.locator('#email').fill(email);await page.locator('#password').fill(password);
 await page.locator('#auth-form button[type=submit]').click();await expect(page).toHaveURL(/\/account$/);await expect(page.locator('.notice.warn')).toBeVisible();
 const code=`from email.parser import BytesParser\nfrom email import policy\nfrom pathlib import Path\nimport re,sys\nfor p in sorted(Path('data/browser-tests/mail').glob('*.eml'), reverse=True):\n m=BytesParser(policy=policy.default).parsebytes(p.read_bytes())\n if m['To']==sys.argv[1]:\n  body=m.get_body(preferencelist=('plain',)).get_content()\n  found=re.search(r'/verify-email\\?token=([A-Za-z0-9_-]+)',body)\n  if found:\n   print(found.group(1));break\n`;
 const token=execFileSync(PY,['-c',code,email],{encoding:'utf8'}).trim();expect(token.length).toBeGreaterThan(20);
 await page.goto('/verify-email?token='+token);await expect(page.locator('#verify-form')).toBeVisible();
 const before=await page.request.get('/api/auth/me');expect((await before.json()).user.email_verified_at).toBeNull();
 await page.locator('#verify-form button[type=submit]').click();await expect(page).toHaveURL(/\/account$/);
 await expect(page.locator('.notice.warn')).toHaveCount(0);
});

test('public PWA cache works offline; private data and writes are never cached',async({page,context})=>{
 await page.goto('/?lang=ar');await ready(page);
 await page.evaluate(async()=>{await navigator.serviceWorker.ready;if(!navigator.serviceWorker.controller)await new Promise(r=>navigator.serviceWorker.addEventListener('controllerchange',r,{once:true}));});
 await page.reload();await ready(page);await page.goto('/product/1');await page.locator('.detail-info h1').waitFor();
 await page.waitForFunction(async()=>{const c=await caches.open('shamaa-public-v1.0.5');return !!(await c.match('/api/products/1'))&&!!(await c.match('/api/bootstrap'));});
 const before=await page.request.get('/api/products/1');const stock=(await before.json()).product.stock;
  const messagesPage=await page.request.get('/messages');
  expect(messagesPage.headers()['cache-control']).toBe('no-store');
 await context.setOffline(true);await page.goto('/product/1');await expect(page.locator('.detail-info h1')).toContainText('سماعات');
 await expect(page.locator('#offline-banner')).toBeVisible();
 await page.locator('[data-action=detail-add]').click();await expect(page.locator('.toast.error')).toBeVisible();
 const cached=await page.evaluate(async()=>{const c=await caches.open('shamaa-public-v1.0.5');return(await c.keys()).map(r=>new URL(r.url).pathname);});
 expect(cached.filter(p=>/^\/(account|orders|checkout|cart|seller|admin|media|messages)/.test(p)||p.startsWith('/api/auth'))).toEqual([]);
 await page.goto('/account');await expect(page.locator('body')).toContainText('صفحات الحساب والطلبات لا تُخزّن');
  await page.goto('/messages');await expect(page.locator('body')).toContainText('صفحات الحساب والطلبات لا تُخزّن');
 await context.setOffline(false);const after=await page.request.get('/api/products/1');expect((await after.json()).product.stock).toBe(stock);
});

test('320px and tablet layouts, 200% text scaling, form labels and focus visibility',async({page})=>{
 for(const width of [320,768,1024]){
  await page.setViewportSize({width,height:900});await page.goto('/?lang=ar');await ready(page);await assertNoOverflow(page);
 }
 await page.setViewportSize({width:390,height:844});await page.goto('/search');await page.locator('.filters').waitFor();await accessible(page);
 await assertNoOverflow(page);
 await page.goto('/?lang=ar');await ready(page);await page.evaluate(()=>{document.documentElement.style.fontSize='200%';});await assertNoOverflow(page);
 await page.keyboard.press('Tab');const focused=await page.evaluate(()=>getComputedStyle(document.activeElement).outlineStyle);expect(focused).not.toBe('none');
});
