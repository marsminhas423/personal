// Settings modal: per-provider keys, proxy URL, queue tuning, and a hard reset.

import { $, el, toast, formatBytes } from './util.js';
import { settings, saveSettings, getKey, setKey, clearKeys, wipeLocalSettings } from './state.js';
import { providers } from './providers/index.js';
import { wipeDatabase, estimateUsage } from './db.js';
import { refreshKeyStatus, applyCapabilities } from './composer.js';
import { refreshGallery } from './panels.js';

export function initSettings() {
  buildKeyFields();

  $('#proxyUrl').value = settings.proxyUrl ?? '';
  $('#concurrency').value = settings.concurrency ?? 2;
  $('#pollInterval').value = settings.pollIntervalSec ?? 8;

  $('#proxyUrl').addEventListener('change', (e) => {
    saveSettings({ proxyUrl: e.target.value.trim() });
    applyCapabilities();
  });
  $('#concurrency').addEventListener('change', (e) =>
    saveSettings({ concurrency: clamp(Number(e.target.value), 1, 6) }));
  $('#pollInterval').addEventListener('change', (e) =>
    saveSettings({ pollIntervalSec: clamp(Number(e.target.value), 2, 60) }));

  $('#btnSettings').addEventListener('click', async () => {
    await showUsage();
    $('#settings').showModal();
  });
  $('#settings').addEventListener('close', () => { refreshKeyStatus(); applyCapabilities(); });

  $('#btnWipe').addEventListener('click', onWipe);
}

function buildKeyFields() {
  $('#keyFields').replaceChildren(...providers.map((provider) => {
    const input = el('input', {
      type: 'password',
      autocomplete: 'off',
      spellcheck: 'false',
      placeholder: provider.direct ? 'paste key — works without a proxy' : 'paste key — needs a proxy',
      value: getKey(provider.id),
      onchange: (e) => { setKey(provider.id, e.target.value); refreshKeyStatus(); },
    });

    return el('div', { class: 'keyrow' },
      el('label', { class: 'field' },
        el('span', { class: 'field-label' },
          provider.keyLabel,
          el('a', { href: provider.keyHelp, target: '_blank', rel: 'noopener noreferrer', class: 'muted', text: 'get one ↗' }),
        ),
        input,
      ),
      el('button', {
        type: 'button', class: 'btn btn-ghost btn-sm', text: 'Show',
        onclick: (e) => {
          const shown = input.type === 'text';
          input.type = shown ? 'password' : 'text';
          e.target.textContent = shown ? 'Show' : 'Hide';
        },
      }),
    );
  }));
}

async function showUsage() {
  const usage = await estimateUsage();
  const note = $('#settings .callout');
  const existing = note.querySelector('.usage');
  if (!usage?.usage) return existing?.remove();
  const text = `Stored clips are using about ${formatBytes(usage.usage)} of this browser's quota.`;
  if (existing) existing.textContent = text;
  else note.append(el('div', { class: 'usage', style: 'margin-top:6px', text }));
}

async function onWipe() {
  const ok = confirm(
    'Erase every saved API key, setting, storyboard and generated clip in this browser?\n\n' +
    'This cannot be undone. Videos already downloaded to disk are unaffected.'
  );
  if (!ok) return;

  clearKeys();
  wipeLocalSettings();
  await wipeDatabase().catch(() => {});
  await refreshGallery();
  toast('Local data erased. Reloading…', 'ok');
  setTimeout(() => location.reload(), 900);
}

const clamp = (n, lo, hi) => Math.min(hi, Math.max(lo, Number.isFinite(n) ? n : lo));
