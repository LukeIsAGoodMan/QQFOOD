// 手机端验收检查（PRD §10.6 MOB / T-MOB）
// 用本机 Chrome 模拟 iPhone 14（390×844，触屏），逐页检查布局，并以老板/现场/仓管/质量/主管身份走关键流程。
// 用法：先启动系统（./run.sh），然后 cd e2e && npm install && npm run mobile
// 可选：BASE=http://127.0.0.1:8597  DEVICE="Pixel 7"
// 注意：流程会写入演示数据（检查记录、调拨签收等），请在演示/测试库上运行。
import { chromium, devices } from 'playwright-core'
import { mkdirSync } from 'node:fs'

const BASE = process.env.BASE || 'http://127.0.0.1:8597'
const DEVICE = devices[process.env.DEVICE || 'iPhone 14']
const OUT = new URL('./screenshots/', import.meta.url).pathname
mkdirSync(OUT, { recursive: true })
const PHOTO = new URL('./fixture-photo.png', import.meta.url).pathname

const browser = await chromium.launch({ channel: 'chrome', headless: true })
const results = []
const pageErrors = []
const ok = (name, pass, detail = '') => { results.push({ name, pass, detail }); console.log(`${pass ? '✓' : '✗'} ${name}${detail ? ` — ${detail}` : ''}`) }

async function login(user, { pin = false } = {}) {
  const ctx = await browser.newContext({ ...DEVICE })
  const page = await ctx.newPage()
  page.on('pageerror', (e) => pageErrors.push(`[${user}] ${page.url()} ${e.message}`))
  page.on('dialog', (d) => d.accept(d.type() === 'prompt' ? '手机验收测试' : undefined))
  await page.goto(BASE)
  if (pin) {
    await page.getByRole('button', { name: '共享设备快速切换' }).tap()
    await page.locator('form button', { hasText: /员工|Hana|Gabriel/ }).first().tap()
    await page.fill('input[type=password]', '1234')
  } else {
    await page.fill('input[autocomplete=username]', user)
    await page.fill('input[type=password]', 'qq597demo')
  }
  await page.locator('form button.btn-primary').tap()
  await page.waitForTimeout(900)
  return page
}
const api = (page, path) => page.evaluate(async (p) => (await fetch(p, { headers: { Authorization: 'Bearer ' + localStorage.getItem('qq_token') } })).json(), path)
const shot = (page, name) => page.screenshot({ path: `${OUT}${name}.png`, fullPage: true })

/** Layout rules every screen must pass on a phone. */
async function checkLayout(page, label) {
  const m = await page.evaluate(() => {
    const vw = document.documentElement.clientWidth
    const inScroller = (el) => { for (let p = el.parentElement; p; p = p.parentElement) { const o = getComputedStyle(p).overflowX; if (o === 'auto' || o === 'scroll') return true } return false }
    const visible = (el) => { const b = el.getBoundingClientRect(); const cs = getComputedStyle(el); return b.width > 0 && b.height > 0 && cs.visibility !== 'hidden' && cs.display !== 'none' }
    const overflow = [...document.querySelectorAll('main *')].filter((el) => visible(el) && el.getBoundingClientRect().right > vw + 1 && !inScroller(el) && getComputedStyle(el).position !== 'fixed')
      .slice(0, 5).map((el) => `${el.tagName.toLowerCase()}.${String(el.className).split(' ').slice(0, 3).join('.')}`)
    const textInputs = [...document.querySelectorAll('input:not([type=checkbox]):not([type=radio]):not([type=file]), select, textarea')].filter(visible)
    const smallFont = textInputs.filter((el) => parseFloat(getComputedStyle(el).fontSize) < 16).length
    const targets = [...document.querySelectorAll('main button, main a.btn-primary, main a.btn-secondary, main a.btn-ghost, main select, header button, nav button, nav a')].filter(visible)
    const small = targets.filter((el) => { const b = el.getBoundingClientRect(); return b.height < 36 || b.width < 32 })
      .slice(0, 5).map((el) => `${el.tagName.toLowerCase()}「${(el.textContent || el.getAttribute('aria-label') || '').trim().slice(0, 12)}」${Math.round(el.getBoundingClientRect().width)}×${Math.round(el.getBoundingClientRect().height)}`)
    return { pageScroll: document.documentElement.scrollWidth > vw + 1, overflow, smallFont, small }
  })
  const pass = !m.pageScroll && !m.overflow.length && !m.smallFont && !m.small.length
  ok(`布局 ${label}`, pass, pass ? '' : JSON.stringify(m))
}

// ---------- 1. 老板：登录即总览，指标两列、可下钻，搜索可用 ----------
{
  const page = await login('boss')
  const h1 = await page.locator('h1').first().textContent()
  ok('老板登录后直接进入「老板总览」', h1?.includes('老板总览'), h1)
  const cards = await page.locator('main section.card').evaluateAll((els) => els.slice(0, 2).map((e) => Math.round(e.getBoundingClientRect().top)))
  ok('指标卡在手机上两列并排', cards.length === 2 && cards[0] === cards[1], JSON.stringify(cards))
  await checkLayout(page, '老板总览')
  await shot(page, 'boss-01-dashboard')
  await page.locator('main a', { hasText: '低于安全库存' }).first().tap()
  await page.waitForTimeout(900)
  ok('点指标可下钻到明细页', (await page.locator('h1').first().textContent())?.includes('库存'))
  await checkLayout(page, '库存与调拨（老板）')
  await shot(page, 'boss-02-drill-inventory')
  await page.getByRole('button', { name: '搜索' }).tap()
  await page.locator('input[type=search]:visible').first().fill('研磨机')
  await page.waitForTimeout(600)
  await shot(page, 'boss-03-search')
  await page.locator('button', { hasText: '研磨机' }).first().tap()
  await page.waitForTimeout(900)
  ok('手机搜索可打开记录', (await page.locator('h1').first().textContent())?.includes('研磨机'))
  await checkLayout(page, '记录详情')
  await shot(page, 'boss-04-record')
  await page.goto(`${BASE}/tasks?f=review`); await page.waitForTimeout(800)
  await checkLayout(page, '待核验任务')
  await page.locator('main a[href^="/t/"]').first().tap(); await page.waitForTimeout(900)
  await checkLayout(page, '任务详情（老板）')
  await shot(page, 'boss-05-task')
  await page.context().close()
}

