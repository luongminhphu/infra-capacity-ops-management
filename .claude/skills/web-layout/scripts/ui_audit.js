// ICO web layout audit: misaligned form rows, clipped text, over-long labels / buttons, page horizontal scroll.
// usage: ICO_PASS=... node .claude/skills/web-layout/scripts/ui_audit.js [--out dir] [--widths 1440,390]
//        [--theme dark|light] [--pages home,sizing,...] [--json]
// Needs a running demo (run_demo.sh) and Playwright (require('playwright')).
// Prints one line per page with findings, then the details; exit code 1 when a page scrolls horizontally.
const { chromium } = require('playwright');
const fs = require('fs');
const path = require('path');

const ALL = ['home', 'home/leader', 'capacity', 'capacity/compliance', 'capacity/trends', 'backup', 'backup/clients', 'backup/jobs',
  'backup/schedule', 'backup/coverage', 'backup/size', 'backup/storage', 'agenda', 'simulate', 'simulate/planning', 'simulate/license',
  'sizing', 'assets', 'assets/hardware', 'assets/os', 'license', 'license/snapshots', 'budget', 'budget/opex', 'budget/capex', 'budget/plan',
  'procure', 'procure/quotes', 'procure/bom', 'procure/renovations', 'contracts', 'contracts/payments', 'docs', 'docs/reports', 'docs/archive',
  'settings', 'settings/sources', 'settings/backup', 'settings/features', 'settings/spend', 'settings/ai', 'settings/users', 'settings/auth', 'settings/brand'];

const arg = (name, def) => { const i = process.argv.indexOf(`--${name}`); return i > 0 ? process.argv[i + 1] : def; };
const BASE = process.env.ICO_URL || 'http://localhost:8765';
const USER = process.env.ICO_USER || 'admin';
const PASS = process.env.ICO_PASS;
if (!PASS) { console.error('set ICO_PASS (and ICO_USER / ICO_URL if not admin @ localhost:8765)'); process.exit(2); }
const out = arg('out', null), theme = arg('theme', 'dark');
const widths = arg('widths', '1440,390').split(',').map(Number);
const pages = arg('pages', '') ? arg('pages').split(',') : ALL;

// Runs in the page. Thresholds: control tops in one row differ > 3 px; button / pill / tab > 1 text line; label / header > 2 lines.
function measure() {
  const main = document.querySelector('main');
  const vis = (e) => e.offsetParent !== null && e.getBoundingClientRect().width > 0 && getComputedStyle(e).visibility !== 'hidden';
  const name = (e) => `${e.tagName.toLowerCase()}${e.id ? '#' + e.id : ''}${typeof e.className === 'string' && e.className ? '.' + e.className.trim().split(/\s+/).slice(0, 2).join('.') : ''}`;
  const txt = (e) => (e.textContent || '').trim().replace(/\s+/g, ' ').slice(0, 70);
  const ctrlOf = (f) => f.matches('input,select,textarea') ? f : f.querySelector('input:not([type=checkbox]):not([type=radio]):not([type=hidden]):not(.sr-only), select, textarea');
  const rowsOff = [];
  main.querySelectorAll('*').forEach(g => {
    if (!vis(g)) return;
    const kids = [...g.children].filter(vis).filter(k => k.matches('.field, .form-field') || (k.matches('label') && ctrlOf(k)));
    if (kids.length < 2) return;
    const byTop = {};
    kids.forEach(k => { const t = Math.round(k.getBoundingClientRect().top / 4) * 4; (byTop[t] = byTop[t] || []).push(k); });
    Object.values(byTop).forEach(row => {
      const cs = row.map(ctrlOf).filter(c => c && vis(c));
      if (cs.length < 2) return;
      // heights compared among one-line controls only (a multi-select / textarea is taller by design)
      const tops = cs.map(c => Math.round(c.getBoundingClientRect().top));
      const hs = cs.filter(c => !c.matches('textarea, select[multiple], select[size]')).map(c => Math.round(c.getBoundingClientRect().height));
      if (!hs.length) hs.push(0);
      if (Math.max(...tops) - Math.min(...tops) > 3 || Math.max(...hs) - Math.min(...hs) > 3)
        rowsOff.push(`${name(g)} [${row.map(k => txt(k.querySelector('span,label') || k).slice(0, 30)).join(' | ')}] dTop=${Math.max(...tops) - Math.min(...tops)} h=${hs.join('/')}`);
    });
  });
  // Editable tables: in a numeric column (th.num) the header, every input and the totals share one right edge.
  const inner = (e) => { const r = e.getBoundingClientRect(), cs = getComputedStyle(e); return r.right - parseFloat(cs.paddingRight) - parseFloat(cs.borderRightWidth); };
  main.querySelectorAll('table').forEach(tb => {
    if (!vis(tb) || !tb.querySelector('tbody input')) return;
    [...tb.querySelectorAll('thead th')].forEach((th, i) => {
      if (!th.matches('.num') || !th.textContent.trim()) return;
      const cells = [...tb.querySelectorAll(`tbody tr > td:nth-child(${i + 1}) > input:not([type=checkbox]):not([type=radio]), tfoot tr > td.num:nth-child(${i + 1})`)].filter(vis);
      const off = cells.map(c => Math.round(inner(c) - inner(th))).filter(d => Math.abs(d) > 3);
      if (off.length) rowsOff.push(`${name(tb)} cột "${th.textContent.trim().slice(0, 24)}" lệch mép phải ${Math.min(...off)}…${Math.max(...off)} px so với tiêu đề`);
    });
  });
  const clipped = [];
  main.querySelectorAll('*').forEach(e => {
    if (!vis(e) || e.children.length > 3 || e.closest('.sr-only')) return;
    const cs = getComputedStyle(e);
    if (!(cs.textOverflow === 'ellipsis' || cs.overflow === 'hidden' || cs.overflowX === 'hidden')) return;
    if (e.matches('select,input,textarea,canvas,svg') || e.closest('svg')) return;
    if (e.scrollWidth > e.clientWidth + 2 && txt(e)) clipped.push(`${name(e)} "${txt(e).slice(0, 50)}"`);
  });
  // Chosen option wider than its select (a typed value wider than an input is data, not a layout bug: not reported).
  main.querySelectorAll('select').forEach(e => {
    if (!vis(e) || !e.selectedOptions[0] || e.multiple) return;
    const c = document.createElement('canvas').getContext('2d'); const cs = getComputedStyle(e);
    c.font = `${cs.fontWeight} ${cs.fontSize} ${cs.fontFamily}`;
    const need = c.measureText(e.selectedOptions[0].text).width + parseFloat(cs.paddingLeft) + 26;
    if (need > e.clientWidth + 2) clipped.push(`select ${name(e)} "${e.selectedOptions[0].text.slice(0, 40)}" needs ${Math.round(need)} > ${e.clientWidth}px`);
  });
  // Text line boxes of the visible text only (icons and .sr-only text do not count as lines).
  const lines = (e) => {
    const tops = new Set();
    const walk = (n) => n.childNodes.forEach(c => {
      if (c.nodeType === 3 && c.textContent.trim()) { const r = document.createRange(); r.selectNodeContents(c); [...r.getClientRects()].forEach(x => { if (x.width > 1) tops.add(Math.round(x.top / 6)); }); }
      else if (c.nodeType === 1 && !c.matches('.sr-only, svg, i[data-lucide]')) walk(c);
    });
    walk(e); return tops.size;
  };
  const wrapped = [];
  main.querySelectorAll('.field > span:first-child, .field > label, th, .btn, .panel-title, .kpi-l, .tab, .page-tabs button, .chip, .pill, .tag').forEach(e => {
    if (!vis(e) || e.matches('.dropzone')) return; // a drop zone is a 2–3 line target by design
    const n = lines(e), t = txt(e);
    const lim = e.matches('.btn,.pill,.tag,.chip,.page-tabs button,.tab') ? 1 : 2;
    if (t && n > lim) wrapped.push(`${e.matches('th') ? 'th' : name(e)} ${n} dòng "${t.slice(0, 60)}"`);
  });
  return { hscroll: document.documentElement.scrollWidth > innerWidth ? document.documentElement.scrollWidth : 0,
    rowsOff, clipped: [...new Set(clipped)].slice(0, 15), wrapped: [...new Set(wrapped)].slice(0, 20) };
}

