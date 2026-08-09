// Persistent settings + API key vault (localStorage).
//
// Keys live in localStorage because this is a static, server-less app: there is nowhere
// else to put them. That is a real trade-off and the UI says so plainly. Anything that
// can run JS on this page can read them, so a scoped, spend-limited key is the right call.

const KEY_SETTINGS = 'videostudio.settings.v1';
const KEY_VAULT = 'videostudio.keys.v1';
const KEY_BOARD = 'videostudio.storyboard.v1';

const DEFAULT_SETTINGS = {
  providerId: 'gemini',
  modelId: '',
  prompt: '',
  aspectRatio: '16:9',
  resolution: '720p',
  durationSeconds: 8,
  sampleCount: 1,
  generateAudio: true,
  negativePrompt: '',
  seed: '',
  styles: [],
  proxyUrl: '',
  concurrency: 2,
  pollIntervalSec: 8,
};

function readJSON(key, fallback) {
  try {
    const raw = localStorage.getItem(key);
    return raw ? { ...fallback, ...JSON.parse(raw) } : { ...fallback };
  } catch {
    return { ...fallback };
  }
}

function writeJSON(key, value) {
  try {
    localStorage.setItem(key, JSON.stringify(value));
  } catch (err) {
    console.warn('Could not persist', key, err);
  }
}

export const settings = readJSON(KEY_SETTINGS, DEFAULT_SETTINGS);

export function saveSettings(patch = {}) {
  Object.assign(settings, patch);
  writeJSON(KEY_SETTINGS, settings);
}

// ── key vault ──────────────────────────────────────────────
let vault = readJSON(KEY_VAULT, {});

export const getKey = (providerId) => (vault[providerId] || '').trim();

export function setKey(providerId, value) {
  const v = (value || '').trim();
  if (v) vault[providerId] = v;
  else delete vault[providerId];
  writeJSON(KEY_VAULT, vault);
}

export const hasKey = (providerId) => getKey(providerId).length > 0;

export function clearKeys() {
  vault = {};
  localStorage.removeItem(KEY_VAULT);
}

// ── storyboard ─────────────────────────────────────────────
export function loadStoryboard() {
  try {
    const raw = localStorage.getItem(KEY_BOARD);
    const parsed = raw ? JSON.parse(raw) : [];
    return Array.isArray(parsed) ? parsed : [];
  } catch {
    return [];
  }
}

export const saveStoryboard = (shots) => writeJSON(KEY_BOARD, shots);

export function wipeLocalSettings() {
  [KEY_SETTINGS, KEY_VAULT, KEY_BOARD].forEach((k) => localStorage.removeItem(k));
}