// ---------- 2. 现场员工：PIN 登录、检查表拍照提交 ----------
let exceptionTaskId
{
  const page = await login('staff2', { pin: true })
  ok('共享设备 PIN 登录', (await page.locator('h1').first().textContent())?.length > 0)
  await checkLayout(page, '我的当班')
  await shot(page, 'staff-01-home')
  await page.locator('main a', { hasText: '开工卫生检查' }).first().tap(); await page.waitForTimeout(900)
  await checkLayout(page, '质量与食品安全（员工）')
  const items = page.locator('section:has(h2:has-text("开工卫生检查")) .divide-y > div')
  await items.nth(0).locator('button').first().tap()
  await items.nth(1).locator('button').nth(1).tap()
  await items.nth(1).locator('input[type=file][accept="image/*"]').setInputFiles(PHOTO)
  await page.waitForTimeout(900)
  await items.nth(2).locator('input[type=number]').fill('45')
  await page.locator('button.btn-primary', { hasText: /提交|Submit|Enviar/ }).tap(); await page.waitForTimeout(1000)
  await shot(page, 'staff-02-checklist-submitted')
  const ex = (await api(page, '/api/tasks?kind=exception&open_only=true')).find((t) => t.source.startsWith('checklist'))
  exceptionTaskId = ex?.id
  ok('检查不合格在手机上提交并生成整改任务', !!ex, ex?.title)
  await page.context().close()
}

// ---------- 3. 仓管：手机收货、调拨发运与部分签收 ----------
{
  const page = await login('wh')
  const inv = (await api(page, '/api/pages')).find((p) => p.name.includes('库存'))
  await page.goto(`${BASE}/p/${inv.id}`); await page.waitForTimeout(900)
  await page.locator('button', { hasText: '收货' }).first().tap(); await page.waitForTimeout(400)
  const dlg = page.locator('div[role=dialog]')
  await dlg.locator('select').first().selectOption({ label: '示例 包装托盘' })
  await dlg.locator('input[type=number]').first().fill('12')
  await dlg.locator('label:has-text("批次") input').fill('MOB-LOT-1')
  await checkLayout(page, '收货弹窗')
  await shot(page, 'wh-01-receive')
  await dlg.locator('button.btn-primary', { hasText: '提交' }).tap(); await page.waitForTimeout(900)
  ok('手机收货过账成功并提示上传签收依据', await dlg.locator('text=已过账').count() > 0)
  await dlg.locator('button[aria-label="关闭"]').tap(); await page.waitForTimeout(400)
  await page.locator('button', { hasText: 'TR-DEMO-001' }).tap(); await page.waitForTimeout(600)
  await page.locator('button', { hasText: '记录实际发运' }).tap(); await page.waitForTimeout(300)
  await page.locator('div[role=dialog] button', { hasText: '确认' }).tap(); await page.waitForTimeout(900)
  await page.locator('button', { hasText: '现场签收' }).tap(); await page.waitForTimeout(300)
  const tdlg = page.locator('div[role=dialog]')
  await tdlg.locator('input[placeholder="实收"]:visible').fill('45')
  await tdlg.locator('input[placeholder="损坏"]:visible').fill('2')
  await checkLayout(page, '调拨签收弹窗')
  await shot(page, 'wh-02-transfer-receive')
  await tdlg.locator('button', { hasText: '确认' }).tap(); await page.waitForTimeout(900)
  const tr = (await api(page, '/api/inv/transfers')).find((t) => t.code === 'TR-DEMO-001')
  ok('手机完成调拨发运与部分签收', tr?.status === 'partial', `status=${tr?.status} 在途=${tr?.lines[0].in_transit}`)
  await page.context().close()
}

// ---------- 4. 质量：手机隔离与放行（电子签名） ----------
{
  const page = await login('qa')
  await page.goto(`${BASE}/quality`); await page.waitForTimeout(1200)
  const lots = page.locator('section:has(h2:has-text("批次隔离与放行"))')
  await lots.locator('select').selectOption('all'); await page.waitForTimeout(300)
  await lots.locator('button', { hasText: '隔离' }).first().tap(); await page.waitForTimeout(400)
  await page.locator('div[role=dialog] input').first().fill('手机验收：包装破损')
  await checkLayout(page, '隔离签名弹窗')
  await shot(page, 'qa-01-quarantine')
  await page.locator('div[role=dialog] button', { hasText: '电子签名并隔离' }).tap(); await page.waitForTimeout(800)
  await lots.locator('select').selectOption('blocked'); await page.waitForTimeout(300)
  await lots.locator('button', { hasText: '放行' }).first().tap(); await page.waitForTimeout(400)
  const inputs = page.locator('div[role=dialog] input:not([type=file])')
  await inputs.nth(0).fill('复检合格'); await inputs.nth(1).fill('复检记录 #M1')
  await page.locator('div[role=dialog] button', { hasText: '电子签名并放行' }).tap(); await page.waitForTimeout(800)
  const ev = await api(page, '/api/audit?limit=20')
  ok('手机完成隔离与放行签名', ev.some((a) => a.action === 'quality.quarantine') && ev.some((a) => a.action === 'quality.release'))
  await page.context().close()
}

