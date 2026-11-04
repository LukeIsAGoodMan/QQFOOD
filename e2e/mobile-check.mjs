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
  await dlg.locator('select').first().selectOption({ label: 'PK-TRAY 示例 包装托盘' })
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

await browser.close()
const failed = results.filter((r) => !r.pass)
console.log(`\n${results.length - failed.length}/${results.length} 通过；页面脚本错误 ${pageErrors.length} 个；截图在 e2e/screenshots/`)
pageErrors.forEach((e) => console.log('  ! ' + e))
process.exit(failed.length || pageErrors.length ? 1 : 0)
