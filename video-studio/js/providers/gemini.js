// Google Veo via the Gemini API (generativelanguage.googleapis.com).
//
// This is the only adapter here that works with no extra infrastructure: the host sends
// permissive CORS headers and accepts `x-goog-api-key`, so the browser can call it directly.
//
//   POST /v1beta/models/{model}:predictLongRunning  -> { name: "models/…/operations/…" }
//   GET  /v1beta/{operation.name}                   -> { done, response | error }
//   GET  {sample.video.uri}?alt=media               -> the mp4 bytes (needs the key again)

import { requestJSON, requestBlob, splitDataURL, ApiError } from '../util.js';

const BASE = 'https://generativelanguage.googleapis.com/v1beta';

const CAPS = {
  aspectRatios: ['16:9', '9:16'],
  resolutions: ['720p', '1080p'],
  durations: [4, 6, 8],
  maxSamples: 4,
  audio: true,
  negativePrompt: true,
  imageToVideo: true,
  seed: true,
};

export const gemini = {
  id: 'gemini',
  label: 'Google Veo (Gemini API)',
  short: 'Veo',
  keyLabel: 'Gemini API key',
  keyHelp: 'https://aistudio.google.com/apikey',
  direct: true, // no proxy required — CORS verified against this host
  note: 'Works straight from the browser. Veo renders 720p/1080p clips with native audio.',

  defaultModels: [
    { id: 'veo-3.1-generate-preview', label: 'Veo 3.1', caps: CAPS, pricePerSecond: 0.40 },
    { id: 'veo-3.1-fast-generate-preview', label: 'Veo 3.1 Fast', caps: CAPS, pricePerSecond: 0.15 },
    { id: 'veo-3.0-generate-001', label: 'Veo 3', caps: CAPS, pricePerSecond: 0.40 },
    { id: 'veo-3.0-fast-generate-001', label: 'Veo 3 Fast', caps: CAPS, pricePerSecond: 0.15 },
    {
      id: 'veo-2.0-generate-001',
      label: 'Veo 2',
      caps: { ...CAPS, audio: false, resolutions: ['720p'], durations: [5, 6, 7, 8] },
      pricePerSecond: 0.35,
    },
  ],

  headers(ctx) {
    return { 'content-type': 'application/json', 'x-goog-api-key': ctx.apiKey };
  },

  /**
   * Model IDs churn fast. Rather than trusting the hard-coded list above, this asks the
   * API which models actually accept predictLongRunning for the caller's key.
   */
  async listModels(ctx) {
    const data = await requestJSON(ctx.resolveUrl(`${BASE}/models?pageSize=1000`), {
      headers: this.headers(ctx),
    });
    const known = new Map(this.defaultModels.map((m) => [m.id, m]));
    return (data.models ?? [])
      .filter((m) => (m.supportedGenerationMethods ?? []).includes('predictLongRunning'))
      .map((m) => {
        const id = String(m.name).replace(/^models\//, '');
        const prior = known.get(id);
        return {
          id,
          label: m.displayName || prior?.label || id,
          caps: prior?.caps ?? CAPS,
          pricePerSecond: prior?.pricePerSecond ?? null,
        };
      })
      .sort((a, b) => b.id.localeCompare(a.id));
  },

  async submit(spec, ctx) {
    const caps = spec.caps ?? CAPS;
    const instance = { prompt: spec.prompt };

    if (spec.image && caps.imageToVideo) {
      const parsed = splitDataURL(spec.image.dataUrl);
      if (!parsed) throw new ApiError('Could not read the reference image.');
      instance.image = { bytesBase64Encoded: parsed.base64, mimeType: parsed.mimeType };
    }

    const parameters = {};
    if (spec.aspectRatio) parameters.aspectRatio = spec.aspectRatio;
    if (spec.resolution) parameters.resolution = spec.resolution;
    if (spec.durationSeconds) parameters.durationSeconds = Number(spec.durationSeconds);
    if (spec.sampleCount > 1) parameters.sampleCount = Number(spec.sampleCount);
    if (spec.negativePrompt && caps.negativePrompt) parameters.negativePrompt = spec.negativePrompt;
    if (spec.seed !== '' && spec.seed !== null && spec.seed !== undefined) parameters.seed = Number(spec.seed);
    if (caps.audio) parameters.generateAudio = Boolean(spec.generateAudio);

    const op = await requestJSON(
      ctx.resolveUrl(`${BASE}/models/${encodeURIComponent(spec.modelId)}:predictLongRunning`),
      { method: 'POST', headers: this.headers(ctx), body: JSON.stringify({ instances: [instance], parameters }) }
    );

    if (!op?.name) throw new ApiError('The API accepted the request but returned no operation name.');
    return { operation: op.name };
  },

  async poll(handle, ctx) {
    const op = await requestJSON(ctx.resolveUrl(`${BASE}/${handle.operation}`), {
      headers: this.headers(ctx),
    });

    if (!op.done) {
      // Veo reports no percentage, so this stays indeterminate on purpose.
      return { done: false };
    }
    if (op.error) {
      throw new ApiError(op.error.message || 'Generation failed.', { body: op.error, status: op.error.code });
    }

    const payload = op.response?.generateVideoResponse ?? op.response ?? {};
    const samples = payload.generatedSamples ?? payload.videos ?? [];
    const videos = samples
      .map((s) => s.video?.uri ?? s.video?.url ?? s.uri ?? s.url)
      .filter(Boolean)
      .map((url) => ({ url, mimeType: 'video/mp4' }));

    if (!videos.length) {
      // Safety filters return a completed operation with nothing in it — say why.
      const filtered = payload.raiMediaFilteredCount ?? 0;
      const reasons = (payload.raiMediaFilteredReasons ?? []).join('; ');
      throw new ApiError(
        filtered
          ? `Blocked by content filters${reasons ? `: ${reasons}` : ''}. Try rephrasing the prompt.`
          : 'The operation finished but returned no video.',
        { body: payload }
      );
    }
    return { done: true, videos };
  },

  async download(video, ctx) {
    const url = new URL(video.url);
    if (!url.searchParams.has('alt')) url.searchParams.set('alt', 'media');
    return requestBlob(ctx.resolveUrl(url.toString()), {
      headers: { 'x-goog-api-key': ctx.apiKey },
    });
  },
};