// ---------- 5. 主管：核验证据并关闭整改、资料中心确认变更、手机上建页面 ----------
{
  const page = await login('ops')
  await page.goto(`${BASE}/t/${exceptionTaskId}`); await page.waitForTimeout(900)
  await page.locator('input[type=file][accept="image/*"]').first().setInputFiles(PHOTO.replace('fixture-photo', 'fixture-photo'))
  await page.waitForTimeout(900)
  await shot(page, 'ops-01-task-evidence')
  // ops uploaded it → must be verified by someone else (分人复核); qa verifies on phone
  const qa = await login('qa')
  await qa.goto(`${BASE}/t/${exceptionTaskId}`); await qa.waitForTimeout(900)
  await qa.getByRole('button', { name: '核验', exact: true }).first().tap(); await qa.waitForTimeout(300)
  await qa.locator('div[role=dialog] button', { hasText: '通过' }).tap(); await qa.waitForTimeout(700)
  await qa.locator('button', { hasText: '核验通过并完成' }).tap(); await qa.waitForTimeout(800)
  const t = await api(qa, `/api/tasks/${exceptionTaskId}`)
  ok('手机核验证据并关闭整改任务', t.task.status === 'done', t.task.status)
  await shot(qa, 'qa-02-task-closed')
  await qa.context().close()

  // ingest on phone
  await page.goto(`${BASE}/ingest`); await page.waitForTimeout(700)
  await page.locator('input[type=file][accept="image/*"]').first().setInputFiles(PHOTO.replace('fixture-photo', 'fixture-photo'))
  await page.waitForTimeout(1200)
  await page.locator('main button:has(img), main button:has(div.h-10)').first().tap(); await page.waitForTimeout(800)
  await checkLayout(page, '资料详情')
  await page.locator('input[placeholder^="搜索要更新的记录"]').fill('Penske'); await page.waitForTimeout(700)
  await page.locator('button', { hasText: 'Penske' }).first().tap(); await page.waitForTimeout(300)
  await page.locator('main select').last().selectOption('next_step')
  await page.locator('input[placeholder="建议值"]').fill('手机验收：确认车源')
  await page.locator('button', { hasText: '提出变更' }).tap(); await page.waitForTimeout(800)
  await checkLayout(page, '变更确认')
  await shot(page, 'ops-02-changeset')
  await page.locator('button', { hasText: '确认应用所选' }).tap(); await page.waitForTimeout(800)
  const pen = (await api(page, '/api/tasks')).find((x) => x.title.startsWith('Penske'))
  ok('手机在资料中心确认并应用变更', pen?.next_step === '手机验收：确认车源', pen?.next_step)

  // page editor on phone (T-MOD-01 on mobile)
  await page.goto(`${BASE}/admin/pages`); await page.waitForTimeout(700)
  await page.locator('button', { hasText: '新建页面' }).tap(); await page.waitForTimeout(300)
  await page.fill('input[placeholder="例如：冷库巡检"]', '冷库巡检（手机建）')
  await page.locator('div[role=dialog] button', { hasText: '创建并编辑' }).tap(); await page.waitForTimeout(1300)
  await checkLayout(page, '页面编辑器-模块')
  await page.locator('button', { hasText: '添加模块' }).tap(); await page.waitForTimeout(300)
  await page.locator('div[role=dialog] button', { hasText: '说明文字' }).tap(); await page.waitForTimeout(500)
  await page.locator('textarea').last().fill('手机上添加的说明模块')
  await checkLayout(page, '页面编辑器-配置')
  await shot(page, 'ops-03-editor-config')
  await page.locator('.seg button', { hasText: '预览' }).tap(); await page.waitForTimeout(600)
  await shot(page, 'ops-04-editor-preview')
  await page.waitForTimeout(800)
  await page.locator('button', { hasText: '发布' }).first().tap(); await page.waitForTimeout(300)
  await page.locator('div[role=dialog] button', { hasText: '发布' }).tap(); await page.waitForTimeout(1000)
  const created = (await api(page, '/api/pages')).find((p) => p.name === '冷库巡检（手机建）')
  const layout = created && (await api(page, `/api/pages/${created.id}`)).layout
  ok('手机上新建页面、添加并配置模块、发布', !!layout && layout.modules.some((m) => m.config?.body === '手机上添加的说明模块'))

  // every remaining screen
  const pages = await api(page, '/api/pages')
  for (const p of pages) { await page.goto(`${BASE}/p/${p.id}`); await page.waitForTimeout(800); await checkLayout(page, `页面 ${p.name}`) }
  for (const r of ['/notebook', '/notebook?tab=contacts', '/notebook?tab=notes', '/ingest', '/evidence-review', '/admin/pages', '/admin/schema', '/admin/audit', '/me']) {
    await page.goto(BASE + r); await page.waitForTimeout(700); await checkLayout(page, r)
  }
  await page.context().close()
}

// ---------- 6. AI 助手（PRD §9.11）：老板在手机设置里开启 → 模拟 ChatGPT 授权并提出变更 → 手机打开上传链接传原件 → 在系统里确认 ----------
{
  const page = await login('boss')
  await page.goto(`${BASE}/me`); await page.waitForTimeout(800)
  await page.locator('button', { hasText: '开启 AI 助手访问' }).tap(); await page.waitForTimeout(800)
  await checkLayout(page, '我的设置 · AI 助手')
  await shot(page, 'agent-01-settings')
  ok('手机上开启 AI 助手访问', await page.locator('text=MCP 服务器地址').count() > 0)

  // simulate the ChatGPT connector: dynamic registration + PKCE + login/consent + token
  const crypto = await import('node:crypto')
  const req = page.context().request
  // local dead-end callback: the code is captured from the request, nothing external is contacted
  const redirect = 'http://127.0.0.1:9/oauth-callback'
  const reg = await (await req.post(`${BASE}/oauth/register`, { data: { client_name: 'ChatGPT', redirect_uris: [redirect] } })).json()
  const verifier = crypto.randomBytes(32).toString('base64url')
  const challenge = crypto.createHash('sha256').update(verifier).digest('base64url')
  const q = new URLSearchParams({ response_type: 'code', client_id: reg.client_id, redirect_uri: redirect, state: 's', code_challenge: challenge, code_challenge_method: 'S256' })
  await page.goto(`${BASE}/oauth/authorize?${q}`); await page.waitForTimeout(500)
  await shot(page, 'agent-02-authorize')
  await checkLayout(page, 'OAuth 授权页').catch(() => {})
  await page.fill('input[name=username]', 'boss'); await page.fill('input[name=password]', 'qq597demo')
  const [resp] = await Promise.all([page.waitForRequest((r) => r.url().startsWith(redirect)).catch(() => null), page.locator('button[type=submit]').tap()])
  const code = resp ? new URL(resp.url()).searchParams.get('code') : null
  const tok = await (await req.post(`${BASE}/oauth/token`, { form: { grant_type: 'authorization_code', code, redirect_uri: redirect, code_verifier: verifier, client_id: reg.client_id } })).json()
  ok('ChatGPT 连接器授权（DCR + PKCE）', !!tok.access_token)
  const mcp = async (name, args) => (await (await req.post(`${BASE}/mcp`, { headers: { Authorization: `Bearer ${tok.access_token}` }, data: { jsonrpc: '2.0', id: 1, method: 'tools/call', params: { name, arguments: args } } })).json()).result
  const task = (await mcp('search', { query: 'Penske' })).structuredContent.results.find((r) => r.type === 'task')
  const prop = (await mcp('propose_change', { target_type: 'task', target_id: task.id, source_summary: 'Wayne 微信截图：车周三下午到', changes: [{ field: '下一步', new_value: '周三下午提车并拍照' }] })).structuredContent
  ok('AI 提出的变更进入待确认、未直接生效', prop?.status === 'pending_confirmation')
  const link = (await mcp('request_upload_link', { target_type: 'task', target_id: task.id, purpose: '车辆照片' })).structuredContent.upload_url

  // open the upload link on a phone that is NOT signed in
  const anon = await (await browser.newContext({ ...DEVICE })).newPage()
  await anon.goto(link.replace(/^https?:\/\/[^/]+/, BASE)); await anon.waitForTimeout(800)
  await anon.locator('input[type=file][accept="image/*"]').setInputFiles(PHOTO); await anon.waitForTimeout(1200)
  await checkLayout(anon, '原件上传链接页')
  await shot(anon, 'agent-03-upload-link')
  ok('未登录手机通过链接上传原件', await anon.locator('text=已上传 1 个文件').count() > 0)
  await anon.context().close()

  // boss confirms in the app on the phone
  await page.goto(prop.confirm_url.replace(/^https?:\/\/[^/]+/, BASE)); await page.waitForTimeout(1000)
  await checkLayout(page, '资料中心 · AI 提交')
  await shot(page, 'agent-04-confirm')
  await page.locator('button', { hasText: '确认应用所选' }).tap(); await page.waitForTimeout(900)
  const after = (await mcp('get_task', { task_id: task.id })).structuredContent
  ok('确认后才生效，并带原件证据', after.next_step === '周三下午提车并拍照' && after.evidence_items.some((e) => e.purpose === '车辆照片'))
  await page.context().close()
}

