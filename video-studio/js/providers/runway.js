// Runway (api.dev.runwayml.com).
//
// Runway documents its API as server-side only and does not advertise browser CORS, so
// every call here goes through the proxy configured in Settings. Requests are shaped from
// Runway's published REST contract; if they reject a field, the raw error is surfaced
// verbatim in the queue so it can be corrected in this file.
//
//   POST /v1/text_to_video | /v1/image_to_video -> { id }
//   GET  /v1/tasks/{id} -> { status, progress, output: [url] }

import { requestJSON, requestBlob, ApiError } from '../util.js';

const BASE = 'https://api.dev.runwayml.com/v1';
const API_VERSION = '2024-11-06';

// Runway takes explicit pixel ratios rather than "16:9".
const RATIO = {
  '16:9': '1280:720',
  '9:16': '720:1280',
  '1:1': '960:960',
  '4:3': '1104:832',
  '3:4': '832:1104',
  '21:9': '1584:672',
};

const CAPS = {
  aspectRatios: ['16:9', '9:16', '1:1', '4:3', '3:4', '21:9'],
  resolutions: ['720p'],
  durations: [5, 10],
  maxSamples: 1,
  audio: false,
  negativePrompt: false,
  imageToVideo: true,
  seed: true,
};

export const runway = {
  id: 'runway',
  label: 'Runway',
  short: 'Runway',
  keyLabel: 'Runway API key',
  keyHelp: 'https://dev.runwayml.com/',
  direct: false,
  note: 'Requires a proxy (Runway blocks browser calls). Gen-4 Turbo is strongest from a reference image.',

  defaultModels: [
    { id: 'gen4_turbo', label: 'Gen-4 Turbo', caps: CAPS, pricePerSecond: 0.05 },
    { id: 'gen4_aleph', label: 'Gen-4 Aleph', caps: CAPS, pricePerSecond: 0.15 },
    { id: 'gen3a_turbo', label: 'Gen-3 Alpha Turbo', caps: CAPS, pricePerSecond: 0.05 },
  ],

  headers(ctx) {
    return {
      'content-type': 'application/json',
      authorization: `Bearer ${ctx.apiKey}`,
      'x-runway-version': API_VERSION,
    };
  },

  async submit(spec, ctx) {
    const ratio = RATIO[spec.aspectRatio] ?? RATIO['16:9'];
    const body = {
      model: spec.modelId,
      ratio,
      duration: Number(spec.durationSeconds) || 5,
      promptText: spec.prompt,
    };
    if (spec.seed !== '' && spec.seed !== null && spec.seed !== undefined) body.seed = Number(spec.seed);

    let endpoint = `${BASE}/text_to_video`;
    if (spec.image) {
      // Runway accepts a data URI for promptImage as well as an https URL.
      endpoint = `${BASE}/image_to_video`;
      body.promptImage = spec.image.dataUrl;
    }

    const task = await requestJSON(ctx.resolveUrl(endpoint), {
      method: 'POST',
      headers: this.headers(ctx),
      body: JSON.stringify(body),
    });
    if (!task?.id) throw new ApiError('Runway returned no task id.', { body: task });
    return { taskId: task.id };
  },

  async poll(handle, ctx) {
    const task = await requestJSON(ctx.resolveUrl(`${BASE}/tasks/${handle.taskId}`), {
      headers: this.headers(ctx),
    });
    const status = String(task.status ?? '').toUpperCase();

    if (status === 'FAILED' || status === 'CANCELLED') {
      throw new ApiError(task.failure || task.failureCode || `Runway task ${status}`, { body: task });
    }
    if (status !== 'SUCCEEDED') {
      const pct = typeof task.progress === 'number' ? Math.round(task.progress * 100) : undefined;
      return { done: false, progress: pct, label: status.toLowerCase() };
    }

    const videos = (task.output ?? []).map((url) => ({ url, mimeType: 'video/mp4' }));
    if (!videos.length) throw new ApiError('Runway reported success but returned no output.', { body: task });
    return { done: true, videos };
  },

  download: downloadViaCdnOrProxy,
};

/** CDN links usually allow cross-origin reads; when they don't, fall back to the proxy. */
export async function downloadViaCdnOrProxy(video, ctx) {
  try {
    return await requestBlob(video.url);
  } catch {
    return requestBlob(ctx.resolveUrl(video.url, { force: true }));
  }
}
