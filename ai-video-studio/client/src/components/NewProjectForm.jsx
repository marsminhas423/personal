import { useState } from "react";
import { api } from "../api/client.js";

const TONES = ["upbeat", "warm", "dramatic", "playful", "luxurious", "gritty", "inspirational", "eerie", "comedic"];
const STYLES = [
  { v: "realistic", label: "Realistic / live-action" },
  { v: "cinematic", label: "Cinematic" },
  { v: "documentary", label: "Documentary" },
  { v: "animated", label: "Animated" },
];
const VIDEO_TYPES = [
  { v: "ad", label: "Ad / commercial" },
  { v: "reel", label: "Social reel" },
  { v: "short-story", label: "Short story" },
  { v: "series-episode", label: "Series episode" },
];

function emptyCharacter() {
  return { name: "", gender: "female", role: "", description: "" };
}

export default function NewProjectForm({ onCreated, onCancel }) {
  const [form, setForm] = useState({
    idea: "",
    videoType: "ad",
    theme: "",
    tone: "upbeat",
    style: "realistic",
    scriptMode: "auto",
    manualScript: "",
    durationSeconds: 20,
    aspectRatio: "9:16",
    resolution: "1080p",
    quality: "standard",
    fps: 24,
    music: true,
    captions: true,
    voice: { gender: "female", accent: "neutral", emotion: "warm" },
  });
  const [characters, setCharacters] = useState([]);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState(null);

  const set = (patch) => setForm((f) => ({ ...f, ...patch }));
  const setVoice = (patch) => setForm((f) => ({ ...f, voice: { ...f.voice, ...patch } }));

  function updateCharacter(idx, patch) {
    setCharacters((cs) => cs.map((c, i) => (i === idx ? { ...c, ...patch } : c)));
  }

  async function handleSubmit(e) {
    e.preventDefault();
    if (!form.idea.trim()) {
      setError("Describe what the video is for first.");
      return;
    }
    setSubmitting(true);
    setError(null);
    try {
      const project = await api.createProject({ ...form, characters });
      onCreated(project.id);
    } catch (err) {
      setError(err.message);
      setSubmitting(false);
    }
  }

  return (
    <form onSubmit={handleSubmit}>
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 20 }}>
        <h2 style={{ margin: 0 }}>New video</h2>
        <button type="button" className="ghost" onClick={onCancel}>
          Cancel
        </button>
      </div>

      <div className="card" style={{ marginBottom: 20 }}>
        <div className="field">
          <label>What's this video for?</label>
          <textarea
            placeholder="e.g. A 20-second ad for a cozy small-batch coffee brand — warm morning light, someone pouring coffee, feels like a slow Sunday. Or: a short dramatic scene where two old friends run into each other after years apart."
            value={form.idea}
            onChange={(e) => set({ idea: e.target.value })}
            style={{ minHeight: 110 }}
          />
        </div>

        <div className="row">
          <div className="field">
            <label>Video type</label>
            <select value={form.videoType} onChange={(e) => set({ videoType: e.target.value })}>
              {VIDEO_TYPES.map((t) => (
                <option key={t.v} value={t.v}>
                  {t.label}
                </option>
              ))}
            </select>
          </div>
          <div className="field">
            <label>Theme / product / title</label>
            <input
              type="text"
              placeholder="e.g. Morning Brew Co."
              value={form.theme}
              onChange={(e) => set({ theme: e.target.value })}
            />
          </div>
        </div>

        <div className="row">
          <div className="field">
            <label>Tone</label>
            <select value={form.tone} onChange={(e) => set({ tone: e.target.value })}>
              {TONES.map((t) => (
                <option key={t} value={t}>
                  {t}
                </option>
              ))}
            </select>
          </div>
          <div className="field">
            <label>Visual style</label>
            <select value={form.style} onChange={(e) => set({ style: e.target.value })}>
              {STYLES.map((s) => (
                <option key={s.v} value={s.v}>
                  {s.label}
                </option>
              ))}
            </select>
          </div>
        </div>
      </div>

      <div className="card" style={{ marginBottom: 20 }}>
        <div className="field" style={{ marginBottom: 12 }}>
          <label>Script</label>
          <div style={{ display: "flex", gap: 16, marginBottom: 10 }}>
            <label style={{ display: "flex", alignItems: "center", gap: 6, textTransform: "none", fontSize: 14 }}>
              <input
                type="radio"
                checked={form.scriptMode === "auto"}
                onChange={() => set({ scriptMode: "auto" })}
                style={{ width: "auto" }}
              />
              Auto-generate from my idea
            </label>
            <label style={{ display: "flex", alignItems: "center", gap: 6, textTransform: "none", fontSize: 14 }}>
              <input
                type="radio"
                checked={form.scriptMode === "manual"}
                onChange={() => set({ scriptMode: "manual" })}
                style={{ width: "auto" }}
              />
              I'll write my own script
            </label>
          </div>
          {form.scriptMode === "manual" && (
            <textarea
              placeholder="Write your script. Separate each scene/shot with a blank line."
              value={form.manualScript}
              onChange={(e) => set({ manualScript: e.target.value })}
            />
          )}
        </div>
      </div>

      <div className="card" style={{ marginBottom: 20 }}>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 12 }}>
          <label style={{ margin: 0 }}>Characters / actors</label>
          <button type="button" onClick={() => setCharacters((c) => [...c, emptyCharacter()])}>
            + Add character
          </button>
        </div>
        {characters.length === 0 && (
          <p style={{ color: "var(--text-dim)", fontSize: 13, margin: 0 }}>
            None yet — the generator will invent characters as needed if you leave this empty.
          </p>
        )}
        {characters.map((c, i) => (
          <div key={i} className="row" style={{ marginBottom: 10, alignItems: "flex-end" }}>
            <div className="field" style={{ marginBottom: 0 }}>
              <label>Name</label>
              <input type="text" value={c.name} onChange={(e) => updateCharacter(i, { name: e.target.value })} />
            </div>
            <div className="field" style={{ marginBottom: 0 }}>
              <label>Gender</label>
              <select value={c.gender} onChange={(e) => updateCharacter(i, { gender: e.target.value })}>
                <option value="female">Female</option>
                <option value="male">Male</option>
                <option value="nonbinary">Non-binary</option>
              </select>
            </div>
            <div className="field" style={{ marginBottom: 0 }}>
              <label>Role</label>
              <input
                type="text"
                placeholder="e.g. barista, protagonist"
                value={c.role}
                onChange={(e) => updateCharacter(i, { role: e.target.value })}
              />
            </div>
            <div className="field" style={{ marginBottom: 0, flex: 2 }}>
              <label>Appearance / description</label>
              <input
                type="text"
                placeholder="e.g. mid-30s, warm smile, flannel shirt"
                value={c.description}
                onChange={(e) => updateCharacter(i, { description: e.target.value })}
              />
            </div>
            <button type="button" className="danger" onClick={() => setCharacters((cs) => cs.filter((_, idx) => idx !== i))}>
              ✕
            </button>
          </div>
        ))}
      </div>

      <div className="card" style={{ marginBottom: 20 }}>
        <label style={{ marginBottom: 12 }}>Voice</label>
        <div className="row">
          <div className="field">
            <label>Narrator/primary voice gender</label>
            <select value={form.voice.gender} onChange={(e) => setVoice({ gender: e.target.value })}>
              <option value="female">Female</option>
              <option value="male">Male</option>
            </select>
          </div>
          <div className="field">
            <label>Accent</label>
            <select value={form.voice.accent} onChange={(e) => setVoice({ accent: e.target.value })}>
              <option value="neutral">Neutral</option>
              <option value="american">American</option>
              <option value="british">British</option>
              <option value="australian">Australian</option>
            </select>
          </div>
          <div className="field">
            <label>Emotion</label>
            <select value={form.voice.emotion} onChange={(e) => setVoice({ emotion: e.target.value })}>
              <option value="warm">Warm</option>
              <option value="energetic">Energetic</option>
              <option value="calm">Calm</option>
              <option value="serious">Serious</option>
              <option value="playful">Playful</option>
            </select>
          </div>
        </div>
      </div>

      <div className="card" style={{ marginBottom: 20 }}>
        <label style={{ marginBottom: 12 }}>Format & quality</label>
        <div className="row">
          <div className="field">
            <label>Duration (seconds)</label>
            <input
              type="number"
              min={6}
              max={90}
              value={form.durationSeconds}
              onChange={(e) => set({ durationSeconds: Number(e.target.value) })}
            />
          </div>
          <div className="field">
            <label>Aspect ratio</label>
            <select value={form.aspectRatio} onChange={(e) => set({ aspectRatio: e.target.value })}>
              <option value="9:16">9:16 — Reels / Shorts / TikTok</option>
              <option value="16:9">16:9 — Widescreen / YouTube</option>
              <option value="1:1">1:1 — Square / Feed</option>
            </select>
          </div>
          <div className="field">
            <label>Resolution</label>
            <select value={form.resolution} onChange={(e) => set({ resolution: e.target.value })}>
              <option value="720p">720p</option>
              <option value="1080p">1080p</option>
              <option value="4k">4K</option>
            </select>
          </div>
        </div>
        <div className="row">
          <div className="field">
            <label>Frame rate</label>
            <select value={form.fps} onChange={(e) => set({ fps: Number(e.target.value) })}>
              <option value={24}>24 fps (cinematic)</option>
              <option value={30}>30 fps</option>
              <option value={60}>60 fps</option>
            </select>
          </div>
          <div className="field">
            <label>Render quality</label>
            <select value={form.quality} onChange={(e) => set({ quality: e.target.value })}>
              <option value="draft">Draft (fast/cheap)</option>
              <option value="standard">Standard</option>
              <option value="high">High</option>
            </select>
          </div>
          <div className="field">
            <label>Extras</label>
            <div style={{ display: "flex", gap: 16, paddingTop: 6 }}>
              <label style={{ display: "flex", alignItems: "center", gap: 6, textTransform: "none", fontSize: 14 }}>
                <input type="checkbox" checked={form.music} onChange={(e) => set({ music: e.target.checked })} style={{ width: "auto" }} />
                Music
              </label>
              <label style={{ display: "flex", alignItems: "center", gap: 6, textTransform: "none", fontSize: 14 }}>
                <input
                  type="checkbox"
                  checked={form.captions}
                  onChange={(e) => set({ captions: e.target.checked })}
                  style={{ width: "auto" }}
                />
                Captions
              </label>
            </div>
          </div>
        </div>
      </div>

      {error && <div style={{ color: "var(--danger)", marginBottom: 16 }}>{error}</div>}

      <button type="submit" className="primary" disabled={submitting}>
        {submitting ? "Creating…" : "Create project →"}
      </button>
    </form>
  );
}
