// Small shared helpers. No dependencies, no build step.

export const $ = (sel, root = document) => root.querySelector(sel);
export const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];

export const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

export function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v === null || v === undefined || v === false) continue;
    if (k === 'class') node.className = v;
    else if (k === 'text') node.textContent = v;
    else if (k.startsWith('on') && typeof v === 'function') node.addEventListener(k.slice(2), v);
    else if (k === 'dataset') Object.assign(node.dataset, v);
    else node.setAttribute(k, v === true ? '' : v);
  }
  for (const c of children.flat()) {
    if (c === null || c === undefined || c === false) continue;
    node.append(c instanceof Node ? c : document.createTextNode(String(c)));
  }
  return node;
}

export const uid = () =>
  (crypto.randomUUID?.() ?? `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`);

/** Error that carries the provider's HTTP status and raw body, so the UI can show something actionable. */
export class ApiError extends Error {
  constructor(message, { status, body, url } = {}) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
    this.body = body;
    this.url = url;
  }
  /** 4xx (except 408/429) will never succeed on retry — don't burn attempts on them. */
  get retryable() {
    if (this.status === undefined) return true; // network/CORS failure
    if (this.status === 408 || this.status === 429) return true;
    return this.status >= 500;
  }
}

/** Pull the most human-readable message out of whatever error shape a provider returned. */
export function describeApiBody(body) {
  if (!body) return '';
  if (typeof body === 'string') return body.slice(0, 600);
  const cand =
    body?.error?.message ??
    body?.error?.detail ??
    body?.message ??
    body?.detail ??
    body?.failure_reason ??
    (Array.isArray(body?.detail) ? body.detail.map((d) => d?.msg).filter(Boolean).join('; ') : null);
  if (typeof cand === 'string' && cand.trim()) return cand.trim().slice(0, 600);
  try { return JSON.stringify(body).slice(0, 600); } catch { return String(body).slice(0, 600); }
}

/**
 * fetch + JSON with uniform error reporting.
 * A thrown TypeError from fetch is almost always CORS or offline, so it is relabelled.
 */
export async function requestJSON(url, options = {}) {
  let res;
  try {
    res = await fetch(url, options);
  } catch (cause) {
    throw new ApiError(
      'Network or CORS failure. If this provider needs a proxy, set one in Settings.',
      { url }
    );
  }
  const text = await res.text();
  let body = text;
  if (text) { try { body = JSON.parse(text); } catch { /* keep raw text */ } }
  if (!res.ok) {
    const detail = describeApiBody(body);
    throw new ApiError(`HTTP ${res.status}${detail ? ` — ${detail}` : ''}`, {
      status: res.status, body, url,
    });
  }
  return body;
}

export async function requestBlob(url, options = {}) {
  let res;
  try {
    res = await fetch(url, options);
  } catch {
    throw new ApiError('Network or CORS failure while downloading the video.', { url });
  }
  if (!res.ok) {
    const text = await res.text().catch(() => '');
    throw new ApiError(`HTTP ${res.status} downloading video${text ? ` — ${text.slice(0, 200)}` : ''}`, {
      status: res.status, url,
    });
  }
  return res.blob();
}

export function fileToDataURL(file) {
  return new Promise((resolve, reject) => {
    const fr = new FileReader();
    fr.onload = () => resolve(fr.result);
    fr.onerror = () => reject(fr.error ?? new Error('Could not read file'));
    fr.readAsDataURL(file);
  });
}

/** "data:image/png;base64,AAA" -> { mimeType: "image/png", data: "AAA" } */
export function splitDataURL(dataURL) {
  const m = /^data:([^;,]+)(;base64)?,(.*)$/s.exec(dataURL ?? '');
  if (!m) return null;
  return { mimeType: m[1], base64: m[2] ? m[3] : btoa(unescape(encodeURIComponent(decodeURIComponent(m[3])))) };
}

export function downloadBlob(blob, filename) {
  const url = URL.createObjectURL(blob);
  const a = el('a', { href: url, download: filename });
  document.body.append(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 10_000);
}

export function slugify(text, max = 48) {
  return (text || 'clip')
    .toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '')
    .slice(0, max) || 'clip';
}

export function relativeTime(ts) {
  const secs = Math.round((Date.now() - ts) / 1000);
  if (secs < 60) return `${secs}s ago`;
  if (secs < 3600) return `${Math.round(secs / 60)}m ago`;
  if (secs < 86400) return `${Math.round(secs / 3600)}h ago`;
  return new Date(ts).toLocaleDateString();
}

export function formatBytes(n) {
  if (!n) return '—';
  const units = ['B', 'KB', 'MB', 'GB'];
  let i = 0;
  while (n >= 1024 && i < units.length - 1) { n /= 1024; i++; }
  return `${n < 10 && i > 0 ? n.toFixed(1) : Math.round(n)} ${units[i]}`;
}

export function toast(message, kind = '') {
  const host = $('#toasts');
  if (!host) return;
  const node = el('div', { class: `toast ${kind}`.trim(), text: message });
  host.append(node);
  setTimeout(() => {
    node.style.opacity = '0';
    node.style.transition = 'opacity .3s';
    setTimeout(() => node.remove(), 320);
  }, kind === 'bad' ? 7000 : 3800);
}