// ---------- 7. 采购到收货（PUR）：采购选报价生成采购单 → 老板手机签批 → 下单 → 仓管手机收货拍签收单签名入账 ----------
{
  const buyer = await login('buyer')
  await buyer.goto(`${BASE}/purchasing`); await buyer.waitForTimeout(1200)
  await checkLayout(buyer, '采购页')
  await shot(buyer, 'pur-01-page')
  await buyer.locator('button', { hasText: '原料包材询价（示例）' }).tap(); await buyer.waitForTimeout(900)
  await checkLayout(buyer, '需求与报价对比')
  await shot(buyer, 'pur-02-quotes')
  await buyer.locator('button', { hasText: '选这家，生成采购单' }).first().tap(); await buyer.waitForTimeout(1200)
  await checkLayout(buyer, '采购单详情（草稿）')
  await buyer.locator('div[role=dialog] button', { hasText: /^提交/ }).tap(); await buyer.waitForTimeout(900)
  const poId = new URL(buyer.url()).searchParams.get('po')
  const po1 = await api(buyer, `/api/purchasing/pos/${poId}`)
  ok('超额度采购单进入待审批', po1.status === 'pending_approval', `${po1.code} $${po1.total}`)

  const boss = await login('boss')
  await boss.locator('main a', { hasText: '待审批采购' }).first().tap(); await boss.waitForTimeout(1000)
  await boss.locator('button', { hasText: po1.code }).tap(); await boss.waitForTimeout(900)
  await checkLayout(boss, '老板审批采购单')
  await shot(boss, 'pur-03-boss-approve')
  await boss.locator('button', { hasText: '签名批准' }).tap(); await boss.waitForTimeout(900)
  ok('老板在手机上签名批准', (await api(boss, `/api/purchasing/pos/${poId}`)).status === 'approved')
  await boss.context().close()

  await buyer.reload(); await buyer.waitForTimeout(1000)
  await buyer.locator('div[role=dialog] button', { hasText: '标记已下单' }).tap(); await buyer.waitForTimeout(800)
  await buyer.context().close()

  const wh = await login('wh')
  const before = (await api(wh, '/api/inv/stock')).rows.find((r) => r.sku_code === 'PK-TRAY' && r.warehouse.includes('597'))?.on_hand || 0
  await wh.goto(`${BASE}/purchasing?po=${poId}`); await wh.waitForTimeout(1200)
  await wh.locator('div[role=dialog] button', { hasText: '收货' }).last().tap(); await wh.waitForTimeout(600)
  const dlg = wh.locator('div[role=dialog]').last()
  const cards = dlg.locator('.rounded-lg.border')
  await cards.nth(0).locator('input[type=number]').first().fill('100')
  await cards.nth(0).locator('label:has-text("Lot") input').fill('PUR-MOB-1')
  await cards.nth(1).locator('input[type=number]').first().fill('46')
  await cards.nth(1).locator('label:has-text("Lot") input').fill('PUR-MOB-2')
  await checkLayout(wh, '收货弹窗（采购）')
  await shot(wh, 'pur-04-receive')
  await dlg.locator('button', { hasText: '提交收货' }).tap(); await wh.waitForTimeout(900)
  await dlg.locator('input[type=file][accept="image/*"]').setInputFiles(PHOTO); await wh.waitForTimeout(1200)
  await shot(wh, 'pur-05-signoff')
  await dlg.locator('button', { hasText: '签名入账' }).tap(); await wh.waitForTimeout(1000)
  const after = (await api(wh, '/api/inv/stock')).rows.find((r) => r.sku_code === 'PK-TRAY' && r.warehouse.includes('597'))?.on_hand || 0
  const po2 = await api(wh, `/api/purchasing/pos/${poId}`)
  ok('手机收货拍签收单后签名入账，库存增加', after === before + 100 && po2.status === 'partial', `PK-TRAY ${before}→${after}，状态 ${po2.status_label}`)
  await wh.reload(); await wh.waitForTimeout(1000)
  await checkLayout(wh, '采购单详情（部分到货）')
  await shot(wh, 'pur-06-partial')
  await wh.context().close()
}

// ---------- 8. admin 一个账号：能看到所有操作；补录一笔没有发票只有收据的日常采购 ----------
{
  const admin = await login('admin')
  await admin.goto(`${BASE}/purchasing`); await admin.waitForTimeout(1200)
  ok('admin 看得到新建与补录按钮', await admin.locator('button', { hasText: '补录已完成采购' }).count() > 0 && await admin.locator('button', { hasText: '新建采购单' }).count() > 0)
  await admin.locator('button', { hasText: '补录已完成采购' }).tap(); await admin.waitForTimeout(500)
  const dlg = admin.locator('div[role=dialog]')
  await dlg.locator('input[placeholder^="如 Costco"]').fill('Costco')
  await dlg.locator('input[placeholder^="品名"]').fill('垃圾袋')
  await dlg.locator('input[placeholder="数量"]').fill('3')
  await dlg.locator('input[placeholder="单价 $"]').fill('12.99')
  await checkLayout(admin, '补录采购弹窗')
  await shot(admin, 'pur-07-quick')
  await dlg.locator('button', { hasText: '保存' }).tap(); await admin.waitForTimeout(900)
  await dlg.locator('input[type=file][accept="image/*"]').setInputFiles(PHOTO); await admin.waitForTimeout(1200)
  await dlg.locator('button', { hasText: '查看采购单' }).tap(); await admin.waitForTimeout(1000)
  const qid = new URL(admin.url()).searchParams.get('po')
  const q = await api(admin, `/api/purchasing/pos/${qid}`)
  ok('补录采购：已到货、已付款待核对、收据已上传', q.status === 'received' && q.payment_status === 'paid_pending' && q.doc_type === 'receipt' && !q.missing_doc, `${q.code} ${q.payment_label}`)
  await checkLayout(admin, '补录采购详情')
  await shot(admin, 'pur-08-quick-detail')
  const newRq = await admin.evaluate(async () => (await fetch('/api/purchasing/requisitions', { method: 'POST', headers: { 'Content-Type': 'application/json', Authorization: 'Bearer ' + localStorage.getItem('qq_token') },
    body: JSON.stringify({ title: '手机验收需求', margin_pct: 10, lines: [{ item_name: '手套', base_qty: 10, unit: '盒' }] }) })).json())
  await admin.goto(`${BASE}/purchasing?rq=${newRq.id}`); await admin.waitForTimeout(1000)
  ok('admin 在报价对比里看得到“新版本/报价”按钮', await admin.locator('button', { hasText: '新版本' }).count() > 0)
  await admin.context().close()
}

