import { useEffect, useState } from "react";
import { api } from "../api/client.js";

export default function SettingsPanel({ onBack }) {
  const [status, setStatus] = useState(null);
  const [keys, setKeys] = useState({ anthropicApiKey: "", replicateApiToken: "", elevenLabsApiKey: "" });
  const [saving, setSaving] = useState(false);
  const [savedMsg, setSavedMsg] = useState("");

  useEffect(() => {
    api.getSettings().then(setStatus);
  }, []);

  async function handleSave(e) {
    e.preventDefault();
    setSaving(true);
    setSavedMsg("");
    try {
      const s = await api.saveSettings(keys);
      setStatus(s);
      setKeys({ anthropicApiKey: "", replicateApiToken: "", elevenLabsApiKey: "" });
      setSavedMsg("Saved. Keys are stored locally in data/config.json on this machine only.");
    } finally {
      setSaving(false);
    }
  }

  return (
    <div style={{ maxWidth: 640 }}>
      <button className="ghost" onClick={onBack} style={{ marginBottom: 12 }}>
        ← Back
      </button>
      <h2>Settings</h2>
      <p style={{ color: "var(--text-dim)" }}>
        This tool works with zero API keys (mock/placeholder mode) so you can try the full pipeline for free. Add keys
        below to get real AI-generated script polish, images/video, and realistic voices. Keys never leave this
        machine — they're saved to a local file and sent only to the provider they belong to.
      </p>

      {status && (
        <div className="card" style={{ marginBottom: 20 }}>
          <strong>Current providers in use</strong>
          <ul style={{ fontSize: 14, color: "var(--text-dim)" }}>
            <li>Script: {status.script}</li>
            <li>Visuals: {status.visuals}</li>
            <li>Voice: {status.voice}</li>
          </ul>
        </div>
      )}

      <form onSubmit={handleSave} className="card">
        <div className="field">
          <label>Anthropic API key (script writing — Claude)</label>
          <input
            type="password"
            placeholder={status?.hasAnthropic ? "•••••••••• (already set — leave blank to keep)" : "sk-ant-..."}
            value={keys.anthropicApiKey}
            onChange={(e) => setKeys((k) => ({ ...k, anthropicApiKey: e.target.value }))}
          />
        </div>
        <div className="field">
          <label>Replicate API token (cheapest realistic images/video + TTS)</label>
          <input
            type="password"
            placeholder={status?.hasReplicate ? "•••••••••• (already set — leave blank to keep)" : "r8_..."}
            value={keys.replicateApiToken}
            onChange={(e) => setKeys((k) => ({ ...k, replicateApiToken: e.target.value }))}
          />
          <p style={{ fontSize: 12, color: "var(--text-dim)" }}>
            Get one at <a href="https://replicate.com" target="_blank" rel="noreferrer">replicate.com</a> — pay-per-use,
            usually a few cents per clip.
          </p>
        </div>
        <div className="field">
          <label>ElevenLabs API key (optional — highest quality voice)</label>
          <input
            type="password"
            placeholder={status?.hasElevenLabs ? "•••••••••• (already set — leave blank to keep)" : "..."}
            value={keys.elevenLabsApiKey}
            onChange={(e) => setKeys((k) => ({ ...k, elevenLabsApiKey: e.target.value }))}
          />
        </div>
        <button type="submit" className="primary" disabled={saving}>
          {saving ? "Saving…" : "Save keys"}
        </button>
        {savedMsg && <p style={{ color: "var(--success)", fontSize: 13 }}>{savedMsg}</p>}
      </form>
    </div>
  );
}
