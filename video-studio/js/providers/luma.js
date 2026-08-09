// Luma Dream Machine (api.lumalabs.ai/dream-machine/v1).
//
// Server-side oriented API, so calls are routed through the configured proxy.
//
//   POST /generations        -> { id, state }
//   GET  /generations/{id}   -> { state: queued|dreaming|completed|failed, assets: { video } }

import { requestJSON, ApiError } from '../util.js';
import { downloadViaCdnOrProxy } from './runway.js';

const BASE = 'https://api.lumalabs.ai/dream-machine/v1';

const CAPS = {
  aspectRatios: ['16:9', '9:16', '1:1', '4:3', '3:4', '21:9'],
  resolutions: ['540p', '720p', '1080p', '4k'],
  durations: [5, 9],
  maxSamples: 1,
  audio: false,
  negativePrompt: false,
  imageToVideo: true,
  seed: false,
};

export const luma = {
  id: 'luma',
  label: 'Luma Dream Machine',
  short: 'Luma',
  keyLabel: 'Luma API key',
  keyHelp: 'https://lumalabs.ai/dream-machine/api/keys',
  direct: false,
  note: 'Requires a proxy. Reference images must be public https URLs — Luma does not accept uploads inline.',

  defaultModels: [
    { id: 'ray-2', label: 'Ray 2', caps: CAPS, pricePerSecond: 0.16 },
    { id: 'ray-flash-2', label: 'Ray 2 Flash', caps: CAPS, pricePerSecond: 0.08 },
    { id: 'ray-1-6', label: 'Ray 1.6', caps: { ...CAPS, resolutions: ['720p'] }, pricePerSecond: 0.10 },
  ],

  headers(ctx) {
    return {
      'content-type': 'application/json',
      accept: 'application/json',
      authorization: `Bearer ${ctx.apiKey}`,
    };
  },

  async submit(spec, ctx) {
    const body = {
      generation_type: 'video',
      model: spec.modelId,
      prompt: spec.prompt,
      aspect_ratio: spec.aspectRatio || '16:9',
      resolution: spec.resolution || '720p',
      duration: `${Number(spec.durationSeconds) || 5}s`,
      loop: false,
    };

    if (spec.image) {
      if (!/^https?:\/\//i.test(spec.image.url ?? '')) {
        throw new ApiError(
          'Luma needs a publicly reachable image URL for the first frame; an uploaded file cannot be sent inline. ' +
          'Host the image and paste its URL, or use Veo/Runway for image-to-video.'
        );
      }
      body.keyframes = { frame0: { type: 'image', url: spec.image.url } };
    }

    const gen = await requestJSON(ctx.resolveUrl(`${BASE}/generations`), {
      method: 'POST',
      headers: this.headers(ctx),
      body: JSON.stringify(body),
    });
    if (!gen?.id) throw new ApiError('Luma returned no generation id.', { body: gen });
    return { generationId: gen.id };
  },

  async poll(handle, ctx) {
    const gen = await requestJSON(ctx.resolveUrl(`${BASE}/generations/${handle.generationId}`), {
      headers: this.headers(ctx),
    });
    const state = String(gen.state ?? '').toLowerCase();

    if (state === 'failed') {
      throw new ApiError(gen.failure_reason || 'Luma generation failed.', { body: gen });
    }
    if (state !== 'completed') {
      return { done: false, label: state || 'queued' };
    }

    const url = gen.assets?.video;
    if (!url) throw new ApiError('Luma completed without a video asset.', { body: gen });
    return { done: true, videos: [{ url, mimeType: 'video/mp4' }] };
  },

  download: downloadViaCdnOrProxy,
};