// ---------- 9. 多伦多调入（不走采购）：手机新建调入（对方已发出→在途）→ 到货签收 ----------
{
  const admin = await login('admin')
  const row = async () => (await api(admin, '/api/inv/stock')).rows.find((r) => r.sku_code === 'RM-SPICE' && r.warehouse.includes('597')) || { on_hand: 0, in_transit: 0 }
  const before = await row()
  await admin.goto(`${BASE}/inventory`); await admin.waitForTimeout(1200)
  await admin.locator('button', { hasText: '新建调拨 / 调入' }).tap(); await admin.waitForTimeout(500)
  const dlg = admin.locator('div[role=dialog]')
  ok('默认从多伦多/温哥华调入', (await dlg.locator('div.font-semibold').first().textContent())?.includes('调入'))
  await dlg.locator('select').nth(2).selectOption({ label: '示例 香料包' })
  await dlg.locator('input[placeholder^="Lot"]').fill('TOR-S-01')
  await dlg.locator('input[placeholder="数量"]').fill('30')
  await checkLayout(admin, '新建调入弹窗')
  await shot(admin, 'inb-01-new')
  await dlg.locator('button', { hasText: '保存' }).tap(); await admin.waitForTimeout(900)
  const mid = await row()
  ok('调入保存后直接在途，不扣来源地', mid.in_transit === before.in_transit + 30 && mid.on_hand === before.on_hand, `在途 ${before.in_transit}→${mid.in_transit}`)
  await admin.locator('button:has(span.font-mono):has-text("调入")').first().tap(); await admin.waitForTimeout(800)
  await admin.locator('div[role=dialog] button', { hasText: '现场签收' }).tap(); await admin.waitForTimeout(300)
  await admin.locator('div[role=dialog] input[placeholder="实收"]:visible').fill('30')
  await shot(admin, 'inb-02-receive')
  await admin.locator('div[role=dialog] button', { hasText: '确认' }).tap(); await admin.waitForTimeout(900)
  const after = await row()
  ok('调入签收后入库', after.on_hand === before.on_hand + 30 && after.in_transit === before.in_transit, `597 香料包 ${before.on_hand}→${after.on_hand}`)
  await admin.goto(`${BASE}/admin/users`); await admin.waitForTimeout(800)
  await checkLayout(admin, '用户与角色（团队模式）')
  await shot(admin, 'team-mode')
  ok('默认是精简团队模式', (await api(admin, '/api/settings/org')).mode === 'simple')
  await admin.context().close()
}

// ---------- 10. ChatGPT 批量导入：SKU 与期初数量 → 老板在手机上逐行确认 ----------
{
  const page = await login('boss')
  const crypto = await import('node:crypto')
  const req = page.context().request
  await page.evaluate(async () => fetch('/api/agent/enable', { method: 'POST', headers: { 'Content-Type': 'application/json', Authorization: 'Bearer ' + localStorage.getItem('qq_token') }, body: JSON.stringify({ enabled: true }) }))
  const redirect = 'http://127.0.0.1:9/oauth-callback'
  const reg = await (await req.post(`${BASE}/oauth/register`, { data: { client_name: 'ChatGPT', redirect_uris: [redirect] } })).json()
  const verifier = crypto.randomBytes(32).toString('base64url')
  const challenge = crypto.createHash('sha256').update(verifier).digest('base64url')
  const auth = await req.post(`${BASE}/oauth/authorize`, { form: { response_type: 'code', client_id: reg.client_id, redirect_uri: redirect, code_challenge: challenge, code_challenge_method: 'S256', username: 'boss', password: 'qq597demo', scope_write: 'on' }, maxRedirects: 0 })
  const code = new URL(auth.headers()['location']).searchParams.get('code')
  const tok = await (await req.post(`${BASE}/oauth/token`, { form: { grant_type: 'authorization_code', code, redirect_uri: redirect, code_verifier: verifier, client_id: reg.client_id } })).json()
  const mcp = async (name, args) => (await (await req.post(`${BASE}/mcp`, { headers: { Authorization: `Bearer ${tok.access_token}` }, data: { jsonrpc: '2.0', id: 1, method: 'tools/call', params: { name, arguments: args } } })).json()).result.structuredContent
  const skus = await mcp('propose_skus', { source_summary: '我和 ChatGPT 做的库存表', rows: [
    { code: 'TT-DUMP-PORK', name: '猪肉白菜水饺 1kg', category: '成品', base_unit: '箱', conversions: { '袋': 12 }, safety_stock: { WH597: 30 } },
    { code: 'TT-DUMP-VEG', name: '素三鲜水饺 1kg', category: '成品', base_unit: '箱', conversions: { '袋': 12 } },
    { code: 'DUP-1', name: '示例 香料包' }] })
  ok('ChatGPT 提交 SKU 批次（含疑似重复）', skus.summary.new === 2 && skus.summary.duplicate === 1, JSON.stringify(skus.summary))
  await page.goto(skus.confirm_url.replace(/^https?:\/\/[^/]+/, BASE)); await page.waitForTimeout(1200)
  await checkLayout(page, '批量确认（SKU）')
  await shot(page, 'bat-01-skus')
  ok('疑似重复默认不勾选', !(await page.locator('label:has-text("DUP-1") input[type=checkbox]').isChecked()))
  await page.locator('button', { hasText: '确认所选' }).tap(); await page.waitForTimeout(900)
  const counts = await mcp('propose_stock_counts', { source_summary: '11/5 实盘', rows: [
    { sku_code: 'TT-DUMP-PORK', lot_code: 'P1105', qty: 48 }, { sku_code: 'TT-DUMP-VEG', qty: 36, unit: '袋' }] })
  await page.goto(counts.confirm_url.replace(/^https?:\/\/[^/]+/, BASE)); await page.waitForTimeout(1200)
  await checkLayout(page, '批量确认（期初数量）')
  await shot(page, 'bat-02-stock')
  await page.locator('button', { hasText: '确认所选' }).tap(); await page.waitForTimeout(900)
  const rows = (await api(page, '/api/inv/stock')).rows
  const pork = rows.find((r) => r.sku_code === 'TT-DUMP-PORK'), veg = rows.find((r) => r.sku_code === 'TT-DUMP-VEG')
  ok('确认后库存写入（36 袋 = 3 箱）', pork?.on_hand === 48 && veg?.on_hand === 3, `猪肉 ${pork?.on_hand} / 素三鲜 ${veg?.on_hand}`)
  await page.context().close()
}

