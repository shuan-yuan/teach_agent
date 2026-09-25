/* 教育智能体 Service Worker
 *
 * 策略：同源 GET 一律 network-first，断网才回落缓存。
 *
 * 为什么不用 cache-first：这个应用的数据会变（批改结果、学生列表）。
 * shell 走 cache-first 会导致「重新发布后用户继续吃旧的 app.js / index.html」，
 * 出现「新页面 + 旧脚本」的版本错配，比全旧更难排查。
 * network-first 不影响离线能力 —— 断网时照样回落缓存打开。
 *
 * 不拦 /api/*：接口响应千变万化，塞进 Cache Storage 会撑爆配额且没有任何好处。
 */

const CACHE = 'edu-agent-shell-v1';
const SHELL = ['/', '/index.html', '/manifest.webmanifest'];

self.addEventListener('install', (event) => {
  event.waitUntil(
    caches
      .open(CACHE)
      .then((cache) => cache.addAll(SHELL))
      .catch(() => undefined)
      .then(() => self.skipWaiting()),
  );
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches
      .keys()
      .then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
      .then(() => self.clients.claim()),
  );
});

self.addEventListener('fetch', (event) => {
  const req = event.request;

  // 只处理同源 GET
  if (req.method !== 'GET') return;

  let url;
  try {
    url = new URL(req.url);
  } catch {
    return;
  }
  if (url.origin !== self.location.origin) return;

  // 接口请求不缓存，直接走网络
  if (url.pathname.indexOf('/api/') === 0) return;

  event.respondWith(
    fetch(req)
      .then((res) => {
        if (res && res.ok && res.type === 'basic') {
          const copy = res.clone();
          caches.open(CACHE).then((c) => c.put(req, copy)).catch(() => undefined);
        }
        return res;
      })
      .catch(() =>
        caches.match(req).then((cached) => cached || caches.match('/index.html')),
      ),
  );
});
