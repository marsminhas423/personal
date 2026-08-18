// Zero-cost fallback "provider". When no video/voice API keys are
// configured, this generates real playable placeholder assets (a colored
// clip with the scene text burned in, and a silent audio track of correct
// length) using the local ffmpeg binary directly, so the whole pipeline
// still produces a watchable, correctly-timed mp4. Swap in real providers
// later without changing anything else.
//
// Two ffmpeg quirks worth knowing if you touch this file:
// 1. We shell out to the ffmpeg binary directly (execFile) instead of
//    fluent-ffmpeg's `.input()/.inputFormat()` API — its capability
//    pre-check misparses "virtual input device" demuxers like lavfi and
//    incorrectly reports them as unavailable even though the binary
//    supports them fine.
// 2. The ffmpeg-static prebuilt binary does not include the `drawtext`
//    filter, so instead of burning text in with drawtext we write a tiny
//    .ass subtitle file per scene and burn it in with the `subtitles`
//    filter (backed by libass, which the static build does include).
import fs from "fs";
import path from "path";
import os from "os";
import { execFile } from "child_process";
import { promisify } from "util";
import { nanoid } from "nanoid";
import ffmpegPath from "ffmpeg-static";

const run = promisify(execFile);

const BG_HEX = ["#1e3a5f", "#5f1e3a", "#1e5f3a", "#5f4a1e", "#3a1e5f"];

function dims(aspectRatio, resolution) {
  const heightBase = resolution === "4k" ? 2160 : resolution === "720p" ? 720 : 1080;
  if (aspectRatio === "9:16") return { w: Math.round((heightBase * 9) / 16 / 2) * 2, h: heightBase };
  if (aspectRatio === "1:1") return { w: heightBase, h: heightBase };
  return { w: Math.round((heightBase * 16) / 9 / 2) * 2, h: heightBase }; // 16:9
}

function assEscape(text) {
  return (text || "").replace(/\r?\n/g, "\\N").replace(/[{}]/g, "");
}

function buildAss({ w, h, title, dialogue, durationSeconds }) {
  const fontSize = Math.round(w / 18);
  const dialogueFontSize = Math.round(w / 24);
  const end = `0:00:${String(Math.max(1, Math.round(durationSeconds))).padStart(2, "0")}.00`;
  const lines = [
    "[Script Info]",
    "ScriptType: v4.00+",
    `PlayResX: ${w}`,
    `PlayResY: ${h}`,
    "ScaledBorderAndShadow: yes",
    "[V4+ Styles]",
    "Format: Name, Fontname, Fontsize, PrimaryColour, OutlineColour, BackColour, Bold, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV",
    `Style: Title,Arial,${fontSize},&H00FFFFFF,&H00000000,&H80000000,1,3,3,0,5,40,40,${Math.round(h * 0.08)}`,
    `Style: Dialogue,Arial,${dialogueFontSize},&H0000FFFF,&H00000000,&H80000000,0,3,2,0,2,40,40,${Math.round(h * 0.1)}`,
    "[Events]",
    "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    `Dialogue: 0,0:00:00.00,${end},Title,,0,0,0,,${assEscape(title)}`,
  ];
  if (dialogue) {
    lines.push(`Dialogue: 0,0:00:00.00,${end},Dialogue,,0,0,0,,${assEscape('"' + dialogue + '"')}`);
  }
  return lines.join("\n");
}

export async function generatePlaceholderClip({ outPath, description, dialogue, durationSeconds, aspectRatio, resolution, fps, index }) {
  const { w, h } = dims(aspectRatio, resolution);
  const bg = BG_HEX[index % BG_HEX.length];
  const title = `Scene ${index + 1}: ${description}`.slice(0, 200);

  const assPath = path.join(os.tmpdir(), `scene-${nanoid(6)}.ass`);
  fs.writeFileSync(assPath, buildAss({ w, h, title, dialogue, durationSeconds }));

  try {
    const args = [
      "-y",
      "-f", "lavfi",
      "-i", `color=c=${bg}:s=${w}x${h}:d=${durationSeconds}`,
      "-vf", `subtitles=${assPath}`,
      "-r", String(fps),
      "-pix_fmt", "yuv420p",
      outPath,
    ];
    await run(ffmpegPath, args);
    return outPath;
  } finally {
    fs.rmSync(assPath, { force: true });
  }
}

export async function generateSilentAudio({ outPath, durationSeconds }) {
  const args = [
    "-y",
    "-f", "lavfi",
    "-i", "anullsrc=r=44100:cl=stereo",
    "-t", String(durationSeconds),
    outPath,
  ];
  await run(ffmpegPath, args);
  return outPath;
}