// ---------- 11. 大统华订单：ChatGPT 录 PO → 手机确认（给未配对行选 SKU）→ 称重打标签 → 选箱发货 → 发货单 → 登记送达 ----------
{
  const page = await login('boss')
  const crypto = await import('node:crypto')
  const req = page.context().request
  const redirect = 'http://127.0.0.1:9/oauth-callback'
  const reg = await (await req.post(`${BASE}/oauth/register`, { data: { client_name: 'ChatGPT', redirect_uris: [redirect] } })).json()
  const verifier = crypto.randomBytes(32).toString('base64url')
  const challenge = crypto.createHash('sha256').update(verifier).digest('base64url')
  const auth = await req.post(`${BASE}/oauth/authorize`, { form: { response_type: 'code', client_id: reg.client_id, redirect_uri: redirect, code_challenge: challenge, code_challenge_method: 'S256', username: 'boss', password: 'qq597demo', scope_write: 'on' }, maxRedirects: 0 })
  const code = new URL(auth.headers()['location']).searchParams.get('code')
  const tok = await (await req.post(`${BASE}/oauth/token`, { form: { grant_type: 'authorization_code', code, redirect_uri: redirect, code_verifier: verifier, client_id: reg.client_id } })).json()
  const mcp = async (name, args) => (await (await req.post(`${BASE}/mcp`, { headers: { Authorization: `Bearer ${tok.access_token}` }, data: { jsonrpc: '2.0', id: 1, method: 'tools/call', params: { name, arguments: args } } })).json()).result.structuredContent
  const sys = await mcp('propose_order', { po_no: '623030106', po_date: '2026-10-30', required_date: '2026-11-04', category: 'General', site: 'Chino Hills Store - 0KITCHEN DELI', source_summary: 'PDF 第 2 页',
    lines: [{ item_no: '539121', description: 'CHILI POWDER PAPA CHICKEN', pack_size: '10gx1000/1.00', unit: 'case', qty: 6, price: 145 }] })
  ok('系统 PO 提交：未配对行可确认', sys.summary.unmapped === 1, JSON.stringify(sys.summary))
  await page.goto(sys.confirm_url.replace(/^https?:\/\/[^/]+/, BASE)); await page.waitForTimeout(1200)
  await page.locator('main select').filter({ hasText: '选我们的 SKU' }).first().selectOption({ label: '示例 成品汤料' })
  await checkLayout(page, '订单确认（选 SKU）')
  await shot(page, 'ord-01-confirm')
  await page.locator('button', { hasText: '确认所选' }).tap(); await page.waitForTimeout(1000)
  const ck = await mcp('propose_order', { po_no: 'CH-CK', po_date: '2026-11-05', required_date: '2026-11-10', category: '中央厨房', site: 'Chino Hills', source_summary: '中央厨房手写 PO',
    lines: [{ description: 'Chicken Leg-Diced 切粒鸡丁', unit: 'lb', qty: 300, price: 3.6, sku_code: 'FG-CHK-DICE' }] })
  await page.goto(ck.confirm_url.replace(/^https?:\/\/[^/]+/, BASE)); await page.waitForTimeout(1000)
  await page.locator('button', { hasText: '确认所选' }).tap(); await page.waitForTimeout(1000)
  const orders = await api(page, '/api/orders?open_only=true')
  const ckOrder = orders.find((o) => o.po_no === 'CH-CK'), sysOrder = orders.find((o) => o.po_no === '623030106')
  ok('两张 PO 都已建成订单、全部配对', !!ckOrder && !!sysOrder && sysOrder.unmapped === 0)
  // weigh 6 boxes on the phone
  await page.goto(`${BASE}/orders`); await page.waitForTimeout(1200)
  await checkLayout(page, '订单页')
  await shot(page, 'ord-02-list')
  const boxes = page.locator('section:has(h2:has-text("称重打标签"))')
  await boxes.locator('select').first().selectOption({ label: '示例 切粒鸡丁' })
  await boxes.locator('input[placeholder^="如 CK"]').fill('CK1106')
  for (const w of ['50.2', '49.8', '50', '50.4', '49.6', '50.1']) { await boxes.locator('input[placeholder="50.2"]').fill(w); await boxes.locator('button', { hasText: '加一箱' }).tap() }
  await checkLayout(page, '称重打标签')
  await boxes.locator('button', { hasText: '生成 6 箱' }).tap(); await page.waitForTimeout(1000)
  await shot(page, 'ord-03-labels')
  ok('称重 6 箱并生成标签', await page.locator('div[role=dialog]:has-text("箱标签（6）")').count() > 0)
  await page.locator('div[role=dialog] button[aria-label="关闭"]').tap(); await page.waitForTimeout(400)
  // ship by selecting boxes
  await page.goto(`${BASE}/orders?so=${ckOrder.id}`); await page.waitForTimeout(1200)
  await checkLayout(page, '订单详情（称重）')
  await page.locator('div[role=dialog] button', { hasText: '发货' }).first().tap(); await page.waitForTimeout(600)
  const ship = page.locator('div[role=dialog]').last()
  for (const chip of await ship.locator('button.chip').all()) await chip.tap()
  await checkLayout(page, '发货（选箱）')
  await shot(page, 'ord-04-ship')
  await ship.locator('button', { hasText: '确认发货' }).tap(); await page.waitForTimeout(1000)
  const shipped = await api(page, `/api/orders/${ckOrder.id}`)
  ok('选 6 箱发货，按实重 300.1 lb 计', shipped.status === 'shipped' && shipped.lines[0].qty_shipped === 300.1, `${shipped.status_label} ${shipped.lines[0].qty_shipped} lb ${shipped.shipped_amount}`)
  await page.locator('div[role=dialog] button', { hasText: '打印发货单' }).tap(); await page.waitForTimeout(500)
  await checkLayout(page, '发货单')
  await shot(page, 'ord-05-slip')
  await page.locator('div[role=dialog]').last().locator('button[aria-label="关闭"]').tap(); await page.waitForTimeout(300)
  await page.locator('div[role=dialog] button', { hasText: '登记送达' }).tap(); await page.waitForTimeout(500)
  await page.locator('div[role=dialog]').last().locator('input[placeholder^="没有签收单"], label:has-text("没有签收单") input').first().fill('司机在门店拍照，签收单稍后补')
  await page.locator('div[role=dialog]').last().locator('button', { hasText: '确认送达' }).tap(); await page.waitForTimeout(900)
  ok('手机登记送达（例外：签收单后补）', (await api(page, `/api/orders/${ckOrder.id}`)).status === 'delivered')
  await page.context().close()
}

