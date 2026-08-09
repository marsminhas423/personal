/**
 * Minimal CORS proxy for Video Studio (Cloudflare Workers).
 *
 * Runway, Luma and fal do not send CORS headers, so a browser cannot call them directly.
 * This forwards the request and adds the headers back.
 *
 *   GET/POST  https://your-worker.workers.dev/?url=<url-encoded target>
 *
 * Two deliberate restrictions keep this from becoming an open relay that anyone can point
 * at anything while your Worker pays the bandwidth:
 *
 *   1. Only the hosts in ALLOWED_HOSTS can be reached.
 *   2. If the ALLOWED_ORIGIN variable is set, only that page may call it.
 *
 * The proxy never sees a key it stores — the browser sends the Authorization header, this
 * passes it through, and nothing is logged. It is still a hop that can read your traffic,
 * so deploy it under your own account rather than using someone else's.
 */

const ALLOWED_HOSTS = new Set([
  'api.dev.runwayml.com',
  'api.lumalabs.ai',
  'queue.fal.run',
  'fal.run',
  'rest.alpha.fal.ai',
  'generativelanguage.googleapis.com',
  // Result CDNs, so downloads can fall back through the proxy too.
  'storage.googleapis.com',
  'dnznrvs05pmza.cloudfront.net',
  'v3.fal.media',
  'v2.fal.media',
  'fal.media',
  'storage.cdn-luma.com',
]);

// Headers that must not be copied from the inbound request.
const STRIP_REQUEST = new Set([
  'host', 'origin', 'referer', 'connection', 'content-length',
  'cf-connecting-ip', 'cf-ipcountry', 'cf-ray', 'cf-visitor', 'x-forwarded-for', 'x-forwarded-proto',
]);

export default {
  async fetch(request, env) {
    const allowedOrigin = env.ALLOWED_ORIGIN?.trim();
    const origin = request.headers.get('origin') ?? '';

    if (allowedOrigin && origin && origin !== allowedOrigin) {
      return text(403, 'Origin not allowed by this proxy.');
    }
    const cors = corsHeaders(allowedOrigin || origin || '*', request);

    if (request.method === 'OPTIONS') {
      return new Response(null, { status: 204, headers: cors });
    }

    const target = new URL(request.url).searchParams.get('url');
    if (!target) return text(400, 'Missing ?url= parameter.', cors);

    let url;
    try {
      url = new URL(target);
    } catch {
      return text(400, 'The url parameter is not a valid URL.', cors);
    }
    if (url.protocol !== 'https:') return text(400, 'Only https targets are allowed.', cors);
    if (!ALLOWED_HOSTS.has(url.hostname)) {
      return text(403, `Host ${url.hostname} is not in this proxy's allowlist. Add it in cors-proxy.js.`, cors);
    }

    const headers = new Headers();
    for (const [name, value] of request.headers) {
      if (!STRIP_REQUEST.has(name.toLowerCase())) headers.set(name, value);
    }

    const hasBody = request.method !== 'GET' && request.method !== 'HEAD';
    let upstream;
    try {
      upstream = await fetch(url.toString(), {
        method: request.method,
        headers,
        body: hasBody ? await request.arrayBuffer() : undefined,
        redirect: 'follow',
      });
    } catch (err) {
      return text(502, `Upstream request failed: ${err.message}`, cors);
    }

    const out = new Headers(upstream.headers);
    for (const [k, v] of Object.entries(cors)) out.set(k, v);
    out.delete('content-encoding'); // already decoded by the runtime
    out.delete('content-security-policy');

    return new Response(upstream.body, { status: upstream.status, statusText: upstream.statusText, headers: out });
  },
};

function corsHeaders(origin, request) {
  return {
    'access-control-allow-origin': origin,
    'access-control-allow-methods': 'GET,POST,PUT,PATCH,DELETE,OPTIONS',
    'access-control-allow-headers':
      request.headers.get('access-control-request-headers') ||
      'authorization,content-type,accept,x-goog-api-key,x-runway-version',
    'access-control-expose-headers': 'content-type,content-length',
    'access-control-max-age': '86400',
    vary: 'origin',
  };
}

function text(status, message, extra = {}) {
  return new Response(JSON.stringify({ error: { message } }), {
    status,
    headers: { 'content-type': 'application/json', ...extra },
  });
}
