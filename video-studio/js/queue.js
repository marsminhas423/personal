// The job engine: submit → poll → download → store.
//
// Every state transition is written to IndexedDB before it is announced, so closing the
// tab mid-render loses nothing: on the next load, jobs that already hold a provider
// handle re-attach to the same remote operation instead of paying to generate again.

import { getProvider, makeContext, findModel } from './providers/index.js';
import { settings } from './state.js';
import { putJob, deleteJob, allJobs, putClip } from './db.js';
import { uid, sleep, ApiError, describeApiBody } from './util.js';

const MAX_ATTEMPTS = 4;
const TERMINAL = new Set(['done', 'error', 'cancelled']);

export const queueEvents = new EventTarget();

/** id -> job, the authoritative in-memory view. */
const jobs = new Map();
/** Jobs with a live polling loop, so a resume never double-attaches. */
const active = new Set();
const cancelled = new Set();

export const listJobs = () => [...jobs.values()].sort((a, b) => b.createdAt - a.createdAt);
export const getJob = (id) => jobs.get(id);
export const isTerminal = (job) => TERMINAL.has(job.status);

function announce(job) {
  queueEvents.dispatchEvent(new CustomEvent('change', { detail: { job } }));
}

async function commit(job, patch = {}) {
  Object.assign(job, patch, { updatedAt: Date.now() });
  jobs.set(job.id, job);
  try {
    await putJob(structuredClone(job));
  } catch (err) {
    console.warn('Could not persist job', job.id, err);
  }
  announce(job);
}

/** Restore persisted jobs and resume anything that was still in flight. */
export async function initQueue() {
  const stored = await allJobs().catch(() => []);
  for (const job of stored) {
    jobs.set(job.id, job);
    // A job that was mid-submit when the tab closed has no handle: safe to re-queue.
    if (job.status === 'running' && !job.handle) job.status = 'queued';
  }
  announce(null);
  for (const job of listJobs()) {
    if (job.status === 'running' && job.handle) void runJob(job, { resume: true });
  }
  pump();
}

export function enqueue(spec, extra = {}) {
  const job = {
    id: uid(),
    createdAt: Date.now(),
    updatedAt: Date.now(),
    status: 'queued',
    providerId: spec.providerId,
    modelId: spec.modelId,
    spec,
    handle: null,
    progress: null,
    label: null,
    error: null,
    attempts: 0,
    clipIds: [],
    ...extra,
  };
  jobs.set(job.id, job);
  void commit(job);
  pump();
  return job;
}

export function cancel(id) {
  const job = jobs.get(id);
  if (!job || isTerminal(job)) return;
  cancelled.add(id);
  void commit(job, { status: 'cancelled', label: null, progress: null });
  pump();
}

export function cancelPending() {
  for (const job of jobs.values()) if (job.status === 'queued') cancel(job.id);
}

export async function removeJob(id) {
  jobs.delete(id);
  cancelled.delete(id);
  await deleteJob(id).catch(() => {});
  announce(null);
}

export async function clearFinished() {
  for (const job of [...jobs.values()]) {
    if (isTerminal(job)) {
      jobs.delete(job.id);
      await deleteJob(job.id).catch(() => {});
    }
  }
  announce(null);
}

export function retry(id) {
  const job = jobs.get(id);
  if (!job) return;
  cancelled.delete(id);
  void commit(job, { status: 'queued', error: null, attempts: 0, handle: null, progress: null, label: null });
  pump();
}

/** Start as many queued jobs as the concurrency budget allows. */
function pump() {
  const limit = Math.max(1, Number(settings.concurrency) || 2);
  const running = [...jobs.values()].filter((j) => j.status === 'running').length;
  let slots = limit - running;
  if (slots <= 0) return;
  for (const job of listJobs().reverse()) {
    if (slots <= 0) break;
    if (job.status !== 'queued' || active.has(job.id)) continue;
    slots--;
    void runJob(job);
  }
}

