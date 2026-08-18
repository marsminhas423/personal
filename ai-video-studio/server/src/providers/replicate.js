// Thin client for Replicate — one API token, many low-cost models. This is
// the "cheapest realistic" path: swap the model id below to trade cost vs
// quality (e.g. a fast image model for storyboarding, a video model like
// Kling/Minimax/Luma for final renders) without changing any other code.
import fetch from "node-fetch";
import { getEffectiveConfig } from "../storage/config.js";

const API_BASE = "https://api.replicate.com/v1";

// Reasonable low-cost defaults. Override via env if you want something else.
export const MODELS = {
  image: process.env.REPLICATE_IMAGE_MODEL || "black-forest-labs/flux-schnell",
  video: process.env.REPLICATE_VIDEO_MODEL || "minimax/video-01", // image-to-video capable, moderate cost
  tts: process.env.REPLICATE_TTS_MODEL || "minimax/speech-02-hd",
};

async function pollPrediction(id, apiToken) {
  const url = `${API_BASE}/predictions/${id}`;
  for (let i = 0; i < 120; i++) {
    const res = await fetch(url, { headers: { Authorization: `Bearer ${apiToken}` } });
    const data = await res.json();
    if (data.status === "succeeded") return data.output;
    if (data.status === "failed" || data.status === "canceled") {
      throw new Error(`Replicate prediction ${data.status}: ${data.error || "unknown error"}`);
    }
    await new Promise((r) => setTimeout(r, 2500));
  }
  throw new Error("Replicate prediction timed out");
}

async function runModel(modelRef, input) {
  const { replicateApiToken } = getEffectiveConfig();
  if (!replicateApiToken) throw new Error("Replicate API token not configured");

  const res = await fetch(`${API_BASE}/models/${modelRef}/predictions`, {
    method: "POST",
    headers: {
      Authorization: `Bearer ${replicateApiToken}`,
      "content-type": "application/json",
      Prefer: "wait=30",
    },
    body: JSON.stringify({ input }),
  });
  if (!res.ok) {
    const text = await res.text();
    throw new Error(`Replicate API error ${res.status}: ${text}`);
  }
  const data = await res.json();
  if (data.status === "succeeded") return data.output;
  return pollPrediction(data.id, replicateApiToken);
}

export async function generateImage({ prompt, aspectRatio }) {
  const output = await runModel(MODELS.image, {
    prompt,
    aspect_ratio: aspectRatio === "9:16" ? "9:16" : aspectRatio === "1:1" ? "1:1" : "16:9",
  });
  return Array.isArray(output) ? output[0] : output;
}

export async function generateVideoFromImage({ prompt, imageUrl, durationSeconds }) {
  const output = await runModel(MODELS.video, {
    prompt,
    first_frame_image: imageUrl,
    duration: Math.min(6, Math.max(3, Math.round(durationSeconds || 5))),
  });
  return Array.isArray(output) ? output[0] : output;
}

export async function generateSpeech({ text, gender = "female" }) {
  const output = await runModel(MODELS.tts, {
    text,
    voice_id: gender === "male" ? "male-qn-qingse" : "female-shaonv",
  });
  return Array.isArray(output) ? output[0] : output;
}
