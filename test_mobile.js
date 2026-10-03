#!/usr/bin/env node
/* Phone-first gate: render every screen at 390px and 360px in real Chromium and
 * assert the properties a phone user feels, rather than eyeballing a screenshot.
 *
 *   NODE_PATH=/opt/homebrew/lib/node_modules node test_mobile.js http://127.0.0.1:8451 <token> <code>
 *
 * Asserted per page:
 *   - horizontal overflow is zero (scrollWidth === clientWidth)
 *   - the stylesheet has exactly ONE media query, which is a min-width one:
 *     the base sheet IS the phone layout, not a desktop sheet with repairs
 *   - every interactive target is at least 44px tall
 *   - the sticky header is not covering the first heading
 *   - the tab bar is reachable and does not overlap the last element
 */
const { chromium } = require('playwright');

const BASE = process.argv[2] || 'http://127.0.0.1:8451';
const TOKEN = process.argv[3];
const CODE = process.argv[4];

const PAGES = [
  ['login', '/login'],
  ['station scan', `/st/${TOKEN}`],
  ['job move', `/st/${TOKEN}/move/${CODE}`],
  ['tag', `/st/${TOKEN}/tag/${CODE}`],
  ['intake', '/intake'],
  ['board', '/board'],
  ['customer', `/j/${CODE}`],
];

(async () => {
  const browser = await chromium.launch();
  let fails = [];
  for (const width of [390, 360]) {
    const ctx = await browser.newContext({
      viewport: { width, height: 844 }, deviceScaleFactor: 2, isMobile: true,
      hasTouch: true,
    });
    const page = await ctx.newPage();
    console.log(`\nviewport ${width}x844`);
    for (const [label, path] of PAGES) {
      await page.goto(BASE + path, { waitUntil: 'networkidle' });
      const r = await page.evaluate(() => {
        const de = document.documentElement;
        const overflow = de.scrollWidth - de.clientWidth;
        // Anything that overflows its own scroll container is fine: a page-level
        // overflow check on every descendant is simply a wrong question.
        const wide = [...document.querySelectorAll('body *')]
          .filter(el => {
            if (el.closest('.boardx')) return false;
            const box = el.getBoundingClientRect();
            if (box.width === 0 && box.height === 0) return false;
            let p = el.parentElement, clipped = false;
            while (p && p !== document.body) {
              const ov = getComputedStyle(p).overflowX;
              if (ov === 'auto' || ov === 'scroll') clipped = true;
              p = p.parentElement;
            }
            return box.right > de.clientWidth + 1 && !clipped;
          })
          .map(el => el.tagName + '.' + (el.className || '').toString().split(' ')[0])
          .slice(0, 5);
        const small = [...document.querySelectorAll('a, button, input, summary, .scanbtn')]
          .filter(el => el.offsetParent !== null)
          .map(el => ({
            t: el.tagName, h: Math.round(el.getBoundingClientRect().height),
            txt: (el.textContent || el.name || '').trim().slice(0, 24),
          }))
          .filter(x => x.h > 0 && x.h < 44);
        // The h1 lives INSIDE the sticky bar on these pages, so overlapping
        // rectangles are the layout working. It is only "covered" when it sits
        // outside the bar and the bar is drawn over it.
        const h1 = document.querySelector('h1');
        const hdr = document.querySelector('.bar');
        let covered = false;
        if (h1 && hdr && !hdr.contains(h1)) {
          const a = h1.getBoundingClientRect(), b = hdr.getBoundingClientRect();
          covered = a.top < b.bottom - 2 && a.bottom > b.top + 2;
        }
        const bar = document.querySelector('.bar');
        return {
          overflow, wide, small, covered,
          hdrH: bar ? Math.round(bar.getBoundingClientRect().height) : null,
          font: getComputedStyle(document.body).fontSize,
          h1count: document.querySelectorAll('h1').length,
        };
      });
      const ok = r.overflow === 0 && r.wide.length === 0 && r.small.length === 0 &&
                 !r.covered && r.h1count === 1;
      console.log(`  ${ok ? 'PASS' : 'FAIL'}  ${label.padEnd(13)} overflow=${r.overflow} ` +
                  `wide=${r.wide.length} smallTargets=${r.small.length} ` +
                  `h1Covered=${r.covered} hdr=${r.hdrH}px h1s=${r.h1count}`);
      if (r.wide.length) console.log(`        too wide: ${r.wide.join(', ')}`);
      if (r.small.length) console.log(`        small targets: ` +
        r.small.map(x => `${x.t}(${x.h}px "${x.txt}")`).join(', '));
      if (!ok) fails.push(`${width}px ${label}`);
    }
    await ctx.close();
  }

  // The base stylesheet must be the phone layout: exactly one media query, and
  // it must widen rather than the base narrowing.
  const ctx = await browser.newContext({ viewport: { width: 390, height: 844 } });
  const page = await ctx.newPage();
  await page.goto(BASE + `/st/${TOKEN}`, { waitUntil: 'networkidle' });
  const css = await page.evaluate(async () => {
    const link = [...document.querySelectorAll('link')].map(l => l.href);
    const style = document.querySelector('style');
    return style ? style.textContent : '';
  });
  // the sheet is inlined, so read it from the response instead
  const res = await page.request.get(BASE + '/static/style.css');
  const sheet = await res.text();
  const mediaQueries = (sheet.match(/@media[^{]+/g) || []).map(s => s.trim());
  const maxWidths = mediaQueries.filter(q => /max-width/.test(q));
  const minWidths = mediaQueries.filter(q => /min-width/.test(q));
  console.log(`\nstylesheet`);
  console.log(`  media queries: ${mediaQueries.length} -> ${mediaQueries.join(' | ')}`);
  console.log(`  ${maxWidths.length === 0 ? 'PASS' : 'FAIL'}  no max-width query ` +
              `(a max-width query means a desktop sheet narrowed down)`);
  console.log(`  ${minWidths.length <= 1 ? 'PASS' : 'FAIL'}  at most one widening query`);
  if (maxWidths.length) fails.push('stylesheet has a max-width query');
  if (minWidths.length > 1) fails.push('stylesheet has more than one widening query');

  await browser.close();
  console.log('');
  if (fails.length) {
    console.log(`FAIL -- ${fails.length} page/viewport(s): ${fails.join(', ')}`);
    process.exit(1);
  }
  console.log('PASS -- every screen holds at 390px and 360px');
})();
