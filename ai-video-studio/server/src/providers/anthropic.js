// Script + storyboard generation via Claude. Falls back to a deterministic
// local template generator when no API key is configured, so the rest of
// the pipeline (voice, visuals, assembly) can still be exercised for free.
import fetch from "node-fetch";
import { getEffectiveConfig } from "../storage/config.js";

const ANTHROPIC_URL = "https://api.anthropic.com/v1/messages";
const MODEL = "claude-sonnet-4-5-20250929";

function buildPrompt(project) {
  const chars = (project.characters || [])
    .map((c) => `- ${c.name} (${c.gender || "unspecified"}): ${c.description || c.role || ""}`)
    .join("\n") || "- (no named characters specified; invent as needed)";

  return `You are a professional ad/short-film director and screenwriter. Create a shot-by-shot storyboard for a ${project.durationSeconds}-second ${project.videoType} video.

Purpose / idea: ${project.idea}
Theme: ${project.theme || "n/a"}
Tone: ${project.tone}
Visual style: ${project.style}
Aspect ratio: ${project.aspectRatio}
Characters:
${chars}

Break it into 3-8 scenes that together total approximately ${project.durationSeconds} seconds. For each scene provide:
- a vivid visual description suitable as a prompt for an AI video/image generator (camera angle, setting, action, lighting, who's in it)
- spoken dialogue or narration line for that scene (can be empty string if purely visual)
- which character (by name) is speaking, if any
- duration in seconds for that scene

Respond ONLY with strict JSON in this exact shape, no markdown fences, no commentary:
{
  "title": "string",
  "logline": "one sentence summary",
  "scenes": [
    { "description": "string", "dialogue": "string", "speaker": "string or null", "durationSeconds": number }
  ]
}`;
}

export async function generateScriptWithClaude(project) {
  const { anthropicApiKey } = getEffectiveConfig();
  if (!anthropicApiKey) return null;

  const res = await fetch(ANTHROPIC_URL, {
    method: "POST",
    headers: {
      "content-type": "application/json",
      "x-api-key": anthropicApiKey,
      "anthropic-version": "2023-06-01",
    },
    body: JSON.stringify({
      model: MODEL,
      max_tokens: 2000,
      messages: [{ role: "user", content: buildPrompt(project) }],
    }),
  });

  if (!res.ok) {
    const text = await res.text();
    throw new Error(`Anthropic API error ${res.status}: ${text}`);
  }
  const data = await res.json();
  const text = data.content?.map((c) => c.text).join("") || "{}";
  const jsonStart = text.indexOf("{");
  const jsonEnd = text.lastIndexOf("}");
  const jsonStr = text.slice(jsonStart, jsonEnd + 1);
  return JSON.parse(jsonStr);
}

export function generateScriptTemplate(project) {
  // Free, offline fallback: splits the idea into a simple 4-scene arc so the
  // whole pipeline is runnable with zero API keys.
  const total = project.durationSeconds || 20;
  const per = Math.max(3, Math.round(total / 4));
  const speaker = project.characters?.[0]?.name || null;
  const idea = project.idea || "your product or story";

  return {
    title: project.theme ? `${project.theme}` : "Untitled Project",
    logline: idea,
    scenes: [
      {
        description: `Establishing shot introducing the setting for: ${idea}. ${project.style} style, ${project.tone} tone.`,
        dialogue: "",
        speaker: null,
        durationSeconds: per,
      },
      {
        description: `Close-up showing the core subject/product/character in action. ${project.style} style.`,
        dialogue: project.scriptMode === "manual" ? "" : `Here's what makes this special.`,
        speaker,
        durationSeconds: per,
      },
      {
        description: `Emotional beat — reaction shot or moment of connection related to: ${idea}.`,
        dialogue: project.scriptMode === "manual" ? "" : `This is why it matters.`,
        speaker,
        durationSeconds: per,
      },
      {
        description: `Closing shot with clear resolution / call-to-action framing.`,
        dialogue: project.scriptMode === "manual" ? "" : `Try it for yourself.`,
        speaker,
        durationSeconds: total - per * 3,
      },
    ],
  };
}
