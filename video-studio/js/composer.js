// The composer panel: provider/model selection, capability-driven controls, and submit.

import { $, el, toast, fileToDataURL, ApiError } from './util.js';
import { settings, saveSettings, hasKey } from './state.js';
import {
  providers, getProvider, modelsFor, findModel, setDiscoveredModels, makeContext, estimateCost,
} from './providers/index.js';
import { STYLE_PRESETS, composePrompt } from './prompt.js';
import { enqueue } from './queue.js';

let referenceImage = null; // { dataUrl, name } | { url } | null

export function initComposer() {
  // Restore the text fields first: applyCapabilities() writes the form back to settings,
  // so an empty textarea at that point would erase the saved prompt.
  $('#prompt').value = settings.prompt ?? '';
  $('#negativePrompt').value = settings.negativePrompt ?? '';

  buildStyleChips();
  buildProviderSelect();
  wireImageInput();

  $('#provider').addEventListener('change', (e) => {
    saveSettings({ providerId: e.target.value, modelId: '' });
    buildModelSelect();
    applyCapabilities();
    refreshKeyStatus();
  });

  $('#model').addEventListener('change', (e) => {
    saveSettings({ modelId: e.target.value });
    applyCapabilities();
  });

  for (const id of ['aspectRatio', 'resolution', 'duration', 'sampleCount', 'seed', 'negativePrompt', 'generateAudio']) {
    $(`#${id}`).addEventListener('change', persistFromForm);
  }
  $('#prompt').addEventListener('input', debounce(persistFromForm, 400));
  $('#negativePrompt').addEventListener('input', debounce(persistFromForm, 400));

  $('#btnRefreshModels').addEventListener('click', refreshModels);
  $('#btnClearStyles').addEventListener('click', () => {
    saveSettings({ styles: [] });
    buildStyleChips();
    updateEstimate();
  });
  $('#btnGenerate').addEventListener('click', onGenerate);

  buildModelSelect();
  applyCapabilities();
  restoreForm();
  refreshKeyStatus();
}

// ── build controls ─────────────────────────────────────────
function buildStyleChips() {
  const host = $('#styleChips');
  host.replaceChildren(
    ...STYLE_PRESETS.map((preset) => {
      const on = settings.styles.includes(preset.id);
      return el('button', {
        type: 'button',
        class: `chip${on ? ' is-on' : ''}`,
        text: preset.label,
        title: preset.clause,
        onclick: () => {
          const next = new Set(settings.styles);
          next.has(preset.id) ? next.delete(preset.id) : next.add(preset.id);
          saveSettings({ styles: [...next] });
          buildStyleChips();
          updateEstimate();
        },
      });
    })
  );
}

function buildProviderSelect() {
  const sel = $('#provider');
  sel.replaceChildren(
    ...providers.map((p) => el('option', { value: p.id, text: p.label }))
  );
  sel.value = settings.providerId;
  if (!sel.value) { sel.value = providers[0].id; saveSettings({ providerId: sel.value }); }
}

function buildModelSelect() {
  const provider = getProvider(settings.providerId);
  const models = modelsFor(provider);
  const sel = $('#model');
  sel.replaceChildren(...models.map((m) => el('option', { value: m.id, text: m.label })));
  const wanted = models.some((m) => m.id === settings.modelId) ? settings.modelId : models[0]?.id;
  sel.value = wanted ?? '';
  saveSettings({ modelId: sel.value });
}

/** Re-shape every dependent control around what the chosen model actually supports. */
function applyCapabilities() {
  const provider = getProvider(settings.providerId);
  const model = findModel(provider, settings.modelId);
  const caps = model?.caps ?? {};

  fillOptions('#aspectRatio', caps.aspectRatios ?? ['16:9'], settings.aspectRatio, (v) => v);
  fillOptions('#resolution', caps.resolutions ?? ['720p'], settings.resolution, (v) => v);
  fillOptions('#duration', caps.durations ?? [5], String(settings.durationSeconds), (v) => `${v}s`);

  const maxSamples = caps.maxSamples ?? 1;
  const counts = Array.from({ length: maxSamples }, (_, i) => i + 1);
  fillOptions('#sampleCount', counts, String(settings.sampleCount), (v) => v);
  $('#sampleCount').disabled = maxSamples === 1;

  $('#generateAudio').disabled = !caps.audio;
  if (!caps.audio) $('#generateAudio').checked = false;
  $('#negativePrompt').disabled = !caps.negativePrompt;
  $('#negativePrompt').placeholder = caps.negativePrompt
    ? 'blurry, warped hands, text overlays, watermark'
    : 'not supported by this model';
  $('#seed').disabled = !caps.seed;

  const notes = [];
  if (provider.note) notes.push(provider.note);
  if (!caps.imageToVideo) notes.push('This model is text-to-video only; the reference image will be ignored.');
  if (!provider.direct && !settings.proxyUrl) notes.push('⚠ No proxy configured — generation will fail until you set one in Settings.');
  $('#capNotes').textContent = notes.join(' ');

  persistFromForm();
}