// ---------- 12. 切配生产：300 lb 咕咾肉 → 备料按 65 lb/箱买 5 箱 → 生成采购草稿 → 原料到货 → 生产完工（逐箱称重，剩余算损耗）----------
{
  const page = await login('admin')
  const call = (path, body, method = 'POST') => page.evaluate(async ([p, b, m]) => (await fetch(p, { method: m, headers: { 'Content-Type': 'application/json', Authorization: 'Bearer ' + localStorage.getItem('qq_token') }, body: b ? JSON.stringify(b) : undefined })).json(), [path, body, method])
  const m = await api(page, '/api/inv/master')
  const S = Object.fromEntries(m.skus.map((s) => [s.code, s.id])), W = Object.fromEntries(m.warehouses.map((w) => [w.code, w.id]))
  const tnt = (await api(page, '/api/orders/customers')).find((c) => c.code === 'TNT')
  await call('/api/orders', { customer_id: tnt.id, site_id: tnt.sites[0].id, po_no: 'CH-CK', po_date: '2026-11-07', required_date: '2026-11-11',
    lines: [{ description: 'Pork Shoulder Butt-Diced 切粒咕咾肉', unit: 'lb', qty: 300, price: 3.68, sku_id: S['FG-PORK-DICE'] }] })
  await page.goto(`${BASE}/production`); await page.waitForTimeout(1500)
  await checkLayout(page, '生产 · 备料计划')
  await shot(page, 'prd-01-plan')
  ok('备料建议 梅头肉 5 箱 × 65 = 325 lb', await page.locator('text=5 箱 × 65 = 325').count() > 0)
  await page.locator('input[placeholder="供应商（可空）"]').fill('梅头肉供应商')
  await page.locator('button', { hasText: '生成采购单草稿' }).tap(); await page.waitForTimeout(1500)
  ok('一键生成采购单草稿', new URL(page.url()).searchParams.get('po') !== null)
  await call('/api/inv/receive', { idempotency_key: crypto.randomUUID(), sku_id: S['RM-PORK-BUTT'], warehouse_id: W['WH597'], qty: 325, lot_code: 'PORK-1107' })
  await page.goto(`${BASE}/production`); await page.waitForTimeout(1500)
  await page.locator('button', { hasText: '去生产' }).first().tap(); await page.waitForTimeout(900)
  const dlg = page.locator('div[role=dialog]')
  const inputs = dlg.locator('input[aria-label^="第"]')
  const ws = ['50.2', '49.9', '50.1', '50.3', '49.8', '50.0']
  for (let i = 0; i < ws.length; i++) await inputs.nth(i).fill(ws[i])
  await checkLayout(page, '生产完工（称重）')
  await shot(page, 'prd-02-run')
  await dlg.locator('label:has-text("完工即放行") input').check()
  await dlg.locator('button', { hasText: '完工并打印标签' }).tap(); await page.waitForTimeout(1200)
  const runs = await api(page, '/api/production/runs')
  const r = runs.find((x) => x.product_code === 'FG-PORK-DICE')
  ok('生产记录：投入 325、产出 300.3、损耗 24.7', r && r.consumed_qty === 325 && r.output_qty === 300.3 && r.loss_qty === 24.7, r && `出成率 ${r.yield_pct}%`)
  ok('完工后打印 6 箱标签', await page.locator('div[role=dialog]:has-text("箱标签（6）")').count() > 0)
  await page.context().close()
}

