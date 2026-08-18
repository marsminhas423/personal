import fs from "fs";
import path from "path";
import fetch from "node-fetch";
import { execFile } from "child_process";
import { promisify } from "util";
import ffmpegPath from "ffmpeg-static";
const runFfmpeg = promisify(execFile);

import { assetsDir, updateProject, appendLog, readProject } from "../storage/projects.js";
import { getEffectiveConfig } from "../storage/config.js";
import { generateScriptWithClaude, generateScriptTemplate } from "../providers/anthropic.js";
import * as replicate from "../providers/replicate.js";
import * as elevenlabs from "../providers/elevenlabs.js";
import { generatePlaceholderClip, generateSilentAudio } from "../providers/placeholder.js";
import { nanoid } from "nanoid";

async function downloadTo(url, outPath) {
  const res = await fetch(url);
  if (!res.ok) throw new Error(`Failed to download asset: ${res.status}`);
  const buf = Buffer.from(await res.arrayBuffer());
  fs.writeFileSync(outPath, buf);
  return outPath;
}

// --- Step 1: script + storyboard ---------------------------------------
export async function generateScript(projectId) {
  const project = readProject(projectId);
  appendLog(projectId, "Generating script & storyboard...");

  let script;
  if (project.scriptMode === "manual" && project.manualScript?.trim()) {
    // User supplied their own script — split into scenes on blank lines.
    const parts = project.manualScript.split(/\n\s*\n/).filter(Boolean);
    const per = Math.max(3, Math.round(project.durationSeconds / Math.max(1, parts.length)));
    script = {
      title: project.theme || "Untitled",
      logline: project.idea,
      scenes: parts.map((p) => ({ description: p.trim(), dialogue: p.trim(), speaker: null, durationSeconds: per })),
    };
  } else {
    try {
      script = await generateScriptWithClaude(project);
    } catch (e) {
      appendLog(projectId, `Claude script generation failed, using template fallback: ${e.message}`);
      script = null;
    }
    if (!script) script = generateScriptTemplate(project);
  }

  const scenes = script.scenes.map((s) => ({
    id: nanoid(8),
    description: s.description,
    dialogue: s.dialogue || "",
    speaker: s.speaker || null,
    durationSeconds: s.durationSeconds || 5,
    status: "pending", // pending -> generating -> ready -> failed
    imagePath: null,
    clipPath: null,
  }));

  return updateProject(projectId, { script, scenes, status: "scripted" });
}

// --- Step 2: per-scene visuals ------------------------------------------
export async function generateScene(projectId, sceneId) {
  const project = readProject(projectId);
  const scene = project.scenes.find((s) => s.id === sceneId);
  if (!scene) throw new Error("Scene not found");

  const dir = assetsDir(projectId);
  const idx = project.scenes.findIndex((s) => s.id === sceneId);
  const clipPath = path.join(dir, `scene-${idx}-${scene.id}.mp4`);
  const { replicateApiToken } = getEffectiveConfig();

  markScene(project, sceneId, { status: "generating" });

  try {
    if (replicateApiToken) {
      appendLog(projectId, `Scene ${idx + 1}: generating image via Replicate...`);
      const imageUrl = await replicate.generateImage({
        prompt: `${scene.description}. ${project.style} style, ${project.tone} tone, cinematic lighting, high detail.`,
        aspectRatio: project.aspectRatio,
      });
      appendLog(projectId, `Scene ${idx + 1}: animating image into video clip...`);
      const videoUrl = await replicate.generateVideoFromImage({
        prompt: scene.description,
        imageUrl,
        durationSeconds: scene.durationSeconds,
      });
      await downloadTo(videoUrl, clipPath);
    } else {
      appendLog(projectId, `Scene ${idx + 1}: no video provider configured, rendering placeholder clip.`);
      await generatePlaceholderClip({
        outPath: clipPath,
        description: scene.description,
        dialogue: scene.dialogue,
        durationSeconds: scene.durationSeconds,
        aspectRatio: project.aspectRatio,
        resolution: project.resolution,
        fps: project.fps,
        index: idx,
      });
    }
    const updated = markScene(readProject(projectId), sceneId, { status: "ready", clipPath });
    return updated;
  } catch (e) {
    appendLog(projectId, `Scene ${idx + 1} failed: ${e.message}`);
    return markScene(readProject(projectId), sceneId, { status: "failed" });
  }
}

