// Simple JSON-file-per-project storage. No database needed for a personal,
// single-user tool. Each project is a folder under data/projects/<id>/
// containing project.json plus generated assets (audio, clips, exports).
import fs from "fs";
import path from "path";
import { fileURLToPath } from "url";
import { nanoid } from "nanoid";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const PROJECTS_DIR = path.join(__dirname, "..", "..", "..", "data", "projects");

function ensureDir() {
  if (!fs.existsSync(PROJECTS_DIR)) fs.mkdirSync(PROJECTS_DIR, { recursive: true });
}

function projectDir(id) {
  return path.join(PROJECTS_DIR, id);
}

function projectFile(id) {
  return path.join(projectDir(id), "project.json");
}

export function assetsDir(id) {
  const dir = path.join(projectDir(id), "assets");
  if (!fs.existsSync(dir)) fs.mkdirSync(dir, { recursive: true });
  return dir;
}

export function listProjects() {
  ensureDir();
  return fs
    .readdirSync(PROJECTS_DIR)
    .filter((f) => fs.existsSync(projectFile(f)))
    .map((id) => readProject(id))
    .sort((a, b) => (b.updatedAt || "").localeCompare(a.updatedAt || ""));
}

export function readProject(id) {
  const p = projectFile(id);
  if (!fs.existsSync(p)) return null;
  return JSON.parse(fs.readFileSync(p, "utf-8"));
}

export function createProject(input) {
  ensureDir();
  const id = nanoid(10);
  fs.mkdirSync(projectDir(id), { recursive: true });
  const now = new Date().toISOString();
  const project = {
    id,
    createdAt: now,
    updatedAt: now,
    status: "draft", // draft -> scripted -> rendering -> ready -> failed
    // --- user-facing creative brief & customization options ---
    idea: input.idea || "",
    videoType: input.videoType || "ad", // ad | reel | short-story | series-episode
    theme: input.theme || "",
    tone: input.tone || "upbeat",
    style: input.style || "realistic", // realistic | cinematic | animated | documentary
    scriptMode: input.scriptMode || "auto", // auto | manual
    manualScript: input.manualScript || "",
    durationSeconds: input.durationSeconds || 20,
    aspectRatio: input.aspectRatio || "9:16", // 9:16 | 16:9 | 1:1
    resolution: input.resolution || "1080p", // 720p | 1080p | 4k
    quality: input.quality || "standard", // draft | standard | high
    fps: input.fps || 24,
    music: input.music !== false,
    captions: input.captions !== false,
    characters: input.characters || [
      // { name, gender, role, description, voiceId }
    ],
    voice: input.voice || { gender: "female", accent: "neutral", emotion: "warm" },
    // --- generated content, filled in by the pipeline ---
    script: null, // { title, logline, scenes: [...] }
    scenes: [], // [{ id, description, dialogue, durationSeconds, imagePath, clipPath, status }]
    voiceoverPath: null,
    finalVideoPath: null,
    log: [],
  };
  fs.writeFileSync(projectFile(id), JSON.stringify(project, null, 2));
  return project;
}

export function updateProject(id, patch) {
  const current = readProject(id);
  if (!current) throw new Error("Project not found");
  const next = { ...current, ...patch, updatedAt: new Date().toISOString() };
  fs.writeFileSync(projectFile(id), JSON.stringify(next, null, 2));
  return next;
}

export function appendLog(id, message) {
  const current = readProject(id);
  if (!current) return;
  const log = [...(current.log || []), { at: new Date().toISOString(), message }];
  return updateProject(id, { log });
}

export function deleteProject(id) {
  const dir = projectDir(id);
  if (fs.existsSync(dir)) fs.rmSync(dir, { recursive: true, force: true });
}