// ---------- 13. 一物多码：Supreme 外库 BOL 用他们的代码 → 手机确认时选一次 SKU（记住）→ 账面不够填原因 → 商品对照加 QuickBooks 品名 → 导出发票 ----------
{
  const page = await login('admin')
  const call = (path, body, method = 'POST') => page.evaluate(async ([p, b, m]) => (await fetch(p, { method: m, headers: { 'Content-Type': 'application/json', Authorization: 'Bearer ' + localStorage.getItem('qq_token') }, body: b ? JSON.stringify(b) : undefined })).json(), [path, body, method])
  const m = await api(page, '/api/inv/master')
  const S = Object.fromEntries(m.skus.map((s) => [s.code, s.id]))
  ok('Supreme 外库已在系统里管账', m.warehouses.some((w) => w.code === 'SUPREME' && w.tracked && w.kind === 'external'))
  const b = await call('/api/batches', { kind: 'inbound', meta: { from_code: 'SUPREME', note: 'BOL S26-39513' }, source_note: 'BOL 照片', rows: [
    { party_code: 'Vatee-001', party_name: 'FRZ CANTONESE FRIED DOUGH STICK', lot_code: '2610-2228', qty: 40 }] })
  await page.goto(`${BASE}/ingest?batch=${b.id}`); await page.waitForTimeout(1200)
  await page.locator('main select').filter({ hasText: '选我们的 SKU' }).first().selectOption({ label: '示例 成品汤料（T&T 539121）' })
  await page.locator('button', { hasText: '确认所选' }).tap(); await page.waitForTimeout(900)
  const reason = page.locator('input[placeholder^="补记原因"]')
  ok('外库账面不够时提示填原因（例外）', await reason.count() > 0)
  await reason.fill('Supreme 期初还没导入')
  await checkLayout(page, '调入确认（外库代码 + 补记原因）')
  await shot(page, 'als-01-bol')
  await page.locator('button', { hasText: '确认所选' }).tap(); await page.waitForTimeout(1000)
  const done = await api(page, `/api/batches/${b.id}`)
  const al = await api(page, `/api/aliases?sku_id=${S['FG-SOUP']}`)
  ok('确认后生成调入单，并记住 Vatee-001 = FG-SOUP', !!done.result?.transfer_code && al.some((a) => a.code === 'Vatee-001' && a.party === 'SUPREME'), done.result?.transfer_code)
  // 商品对照：用对方编码搜到我们的 SKU，加 QuickBooks 品名
  await page.goto(`${BASE}/inventory`); await page.waitForTimeout(1200)
  await page.locator('button', { hasText: '商品对照' }).first().tap(); await page.waitForTimeout(800)
  const dlg = page.locator('div[role=dialog]')
  await dlg.locator('input[type=search]').fill('Vatee'); await page.waitForTimeout(300)
  ok('用外库代码搜到我们的商品（按品名显示）', await dlg.locator('button', { hasText: '示例 成品汤料' }).count() === 1)
  await checkLayout(page, '商品对照列表')
  await dlg.locator('button', { hasText: '示例 成品汤料' }).first().tap(); await page.waitForTimeout(500)
  await dlg.locator('button', { hasText: '加一个对照' }).tap()
  await dlg.locator('select').first().selectOption({ label: 'QuickBooks' })
  await dlg.locator('label:has-text("QuickBooks 品名") input').fill('汤料 Soup Base')
  await checkLayout(page, '商品对照（加 QuickBooks 品名）')
  await shot(page, 'als-02-aliases')
  await dlg.locator('button', { hasText: '保存' }).tap(); await page.waitForTimeout(800)
  ok('保存 QuickBooks 品名', (await api(page, `/api/aliases?sku_id=${S['FG-SOUP']}`)).some((a) => a.party_kind === 'accounting' && a.name === '汤料 Soup Base'))
  await dlg.locator('button[aria-label="关闭"]').first().tap(); await page.waitForTimeout(300)
  // QuickBooks 发票：已送达的中央厨房订单
  const shippedOrder = (await api(page, '/api/orders?status=shipped,delivered')).find((o) => o.shipped_amount > 0)
  await page.goto(`${BASE}/orders?so=${shippedOrder.id}`); await page.waitForTimeout(1200)
  await page.locator('div[role=dialog] button', { hasText: 'QuickBooks 发票' }).tap(); await page.waitForTimeout(600)
  const qb = page.locator('div[role=dialog]').last()
  await qb.locator('button', { hasText: '生成' }).tap(); await page.waitForTimeout(800)
  ok('生成 QuickBooks 发票 CSV（缺品名会提示）', await qb.locator('text=合计').count() > 0 && await qb.locator('text=还没记 QuickBooks 品名').count() > 0)
  ok('可下载 CSV', await qb.locator('button', { hasText: /下载 QQUSA\d{8}\.csv/ }).count() === 1)
  await checkLayout(page, 'QuickBooks 发票导出')
  await shot(page, 'als-03-quickbooks')
  // 先送货（没有 PO、没有大统华 Item#）→ PO 到了，确认页提示“补到之前那张送货单”
  const tnt = (await api(page, '/api/orders/customers')).find((c) => c.code === 'TNT')
  const early = await call('/api/orders', { customer_id: tnt.id, site_id: tnt.sites[0].id, required_date: '2026-11-09',
    lines: [{ description: '示例 包装托盘', unit: '箱', qty: 2, sku_id: S['PK-TRAY'] }] })
  await page.goto(`${BASE}/orders?so=${early.id}`); await page.waitForTimeout(1200)
  ok('没有 PO 的送货单可以录入，显示「PO 待补」', await page.locator('div[role=dialog] >> text=PO 待补').count() > 0 && await page.locator('div[role=dialog] button', { hasText: '补 PO 号' }).count() === 1)
  await checkLayout(page, '订单详情（PO 待补）')
  const pb = await call('/api/batches', { kind: 'order', meta: { customer: 'TNT', site: 'Chino Hills', po_no: '623039999', po_date: '2026-11-08' }, source_note: 'PO 到了',
    rows: [{ item_no: '520001', description: 'PACKING TRAY 示例 包装托盘', unit: 'case', qty: 2, price: 12 }] })
  await page.goto(`${BASE}/ingest?batch=${pb.id}`); await page.waitForTimeout(1200)
  ok('PO 确认页提示补到之前的送货单', await page.locator('text=这张 PO 是给它补的吗').count() > 0)
  await checkLayout(page, 'PO 补到送货单')
  await shot(page, 'als-04-fill-po')
  await page.locator('button', { hasText: '确认所选' }).tap(); await page.waitForTimeout(1000)
  const filled = await api(page, `/api/orders/${early.id}`)
  ok('PO 号、大统华 Item#、单价补到原单', filled.po_no === '623039999' && filled.lines[0].item_no === '520001' && filled.lines[0].price === 12, `${filled.po_no} ${filled.lines[0].item_no}`)
  await page.context().close()
}

// ---------- 14. 库存分类：干货/冻货/原料肉/原料菜/生产用品/公司用品/生产机器 → 按分类筛选 → 未分类的点一下补上 ----------
{
  const page = await login('admin')
  const call = (path, body, method = 'POST') => page.evaluate(async ([p, b, m]) => (await fetch(p, { method: m, headers: { 'Content-Type': 'application/json', Authorization: 'Bearer ' + localStorage.getItem('qq_token') }, body: b ? JSON.stringify(b) : undefined })).json(), [path, body, method])
  const glove = await call('/api/inv/skus', { name: '示例 一次性手套', base_unit: '盒' })
  await page.goto(`${BASE}/inventory`); await page.waitForTimeout(1500)
  const visible = (txt) => page.evaluate((t) => [...document.querySelectorAll('main *')].filter((e) => e.children.length === 0 && e.textContent.trim() === t && e.offsetParent !== null).length, txt)
  ok('库存页有分类筛选', await page.locator('[aria-label="按分类筛选"] button', { hasText: '冻货' }).count() === 1 && await page.locator('[aria-label="按分类筛选"] button', { hasText: '干货' }).count() === 1)
  await page.locator('[aria-label="按分类筛选"] button', { hasText: '冻货' }).tap(); await page.waitForTimeout(400)
  ok('点「冻货」只看冻货', await visible('示例 切粒鸡丁') > 0 && await visible('示例 成品汤料') === 0)
  await checkLayout(page, '库存按分类筛选')
  await shot(page, 'cat-01-filter')
  ok('有未分类商品时提示去分类', await page.locator('button', { hasText: '还没分类' }).count() === 1)
  await page.locator('button', { hasText: '还没分类' }).tap(); await page.waitForTimeout(600)
  const dlg = page.locator('div[role=dialog]').last()
  const card = dlg.locator('[role=group][aria-label="示例 一次性手套"]')
  await checkLayout(page, '整理分类')
  await shot(page, 'cat-02-classify')
  await card.locator('button', { hasText: '生产用品' }).tap(); await page.waitForTimeout(700)
  const after = (await api(page, '/api/inv/master')).skus.find((s) => s.id === glove.id)
  ok('点一下就归类保存', after?.category === '生产用品', after?.category)
  await dlg.locator('button', { hasText: '完成' }).tap(); await page.waitForTimeout(800)
  await page.context().close()
}

await browser.close()
const failed = results.filter((r) => !r.pass)
console.log(`\n${results.length - failed.length}/${results.length} 通过；页面脚本错误 ${pageErrors.length} 个；截图在 e2e/screenshots/`)
pageErrors.forEach((e) => console.log('  ! ' + e))
process.exit(failed.length || pageErrors.length ? 1 : 0)
