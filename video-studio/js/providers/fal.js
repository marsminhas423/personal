// fal.ai queue API (queue.fal.run).
//
// One key reaches Veo, Kling, Sora, Wan, Seedance and the rest, which makes it the
// pragmatic way to compare models. fal explicitly tells you not to ship FAL_KEY to a
// browser, so this adapter is proxy-only too.
//
//   POST /{model}                       -> { request_id, status_url, response_url }
//   GET  {status_url}                   -> { status: IN_QUEUE|IN_PROGRESS|COMPLETED }
//   GET  {response_url}                 -> { video: { url } }

import { requestJSON, ApiError } from '../util.js';
import { downloadViaCdnOrProxy } from './runway.js';

const BASE = 'https://queue.fal.run';

const CAPS = {
  aspectRatios: ['16:9', '9:16', '1:1'],
  resolutions: ['480p', '720p', '1080p'],
  durations: [4, 5, 6, 8, 10],
  maxSamples: 1,
  audio: true,
  negativePrompt: true,
  imageToVideo: true,
  seed: true,
};

export const fal = {
  id: 'fal',
  label: 'fal.ai (multi-model)',
  short: 'fal',
  keyLabel: 'fal API key',
  keyHelp: 'https://fal.ai/dashboard/keys',
  direct: false,
  note: 'Requires a proxy. One key, many models — handy for comparing Veo against Kling or Wan.',

  defaultModels: [
    { id: 'fal-ai/veo3', label: 'Veo 3', caps: CAPS, pricePerSecond: 0.40 },
    { id: 'fal-ai/veo3/fast', label: 'Veo 3 Fast', caps: CAPS, pricePerSecond: 0.15 },
    { id: 'fal-ai/kling-video/v2.1/pro/text-to-video', label: 'Kling 2.1 Pro', caps: { ...CAPS, audio: false }, pricePerSecond: 0.09 },
    { id: 'fal-ai/minimax/hailuo-02/standard/text-to-video', label: 'Hailuo 02', caps: { ...CAPS, audio: false }, pricePerSecond: 0.045 },
    { id: 'fal-ai/wan-25-preview/text-to-video', label: 'Wan 2.5', caps: CAPS, pricePerSecond: 0.05 },
  ],

  headers(ctx) {
    return { 'content-type': 'application/json', authorization: `Key ${ctx.apiKey}` };
  },

  /** Image-to-video lives at a sibling path on fal; swap the suffix when a frame is supplied. */
  resolveModelPath(spec) {
    const id = spec.modelId;
    if (!spec.image) return id;
    if (id.includes('text-to-video')) return id.replace('text-to-video', 'image-to-video');
    if (id.startsWith('fal-ai/veo3')) return `${id}/image-to-video`;
    return id;
  },

  async submit(spec, ctx) {
    const input = {
      prompt: spec.prompt,
      aspect_ratio: spec.aspectRatio || '16:9',
      duration: `${Number(spec.durationSeconds) || 5}s`,
      resolution: spec.resolution || '720p',
      generate_audio: Boolean(spec.generateAudio),
    };
    if (spec.negativePrompt) input.negative_prompt = spec.negativePrompt;
    if (spec.seed !== '' && spec.seed !== null && spec.seed !== undefined) input.seed = Number(spec.seed);
    if (spec.image) input.image_url = spec.image.url || spec.image.dataUrl; // fal accepts data URIs

    const path = this.resolveModelPath(spec);
    const queued = await requestJSON(ctx.resolveUrl(`${BASE}/${path}`), {
      method: 'POST',
      headers: this.headers(ctx),
      body: JSON.stringify(input),
    });
    if (!queued?.request_id) throw new ApiError('fal returned no request id.', { body: queued });

    return {
      requestId: queued.request_id,
      statusUrl: queued.status_url ?? `${BASE}/${path}/requests/${queued.request_id}/status`,
      responseUrl: queued.response_url ?? `${BASE}/${path}/requests/${queued.request_id}`,
    };
  },

  async poll(handle, ctx) {
    const status = await requestJSON(ctx.resolveUrl(handle.statusUrl), { headers: this.headers(ctx) });
    const state = String(status.status ?? '').toUpperCase();

    if (state === 'FAILED' || state === 'ERROR') {
      throw new ApiError(status.error || 'fal request failed.', { body: status });
    }
    if (state !== 'COMPLETED' && state !== 'OK') {
      const label = state === 'IN_QUEUE'
        ? `queued${status.queue_position != null ? ` #${status.queue_position}` : ''}`
        : 'rendering';
      return { done: false, label };
    }

    const result = await requestJSON(ctx.resolveUrl(handle.responseUrl), { headers: this.headers(ctx) });
    const url = result.video?.url ?? result.videos?.[0]?.url ?? result.output?.video?.url;
    if (!url) throw new ApiError('fal completed without a video URL.', { body: result });
    return { done: true, videos: [{ url, mimeType: result.video?.content_type ?? 'video/mp4' }] };
  },

  download: downloadViaCdnOrProxy,
};