function fillOptions(sel, values, preferred, label) {
  const node = $(sel);
  node.replaceChildren(...values.map((v) => el('option', { value: String(v), text: String(label(v)) })));
  node.value = values.map(String).includes(String(preferred)) ? String(preferred) : String(values[0] ?? '');
}

// ── reference image ────────────────────────────────────────
function wireImageInput() {
  const drop = $('#imageDrop');
  const input = $('#imageInput');

  $('#btnPickImage').addEventListener('click', () => input.click());
  input.addEventListener('change', () => input.files?.[0] && loadImage(input.files[0]));
  $('#btnClearImage').addEventListener('click', clearImage);

  $('#imageUrl')?.addEventListener('change', (e) => {
    const url = e.target.value.trim();
    if (!url) return clearImage();
    referenceImage = { url, name: url.split('/').pop() };
    showImagePreview(url);
  });

  ['dragenter', 'dragover'].forEach((evt) =>
    drop.addEventListener(evt, (e) => { e.preventDefault(); drop.classList.add('is-over'); })
  );
  ['dragleave', 'drop'].forEach((evt) =>
    drop.addEventListener(evt, (e) => { e.preventDefault(); drop.classList.remove('is-over'); })
  );
  drop.addEventListener('drop', (e) => {
    const file = e.dataTransfer?.files?.[0];
    if (file?.type.startsWith('image/')) loadImage(file);
  });
}

async function loadImage(file) {
  if (file.size > 8 * 1024 * 1024) {
    toast('That image is over 8 MB — most APIs reject it. Try a smaller one.', 'bad');
    return;
  }
  const dataUrl = await fileToDataURL(file);
  referenceImage = { dataUrl, name: file.name };
  showImagePreview(dataUrl);
}

function showImagePreview(src) {
  $('#imagePreview').src = src;
  $('#imagePreviewWrap').classList.remove('hidden');
  $('#imageHint').classList.add('hidden');
  // The disclosure is usually collapsed, so flag the attachment where it can be seen.
  $('#imageBadge').classList.remove('hidden');
}

function clearImage() {
  referenceImage = null;
  $('#imageInput').value = '';
  if ($('#imageUrl')) $('#imageUrl').value = '';
  $('#imagePreview').removeAttribute('src');
  $('#imagePreviewWrap').classList.add('hidden');
  $('#imageHint').classList.remove('hidden');
  $('#imageBadge').classList.add('hidden');
}

// ── form <-> settings ──────────────────────────────────────
function persistFromForm() {
  saveSettings({
    prompt: $('#prompt').value,
    aspectRatio: $('#aspectRatio').value,
    resolution: $('#resolution').value,
    durationSeconds: Number($('#duration').value) || 5,
    sampleCount: Number($('#sampleCount').value) || 1,
    seed: $('#seed').value,
    negativePrompt: $('#negativePrompt').value,
    generateAudio: $('#generateAudio').checked,
  });
  updateEstimate();
}

function restoreForm() {
  if (!$('#prompt').value) $('#prompt').value = settings.prompt ?? '';
  $('#negativePrompt').value = settings.negativePrompt ?? '';
  $('#seed').value = settings.seed ?? '';
  $('#generateAudio').checked = Boolean(settings.generateAudio) && !$('#generateAudio').disabled;
  updateEstimate();
}

export function updateEstimate() {
  const provider = getProvider(settings.providerId);
  const model = findModel(provider, settings.modelId);
  const cost = estimateCost(model, settings);
  $('#estimate').textContent = cost === null ? '—' : `$${cost.toFixed(2)}`;
  $('#estimateNote').textContent = cost === null
    ? 'no published rate for this model'
    : `${settings.sampleCount}× ${settings.durationSeconds}s · rate is approximate`;
}

