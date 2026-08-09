// Queue list, gallery, storyboard and the lightbox.

import { $, el, toast, downloadBlob, relativeTime, formatBytes, slugify, uid } from './util.js';
import { queueEvents, listJobs, getJob, cancel, retry, removeJob, clearFinished, cancelPending, enqueue } from './queue.js';
import { allClips, deleteClip } from './db.js';
import { loadStoryboard, saveStoryboard } from './state.js';
import { buildSpec, applySpec } from './composer.js';

/** clipId -> object URL. Revoked on re-render so long sessions don't leak blobs. */
const objectUrls = new Map();
let clips = [];
let shots = loadStoryboard();

export function initPanels() {
  wireTabs();
  wireQueue();
  wireGallery();
  wireStoryboard();
  wireLightbox();

  queueEvents.addEventListener('change', () => { renderQueue(); renderShots(); });
  queueEvents.addEventListener('clips', (e) => {
    attachClipsToShot(e.detail.jobId, e.detail.clipIds);
    void refreshGallery();
  });

  renderQueue();
  renderShots();
  void refreshGallery();
}

// ── tabs ───────────────────────────────────────────────────
function wireTabs() {
  for (const tab of document.querySelectorAll('.tab')) {
    tab.addEventListener('click', () => {
      document.querySelectorAll('.tab').forEach((t) => t.classList.toggle('is-active', t === tab));
      const name = tab.dataset.tab;
      document.querySelectorAll('.tabpane').forEach((p) => p.classList.toggle('is-active', p.id === `tab-${name}`));
    });
  }
}

// ── queue ──────────────────────────────────────────────────
function wireQueue() {
  $('#btnClearFinished').addEventListener('click', () => void clearFinished());
  $('#btnCancelAll').addEventListener('click', cancelPending);
}

function renderQueue() {
  const jobs = listJobs();
  const host = $('#queueList');
  $('#queueCount').textContent = String(jobs.filter((j) => j.status === 'queued' || j.status === 'running').length);
  $('#queueEmpty').classList.toggle('hidden', jobs.length > 0);

  host.replaceChildren(...jobs.map(renderJobRow));
}

function renderJobRow(job) {
  const busy = job.status === 'queued' || job.status === 'running';
  const bar = job.status === 'running'
    ? el('div', { class: `bar${job.progress == null ? ' indeterminate' : ''}` },
        el('i', { style: job.progress == null ? '' : `width:${job.progress}%` }))
    : null;

  const meta = [
    job.spec?.modelId,
    `${job.spec?.durationSeconds}s`,
    job.spec?.aspectRatio,
    job.spec?.resolution,
    job.spec?.sampleCount > 1 ? `×${job.spec.sampleCount}` : null,
    job.label,
    relativeTime(job.createdAt),
  ].filter(Boolean).join(' · ');

  const actions = [];
  if (busy) actions.push(el('button', { class: 'btn btn-ghost btn-sm', text: 'Cancel', onclick: () => cancel(job.id) }));
  if (job.status === 'error') actions.push(el('button', { class: 'btn btn-ghost btn-sm', text: 'Retry', onclick: () => retry(job.id) }));
  if (!busy) actions.push(el('button', { class: 'btn btn-ghost btn-sm', text: 'Remove', onclick: () => void removeJob(job.id) }));

  return el('div', { class: 'job', dataset: { status: job.status } },
    el('span', { class: 'job-status', text: job.status }),
    el('div', { class: 'job-body' },
      el('div', { class: 'job-prompt', text: job.spec?.rawPrompt || job.spec?.prompt || '(no prompt)', title: job.spec?.prompt }),
      el('div', { class: 'job-meta', text: meta }),
      job.error ? el('div', { class: 'job-error', text: job.error }) : null,
      bar,
    ),
    el('div', { class: 'job-actions' }, actions),
  );
}

