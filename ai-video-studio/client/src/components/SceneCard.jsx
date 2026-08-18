import { mediaUrl } from "../api/client.js";

export default function SceneCard({ scene, index, projectId, onRegenerate, busy }) {
  const clipUrl = mediaUrl(projectId, scene.clipPath);

  return (
    <div className="card" style={{ display: "flex", gap: 16 }}>
      <div style={{ width: 160, flexShrink: 0 }}>
        {clipUrl ? (
          <video src={clipUrl} controls style={{ width: "100%", borderRadius: 8, background: "#000" }} />
        ) : (
          <div
            style={{
              width: "100%",
              aspectRatio: "9/16",
              borderRadius: 8,
              background: "var(--bg-elev-2)",
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              color: "var(--text-dim)",
              fontSize: 12,
            }}
          >
            not rendered
          </div>
        )}
      </div>
      <div style={{ flex: 1 }}>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start" }}>
          <strong>Scene {index + 1}</strong>
          <span className={`pill dot ${scene.status}`}>{scene.status}</span>
        </div>
        <p style={{ fontSize: 14, color: "var(--text)", margin: "8px 0" }}>{scene.description}</p>
        {scene.dialogue && (
          <p style={{ fontSize: 13, color: "var(--accent-2)", fontStyle: "italic", margin: "0 0 8px" }}>
            {scene.speaker ? `${scene.speaker}: ` : ""}"{scene.dialogue}"
          </p>
        )}
        <div style={{ fontSize: 12, color: "var(--text-dim)", marginBottom: 10 }}>{scene.durationSeconds}s</div>
        <button onClick={onRegenerate} disabled={busy}>
          {busy ? "Generating…" : scene.status === "ready" ? "↻ Regenerate" : "▶ Generate"}
        </button>
      </div>
    </div>
  );
}