function markScene(project, sceneId, patch) {
  const scenes = project.scenes.map((s) => (s.id === sceneId ? { ...s, ...patch } : s));
  return updateProject(project.id, { scenes });
}

export async function generateAllScenes(projectId) {
  const project = readProject(projectId);
  for (const scene of project.scenes) {
    await generateScene(projectId, scene.id);
  }
  return readProject(projectId);
}

// --- Step 3: voiceover ----------------------------------------------------
export async function generateVoiceover(projectId) {
  const project = readProject(projectId);
  const dir = assetsDir(projectId);
  const outPath = path.join(dir, "voiceover.mp3");
  const fullText = project.scenes.map((s) => s.dialogue).filter(Boolean).join(" ... ");
  const { elevenLabsApiKey, replicateApiToken } = getEffectiveConfig();

  if (!fullText.trim()) {
    appendLog(projectId, "No dialogue in script — skipping voiceover, using silence.");
    const silentPath = path.join(dir, "voiceover.wav");
    await generateSilentAudio({ outPath: silentPath, durationSeconds: project.durationSeconds });
    return updateProject(projectId, { voiceoverPath: silentPath });
  }

  try {
    if (elevenLabsApiKey) {
      appendLog(projectId, "Generating voiceover via ElevenLabs...");
      const buf = await elevenlabs.generateSpeech({ text: fullText, gender: project.voice?.gender });
      fs.writeFileSync(outPath, buf);
      return updateProject(projectId, { voiceoverPath: outPath });
    }
    if (replicateApiToken) {
      appendLog(projectId, "Generating voiceover via Replicate TTS...");
      const url = await replicate.generateSpeech({ text: fullText, gender: project.voice?.gender });
      await downloadTo(url, outPath);
      return updateProject(projectId, { voiceoverPath: outPath });
    }
  } catch (e) {
    appendLog(projectId, `Voiceover generation failed, falling back to silence: ${e.message}`);
  }

  appendLog(projectId, "No voice provider configured — generating silent placeholder track.");
  const silentPath = path.join(dir, "voiceover.wav");
  await generateSilentAudio({ outPath: silentPath, durationSeconds: project.durationSeconds });
  return updateProject(projectId, { voiceoverPath: silentPath });
}

// --- Step 4: assemble final video -----------------------------------------
export async function assembleVideo(projectId) {
  const project = readProject(projectId);
  const dir = assetsDir(projectId);

  const missing = project.scenes.some((s) => !s.clipPath || !fs.existsSync(s.clipPath));
  if (missing) throw new Error("Not all scenes have a rendered clip yet");

  updateProject(projectId, { status: "rendering" });
  appendLog(projectId, "Concatenating scene clips...");

  // The concat demuxer's mini-language also treats backslash as an escape
  // character, so Windows paths need forward slashes here too.
  const concatListPath = path.join(dir, "concat.txt");
  fs.writeFileSync(
    concatListPath,
    project.scenes
      .map((s) => `file '${s.clipPath.replace(/\\/g, "/").replace(/'/g, "'\\''")}'`)
      .join("\n")
  );

  const silentCombined = path.join(dir, "combined-video-only.mp4");
  await runFfmpeg(ffmpegPath, [
    "-y",
    "-f", "concat",
    "-safe", "0",
    "-i", concatListPath,
    "-c", "copy",
    silentCombined,
  ]);

  const finalPath = path.join(dir, "final.mp4");
  appendLog(projectId, "Muxing voiceover / music track...");
  const hasVoice = project.voiceoverPath && fs.existsSync(project.voiceoverPath);
  const muxArgs = hasVoice
    ? [
        "-y",
        "-i", silentCombined,
        "-i", project.voiceoverPath,
        "-c:v", "copy",
        "-c:a", "aac",
        "-shortest",
        "-map", "0:v:0",
        "-map", "1:a:0",
        finalPath,
      ]
    : ["-y", "-i", silentCombined, "-c", "copy", finalPath];
  await runFfmpeg(ffmpegPath, muxArgs);

  appendLog(projectId, "Export complete.");
  return updateProject(projectId, { status: "ready", finalVideoPath: finalPath });
}

// --- Convenience: run the whole pipeline end-to-end ------------------------
export async function runFullPipeline(projectId) {
  await generateScript(projectId);
  await generateAllScenes(projectId);
  await generateVoiceover(projectId);
  return assembleVideo(projectId);
}
