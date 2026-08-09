// Provider registry + the request context handed to every adapter.

import { gemini } from './gemini.js';
import { runway } from './runway.js';
import { luma } from './luma.js';
import { fal } from './fal.js';
import { getKey, settings } from '../state.js';
import { ApiError } from '../util.js';

export const providers = [gemini, runway, luma, fal];

export const getProvider = (id) => providers.find((p) => p.id === id) ?? gemini;

/** Models the UI should offer: whatever a live lookup found, else the built-in list. */
const discovered = new Map();

export function modelsFor(provider) {
  return discovered.get(provider.id) ?? provider.defaultModels;
}

export function setDiscoveredModels(providerId, models) {
  if (models?.length) discovered.set(providerId, models);
}

export function findModel(provider, modelId) {
  const list = modelsFor(provider);
  return list.find((m) => m.id === modelId) ?? list[0];
}

/**
 * Builds the per-call context. `resolveUrl` is where the CORS story lives: adapters marked
 * `direct` hit the provider straight from the page, everything else is rewritten to go
 * through the user's proxy. Nothing is rewritten silently — a missing proxy throws with
 * an explanation rather than failing later as an opaque CORS error.
 */
export function makeContext(provider) {
  const apiKey = getKey(provider.id);
  if (!apiKey) {
    throw new ApiError(`No ${provider.keyLabel} saved. Add one under Settings.`);
  }
  const proxy = (settings.proxyUrl || '').trim();
  const noProxy = new ApiError(
    `${provider.label} blocks direct browser calls, so it needs a proxy. ` +
    `Deploy worker/cors-proxy.js and paste its URL under Settings.`
  );
  // Fail here rather than mid-submit, so the composer can block the click.
  if (!provider.direct && !proxy) throw noProxy;

  const resolveUrl = (url, { force = false } = {}) => {
    if (provider.direct && !force) return url;
    if (!proxy) throw noProxy;
    const base = proxy.endsWith('/') ? proxy : `${proxy}/`;
    return `${base}?url=${encodeURIComponent(url)}`;
  };

  return { apiKey, resolveUrl, provider };
}

/** Cost estimate for the composer. Published rates move — treat this as a guide. */
export function estimateCost(model, spec) {
  if (!model?.pricePerSecond) return null;
  const seconds = Number(spec.durationSeconds) || 0;
  const count = Number(spec.sampleCount) || 1;
  return model.pricePerSecond * seconds * count;
}