export function refreshKeyStatus() {
  const provider = getProvider(settings.providerId);
  const ok = hasKey(provider.id);
  const pill = $('#keyStatus');
  const name = provider.short ?? provider.label;
  pill.textContent = ok ? `${name} key saved` : `${name} key needed`;
  pill.title = ok ? `A ${provider.keyLabel} is saved in this browser.` : `Add a ${provider.keyLabel} under Settings.`;
  pill.className = `pill ${ok ? 'pill-ok' : 'pill-warn'}`;
  $('#btnGenerate').disabled = !ok;
}

// ── actions ────────────────────────────────────────────────
async function refreshModels() {
  const provider = getProvider(settings.providerId);
  if (!provider.listModels) {
    toast(`${provider.label} has no model-listing endpoint; using the built-in list.`);
    return;
  }
  const btn = $('#btnRefreshModels');
  btn.disabled = true;
  btn.textContent = 'fetching…';
  try {
    const models = await provider.listModels(makeContext(provider));
    if (!models.length) throw new ApiError('No video-capable models are available for this key.');
    setDiscoveredModels(provider.id, models);
    buildModelSelect();
    applyCapabilities();
    toast(`Found ${models.length} video model${models.length === 1 ? '' : 's'}.`, 'ok');
  } catch (err) {
    toast(err.message ?? String(err), 'bad');
  } finally {
    btn.disabled = false;
    btn.textContent = 'refresh';
  }
}

/** Everything the queue needs to reproduce this generation, self-contained. */
export function buildSpec(overrides = {}) {
  const provider = getProvider(settings.providerId);
  const model = findModel(provider, settings.modelId);
  const caps = model?.caps ?? {};
  const base = overrides.prompt ?? $('#prompt').value;

  return {
    providerId: provider.id,
    modelId: model?.id ?? settings.modelId,
    prompt: composePrompt(base, settings.styles),
    rawPrompt: (base ?? '').trim(),
    styles: [...settings.styles],
    negativePrompt: caps.negativePrompt ? ($('#negativePrompt').value || '').trim() : '',
    aspectRatio: $('#aspectRatio').value,
    resolution: $('#resolution').value,
    durationSeconds: Number($('#duration').value) || 5,
    sampleCount: Number($('#sampleCount').value) || 1,
    seed: caps.seed ? $('#seed').value : '',
    generateAudio: caps.audio ? $('#generateAudio').checked : false,
    image: caps.imageToVideo ? referenceImage : null,
    ...overrides,
  };
}

function onGenerate() {
  const errBox = $('#composerError');
  errBox.classList.add('hidden');

  const spec = buildSpec();
  if (!spec.rawPrompt) {
    errBox.textContent = 'Write a prompt first.';
    errBox.classList.remove('hidden');
    return;
  }
  const provider = getProvider(spec.providerId);
  try {
    makeContext(provider); // surfaces missing key / missing proxy before anything is queued
  } catch (err) {
    errBox.textContent = err.message;
    errBox.classList.remove('hidden');
    return;
  }

  enqueue(spec);
  toast('Queued.', 'ok');
  document.querySelector('.tab[data-tab="queue"]')?.click();
}

/** Repopulate the composer from a previous clip's settings. */
export function applySpec(spec) {
  saveSettings({
    providerId: spec.providerId,
    modelId: spec.modelId,
    styles: spec.styles ?? [],
    aspectRatio: spec.aspectRatio,
    resolution: spec.resolution,
    durationSeconds: spec.durationSeconds,
    sampleCount: spec.sampleCount,
    seed: spec.seed ?? '',
    negativePrompt: spec.negativePrompt ?? '',
    generateAudio: Boolean(spec.generateAudio),
  });
  $('#provider').value = spec.providerId;
  buildStyleChips();
  buildModelSelect();
  applyCapabilities();
  $('#prompt').value = spec.rawPrompt ?? spec.prompt ?? '';
  saveSettings({ prompt: $('#prompt').value });
  restoreForm();
  refreshKeyStatus();
  window.scrollTo({ top: 0, behavior: 'smooth' });
}

function debounce(fn, ms) {
  let t;
  return (...args) => { clearTimeout(t); t = setTimeout(() => fn(...args), ms); };
}

export { applyCapabilities };
