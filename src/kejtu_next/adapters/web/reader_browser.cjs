'use strict';

const fs = require('node:fs');
const { chromium, errors } = require(process.argv[2]);

function allowedRequest(request, page) {
  const address = request.url();
  if (address.length > 2048 || /[\x00-\x20\\]/.test(address)) return false;
  let url;
  try {
    url = new URL(address);
  } catch {
    return false;
  }
  const host = url.hostname.toLowerCase().replace(/\.$/, '');
  if (request.isNavigationRequest()) {
    // A popup or provisional frame may not yet have an addressable Frame.
    try {
      if (request.frame() !== page.mainFrame()) return false;
    } catch {
      return false;
    }
  }
  return ['https:', 'http:'].includes(url.protocol) && !url.username && !url.password &&
    ['', '80', '443'].includes(url.port) && host && !host.includes('%') &&
    host !== 'localhost' && !['.local', '.localhost', '.internal', '.onion'].some(s => host.endsWith(s)) &&
    ['GET', 'HEAD'].includes(request.method()) && !['image', 'media', 'font'].includes(request.resourceType());
}

const visibleText = page => page.evaluate(() => document.body ? document.body.innerText.slice(0, 220000) : '');

async function snapshot({ url, timeout, port, password }) {
  const started = Date.now();
  const browser = await chromium.launch({
    headless: true, chromiumSandbox: true,
    proxy: { server: 'http://127.0.0.1:' + port, username: 'kejtu', password, bypass: '<-loopback>' },
    args: ['--disable-quic', '--force-webrtc-ip-handling-policy=disable_non_proxied_udp'],
  });
  try {
    const context = await browser.newContext({
      acceptDownloads: false, serviceWorkers: 'block', permissions: [], locale: 'de-AT', timezoneId: 'Europe/Vienna',
    });
    const page = await context.newPage();
    let rejected = 0;
    await context.route('**/*', route => {
      if (allowedRequest(route.request(), page)) return route.continue();
      rejected++;
      return route.abort();
    });
    await context.routeWebSocket('**/*', socket => socket.close());
    context.on('page', extra => { if (extra !== page) void extra.close().catch(() => {}); });
    page.on('dialog', dialog => dialog.dismiss());
    page.setDefaultTimeout(Math.max(1000, (timeout - 3) * 1000));
    const warnings = [];
    let response;
    try {
      response = await page.goto(url, { waitUntil: 'domcontentloaded', timeout: Math.max(1000, (timeout - 5) * 1000) });
    } catch (error) {
      if (!(error instanceof errors.TimeoutError)) throw error;
      warnings.push('Navigation timed out; content may be incomplete.');
    }
    const result = { url: page.url(), http_status: response ? response.status() : null,
      content_type: (response?.headers()['content-type'] || '').toLowerCase(), warnings };
    if (response && (result.http_status >= 400 || !/text\/html|application\/xhtml|text\/plain/.test(result.content_type))) {
      return result;
    }
    let previous, stable = 0;
    for (let i = 0; i < 8 && Date.now() - started < (timeout - 3) * 1000; i++) {
      await page.waitForTimeout(350);
      const current = await visibleText(page);
      stable = current === previous && current.length > 150 ? stable + 1 : 0;
      if (stable >= 3) break;
      previous = current;
    }
    const title = await page.title();
    const html = await page.content();
    if (Buffer.byteLength(html) > 5000000) throw Error('Rendered page is too large to extract in this reader.');
    return { ...result, url: page.url(), title, html, visible: await visibleText(page), rejected };
  } finally {
    await browser.close();
  }
}

snapshot(JSON.parse(fs.readFileSync(0, 'utf8')))
  .then(result => process.stdout.write(JSON.stringify(result)))
  .catch(error => {
    process.stderr.write(String(error.message).split('Call log:')[0].slice(0, 500));
    process.exitCode = 1;
  });
