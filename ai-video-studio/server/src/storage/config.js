// Local-only settings store. API keys never leave this machine and are never
// committed to git (data/ is gitignored). This is intentionally a flat JSON
// file, not a database, since this tool is meant to run on one person's machine.
import fs from "fs";
import path from "path";
import { fileURLToPath } from "url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const DATA_DIR = path.join(__dirname, "..", "..", "..", "data");
const CONFIG_PATH = path.join(DATA_DIR, "config.json");

function ensureDataDir() {
  if (!fs.existsSync(DATA_DIR)) fs.mkdirSync(DATA_DIR, { recursive: true });
}

const DEFAULTS = {
  anthropicApiKey: "",
  replicateApiToken: "",
  elevenLabsApiKey: "",
};

export function readConfig() {
  ensureDataDir();
  if (!fs.existsSync(CONFIG_PATH)) return { ...DEFAULTS };
  try {
    const raw = JSON.parse(fs.readFileSync(CONFIG_PATH, "utf-8"));
    return { ...DEFAULTS, ...raw };
  } catch {
    return { ...DEFAULTS };
  }
}

export function writeConfig(partial) {
  ensureDataDir();
  const current = readConfig();
  const next = { ...current, ...partial };
  fs.writeFileSync(CONFIG_PATH, JSON.stringify(next, null, 2));
  return next;
}

// Env vars still win if set (useful for CI / power users), otherwise fall
// back to whatever was saved via the Settings screen in the UI.
export function getEffectiveConfig() {
  const saved = readConfig();
  return {
    anthropicApiKey: process.env.ANTHROPIC_API_KEY || saved.anthropicApiKey || "",
    replicateApiToken: process.env.REPLICATE_API_TOKEN || saved.replicateApiToken || "",
    elevenLabsApiKey: process.env.ELEVENLABS_API_KEY || saved.elevenLabsApiKey || "",
  };
}

export function providerStatus() {
  const cfg = getEffectiveConfig();
  return {
    script: cfg.anthropicApiKey ? "claude" : "template (mock)",
    visuals: cfg.replicateApiToken ? "replicate" : "placeholder (mock)",
    voice: cfg.elevenLabsApiKey
      ? "elevenlabs"
      : cfg.replicateApiToken
      ? "replicate-tts"
      : "silent (mock)",
    hasAnthropic: !!cfg.anthropicApiKey,
    hasReplicate: !!cfg.replicateApiToken,
    hasElevenLabs: !!cfg.elevenLabsApiKey,
  };
}