// ── gallery ────────────────────────────────────────────────
function wireGallery() {
  $('#gallerySearch').addEventListener('input', renderGallery);
  $('#btnDownloadAll').addEventListener('click', () => {
    if (!clips.length) return toast('Nothing to download yet.');
    clips.forEach((clip, i) => setTimeout(() => downloadClip(clip), i * 350));
  });
}

export async function refreshGallery() {
  clips = await allClips().catch(() => []);
  renderGallery();
  renderShots(); // shot rows resolve their thumbnail out of `clips`, so they follow it
}

function clipUrl(clip) {
  if (!objectUrls.has(clip.id)) objectUrls.set(clip.id, URL.createObjectURL(clip.blob));
  return objectUrls.get(clip.id);
}

function renderGallery() {
  const q = $('#gallerySearch').value.trim().toLowerCase();
  const shown = q ? clips.filter((c) => (c.prompt ?? '').toLowerCase().includes(q)) : clips;

  $('#galleryCount').textContent = String(clips.length);
  $('#galleryEmpty').classList.toggle('hidden', clips.length > 0);

  $('#galleryGrid').replaceChildren(...shown.map((clip) =>
    el('div', { class: 'clip', onclick: () => openLightbox(clip) },
      el('video', {
        src: clipUrl(clip), muted: true, playsinline: true, preload: 'metadata',
        onmouseenter: (e) => e.target.play().catch(() => {}),
        onmouseleave: (e) => { e.target.pause(); e.target.currentTime = 0; },
      }),
      el('div', { class: 'clip-info' },
        el('div', { class: 'clip-prompt', text: clip.prompt }),
        el('div', {
          class: 'clip-meta',
          text: `${clip.modelLabel} · ${clip.spec?.durationSeconds ?? '?'}s · ${formatBytes(clip.size)} · ${relativeTime(clip.createdAt)}`,
        }),
      ),
    )
  ));
}

function downloadClip(clip) {
  const ext = (clip.mimeType?.split('/')[1] ?? 'mp4').replace(/[^a-z0-9]/gi, '') || 'mp4';
  downloadBlob(clip.blob, `${slugify(clip.prompt)}-${clip.id.slice(0, 6)}.${ext}`);
}

// ── lightbox ───────────────────────────────────────────────
let lightboxClip = null;

function wireLightbox() {
  $('#btnLbClose').addEventListener('click', () => $('#lightbox').close());
  $('#btnLbDownload').addEventListener('click', () => lightboxClip && downloadClip(lightboxClip));
  $('#btnLbReuse').addEventListener('click', () => {
    if (!lightboxClip?.spec) return;
    applySpec(lightboxClip.spec);
    $('#lightbox').close();
    toast('Composer reloaded with those settings.');
  });
  $('#btnLbDelete').addEventListener('click', async () => {
    if (!lightboxClip) return;
    await deleteClip(lightboxClip.id);
    URL.revokeObjectURL(objectUrls.get(lightboxClip.id));
    objectUrls.delete(lightboxClip.id);
    shots = shots.map((s) => (s.clipId === lightboxClip.id ? { ...s, clipId: null } : s));
    saveStoryboard(shots);
    renderShots();
    $('#lightbox').close();
    await refreshGallery();
  });
  $('#lightbox').addEventListener('close', () => {
    const v = $('#lightboxVideo');
    v.pause();
    v.removeAttribute('src');
    v.load();
    lightboxClip = null;
  });
}

function openLightbox(clip) {
  lightboxClip = clip;
  const v = $('#lightboxVideo');
  v.src = clipUrl(clip);
  v.loop = false;
  v.play().catch(() => {});
  $('#lightboxMeta').textContent =
    `${clip.providerLabel} · ${clip.modelLabel} · ${clip.spec?.aspectRatio ?? ''} ${clip.spec?.resolution ?? ''} · ` +
    `${formatBytes(clip.size)}\n${clip.prompt}`;
  $('#btnLbDelete').classList.remove('hidden');
  $('#btnLbReuse').classList.remove('hidden');
  $('#lightbox').showModal();
}