(async () => {
  if (out) fs.mkdirSync(out, { recursive: true });
  const b = await chromium.launch(); const errs = []; const report = {};
  for (const w of widths) {
    const p = await (await b.newContext({ viewport: { width: w, height: 900 } })).newPage();
    p.on('pageerror', e => errs.push(`${w}px ${e.message}`));
    await p.goto(BASE + '/');
    await p.fill('#loginUsername', USER); await p.fill('#loginPassword', PASS); await p.click('#btnLogin'); await p.waitForTimeout(1800);
    if (pages.includes('sizing')) { // run the bundled sample so the result panels exist
      await p.goto(BASE + '/#/sizing'); await p.waitForTimeout(1500);
      const s = await p.$('#btnSizingSample');
      if (s) { await s.click(); await p.waitForTimeout(800); await p.click('#btnSizingRun').catch(() => {}); await p.waitForTimeout(2000); }
    }
    for (const pg of pages) {
      await p.goto(`${BASE}/#/${pg}`); await p.waitForTimeout(2000);
      await p.evaluate(t => { document.documentElement.dataset.theme = t; }, theme);
      report[`${pg} @${w}`] = await p.evaluate(measure);
      if (out) await p.screenshot({ path: path.join(out, `${pg.replace(/\//g, '_')}-${theme}-${w}.png`), fullPage: true });
    }
    await p.context().close();
  }
  await b.close();
  if (process.argv.includes('--json')) { console.log(JSON.stringify({ report, errs }, null, 1)); }
  else {
    const tot = { hscroll: 0, rowsOff: 0, clipped: 0, wrapped: 0 };
    for (const [k, v] of Object.entries(report)) {
      const c = { hscroll: v.hscroll ? 1 : 0, rowsOff: v.rowsOff.length, clipped: v.clipped.length, wrapped: v.wrapped.length };
      Object.keys(tot).forEach(x => { tot[x] += c[x]; });
      if (!Object.values(c).some(Boolean)) continue;
      console.log(`${k.padEnd(28)} cuộn ngang=${v.hscroll || '-'} lệch hàng=${c.rowsOff} bị cắt=${c.clipped} quá dài=${c.wrapped}`);
      v.rowsOff.forEach(x => console.log('    LỆCH ', x));
      v.clipped.forEach(x => console.log('    CẮT  ', x));
      v.wrapped.forEach(x => console.log('    DÀI  ', x));
    }
    console.log('TỔNG', JSON.stringify(tot), errs.length ? `\nLỖI JS: ${errs.slice(0, 5).join(' | ')}` : '');
  }
  process.exit(Object.values(report).some(v => v.hscroll) || errs.length ? 1 : 0);
})();
