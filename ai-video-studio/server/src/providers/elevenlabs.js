// Realistic voiceover via ElevenLabs. Optional — used instead of Replicate's
// TTS model when an ElevenLabs key is present, since voice quality there is
// generally the most natural-sounding for narration/ad reads.
import fetch from "node-fetch";
import { getEffectiveConfig } from "../storage/config.js";

const API_BASE = "https://api.elevenlabs.io/v1";

// A few good default preset voices (public ElevenLabs voice IDs).
const VOICE_PRESETS = {
  female: "21m00Tcm4TlvDq8ikWAM", // Rachel
  male: "TxGEqnHWrfWFTfGW9XjX", // Josh
};

export async function generateSpeech({ text, gender = "female", voiceId }) {
  const { elevenLabsApiKey } = getEffectiveConfig();
  if (!elevenLabsApiKey) throw new Error("ElevenLabs API key not configured");

  const id = voiceId || VOICE_PRESETS[gender] || VOICE_PRESETS.female;
  const res = await fetch(`${API_BASE}/text-to-speech/${id}`, {
    method: "POST",
    headers: {
      "xi-api-key": elevenLabsApiKey,
      "content-type": "application/json",
      accept: "audio/mpeg",
    },
    body: JSON.stringify({
      text,
      model_id: "eleven_multilingual_v2",
      voice_settings: { stability: 0.5, similarity_boost: 0.75 },
    }),
  });
  if (!res.ok) {
    const errText = await res.text();
    throw new Error(`ElevenLabs API error ${res.status}: ${errText}`);
  }
  return Buffer.from(await res.arrayBuffer());
}