async function runJob(job, { resume = false } = {}) {
  if (active.has(job.id)) return;
  active.add(job.id);

  try {
    const provider = getProvider(job.providerId);
    const ctx = makeContext(provider);

    if (!resume || !job.handle) {
      await commit(job, { status: 'running', label: 'submitting', error: null });
      const model = findModel(provider, job.modelId);
      const handle = await withRetries(job, () => provider.submit({ ...job.spec, caps: model?.caps }, ctx));
      if (cancelled.has(job.id)) return;
      await commit(job, { handle, label: 'queued at provider' });
    } else {
      await commit(job, { status: 'running', label: 'reconnecting', error: null });
    }

    const result = await pollUntilDone(job, provider, ctx);
    if (cancelled.has(job.id)) return;

    await commit(job, { label: 'downloading', progress: null });
    const clipIds = [];
    for (const [i, video] of result.videos.entries()) {
      if (cancelled.has(job.id)) return;
      const blob = await withRetries(job, () => provider.download(video, ctx));
      const clip = {
        id: uid(),
        jobId: job.id,
        createdAt: Date.now() + i,
        providerId: job.providerId,
        providerLabel: provider.label,
        modelId: job.modelId,
        modelLabel: findModel(provider, job.modelId)?.label ?? job.modelId,
        prompt: job.spec.prompt,
        spec: job.spec,
        mimeType: blob.type || video.mimeType || 'video/mp4',
        size: blob.size,
        blob,
      };
      await putClip(clip);
      clipIds.push(clip.id);
    }

    await commit(job, { status: 'done', label: null, progress: 100, clipIds });
    queueEvents.dispatchEvent(new CustomEvent('clips', { detail: { jobId: job.id, clipIds } }));
  } catch (err) {
    if (cancelled.has(job.id)) return;
    const message = err instanceof ApiError
      ? [err.message, describeApiBody(err.body)].filter(Boolean).join('\n').slice(0, 900)
      : (err?.message ?? String(err));
    await commit(job, { status: 'error', error: message, label: null, progress: null });
  } finally {
    active.delete(job.id);
    cancelled.delete(job.id);
    pump();
  }
}

async function pollUntilDone(job, provider, ctx) {
  const intervalMs = Math.max(2, Number(settings.pollIntervalSec) || 8) * 1000;
  const startedAt = Date.now();
  const timeoutMs = 30 * 60 * 1000; // a stuck operation should not poll forever

  for (;;) {
    if (cancelled.has(job.id)) throw new Error('cancelled');
    if (Date.now() - startedAt > timeoutMs) {
      throw new ApiError('Gave up after 30 minutes without a result. The provider job may still be running.');
    }
    await sleep(intervalMs);
    if (cancelled.has(job.id)) throw new Error('cancelled');

    const status = await withRetries(job, () => provider.poll(job.handle, ctx));
    if (status.done) return status;

    const elapsed = Math.round((Date.now() - startedAt) / 1000);
    await commit(job, {
      progress: status.progress ?? null,
      label: `${status.label ?? 'rendering'} · ${elapsed}s`,
    });
  }
}

/** Retry transient failures only; a 400 from a bad parameter should surface immediately. */
async function withRetries(job, fn) {
  let lastErr;
  for (let attempt = 1; attempt <= MAX_ATTEMPTS; attempt++) {
    if (cancelled.has(job.id)) throw new Error('cancelled');
    try {
      return await fn();
    } catch (err) {
      lastErr = err;
      const retryable = err instanceof ApiError ? err.retryable : false;
      if (!retryable || attempt === MAX_ATTEMPTS) throw err;
      await commit(job, { label: `retrying (${attempt}/${MAX_ATTEMPTS - 1})` });
      await sleep(2000 * 2 ** (attempt - 1)); // 2s, 4s, 8s
    }
  }
  throw lastErr;
}
