import { useEffect, useState } from "react";
import { api } from "../api/client.js";

const STATUS_LABEL = {
  draft: "Draft",
  scripted: "Scripted",
  rendering: "Rendering",
  ready: "Ready",
  failed: "Failed",
};

export default function Dashboard({ onOpen, onNew }) {
  const [projects, setProjects] = useState(null);
  const [error, setError] = useState(null);

  useEffect(() => {
    api.listProjects().then(setProjects).catch((e) => setError(e.message));
  }, []);

  async function handleDelete(e, id) {
    e.stopPropagation();
    if (!confirm("Delete this project and all its generated assets?")) return;
    await api.deleteProject(id);
    setProjects((prev) => prev.filter((p) => p.id !== id));
  }

  if (error) return <div className="card">Failed to load projects: {error}</div>;
  if (!projects) return <div style={{ color: "var(--text-dim)" }}>Loading…</div>;

  if (projects.length === 0) {
    return (
      <div className="card" style={{ textAlign: "center", padding: 60 }}>
        <h2 style={{ marginTop: 0 }}>No videos yet</h2>
        <p style={{ color: "var(--text-dim)" }}>
          Describe an ad, reel, or short story and let the pipeline storyboard, voice, and render it for you.
        </p>
        <button className="primary" onClick={onNew}>
          + Create your first video
        </button>
      </div>
    );
  }

  return (
    <div>
      <h2>Your projects</h2>
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(260px, 1fr))", gap: 16 }}>
        {projects.map((p) => (
          <div key={p.id} className="card" style={{ cursor: "pointer" }} onClick={() => onOpen(p.id)}>
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start" }}>
              <strong>{p.theme || p.script?.title || "Untitled video"}</strong>
              <span className={`pill dot ${p.status}`}>{STATUS_LABEL[p.status] || p.status}</span>
            </div>
            <p style={{ color: "var(--text-dim)", fontSize: 13, minHeight: 36 }}>
              {(p.idea || "").slice(0, 110) || "No description"}
            </p>
            <div style={{ display: "flex", gap: 8, fontSize: 12, color: "var(--text-dim)", marginBottom: 12 }}>
              <span>{p.videoType}</span>·<span>{p.aspectRatio}</span>·<span>{p.durationSeconds}s</span>·
              <span>{p.resolution}</span>
            </div>
            <button className="ghost" style={{ fontSize: 12 }} onClick={(e) => handleDelete(e, p.id)}>
              Delete
            </button>
          </div>
        ))}
      </div>
    </div>
  );
}