// ── storyboard ─────────────────────────────────────────────
function wireStoryboard() {
  $('#btnAddShot').addEventListener('click', () => {
    shots.push({ id: uid(), text: '', clipId: null, jobId: null });
    saveStoryboard(shots);
    renderShots();
  });
  $('#btnGenerateBoard').addEventListener('click', generateBoard);
  $('#btnPlayBoard').addEventListener('click', playSequence);
}

function renderShots() {
  $('#shotEmpty').classList.toggle('hidden', shots.length > 0);
  $('#shotList').replaceChildren(...shots.map((shot, i) => {
    const job = shot.jobId ? getJob(shot.jobId) : null;
    const clip = shot.clipId ? clips.find((c) => c.id === shot.clipId) : null;
    const state = clip ? 'ready' : job ? job.status : 'not generated';

    return el('div', { class: 'shot' },
      el('span', { class: 'shot-num', text: String(i + 1) }),
      el('div', {},
        el('textarea', {
          rows: 2,
          placeholder: `Shot ${i + 1} — describe the action and camera`,
          oninput: (e) => { shot.text = e.target.value; saveStoryboard(shots); },
        }, shot.text ?? ''),
      ),
      el('div', { class: 'shot-side' },
        el('span', { class: 'shot-state', text: state }),
        clip ? el('button', { class: 'btn btn-ghost btn-sm', text: 'View', onclick: () => openLightbox(clip) }) : null,
        el('button', {
          class: 'btn btn-ghost btn-sm', text: 'Remove',
          onclick: () => {
            shots = shots.filter((s) => s.id !== shot.id);
            saveStoryboard(shots);
            renderShots();
          },
        }),
      ),
    );
  }));
}

function generateBoard() {
  const pending = shots.filter((s) => (s.text ?? '').trim() && !s.clipId);
  if (!pending.length) return toast('Every shot with text already has a clip.');

  for (const shot of pending) {
    const spec = buildSpec({ prompt: shot.text, image: null });
    spec.rawPrompt = shot.text.trim();
    const job = enqueue(spec, { shotId: shot.id });
    shot.jobId = job.id;
  }
  saveStoryboard(shots);
  renderShots();
  toast(`Queued ${pending.length} shot${pending.length === 1 ? '' : 's'}.`, 'ok');
  document.querySelector('.tab[data-tab="queue"]')?.click();
}

function attachClipsToShot(jobId, clipIds) {
  const job = getJob(jobId);
  if (!job?.shotId) return;
  const shot = shots.find((s) => s.id === job.shotId);
  if (!shot) return;
  shot.clipId = clipIds[0] ?? null;
  saveStoryboard(shots);
  renderShots();
}

/** Play the storyboard's clips back to back in the lightbox — a cheap rough cut. */
function playSequence() {
  const ordered = shots.map((s) => clips.find((c) => c.id === s.clipId)).filter(Boolean);
  if (!ordered.length) return toast('No generated shots to play yet.');

  const v = $('#lightboxVideo');
  let index = 0;
  lightboxClip = ordered[0];

  const playNext = () => {
    if (index >= ordered.length) { v.removeEventListener('ended', playNext); return; }
    const clip = ordered[index++];
    lightboxClip = clip;
    $('#lightboxMeta').textContent = `Sequence ${index}/${ordered.length} · ${clip.prompt}`;
    v.src = clipUrl(clip);
    v.play().catch(() => {});
  };

  v.addEventListener('ended', playNext);
  $('#lightbox').addEventListener('close', () => v.removeEventListener('ended', playNext), { once: true });
  $('#btnLbDelete').classList.add('hidden');
  $('#btnLbReuse').classList.add('hidden');
  $('#lightbox').showModal();
  playNext();
}
