import { useEffect, useState, useCallback } from "react";
import { api, mediaUrl, exportUrl } from "../api/client.js";
import SceneCard from "./SceneCard.jsx";

export default function ProjectWorkspace({ projectId, onBack }) {
  const [project, setProject] = useState(null);
  const [busy, setBusy] = useState(null); // string describing what's in flight
  const [sceneBusy, setSceneBusy] = useState(null); // sceneId
  const [error, setError] = useState(null);

  const refresh = useCallback(() => {
    return api.getProject(projectId).then(setProject);
  }, [projectId]);

  useEffect(() => {
    refresh();
  }, [refresh]);

  async function run(label, fn) {
    setBusy(label);
    setError(null);
    try {
      await fn();
      await refresh();
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(null);
    }
  }

  async function regenerateScene(sceneId) {
    setSceneBusy(sceneId);
    setError(null);
    try {
      await api.generateScene(projectId, sceneId);
      await refresh();
    } catch (e) {
      setError(e.message);
    } finally {
      setSceneBusy(null);
    }
  }

  if (!project) return <div style={{ color: "var(--text-dim)" }}>Loading…</div>;

  const hasScript = !!project.script;
  const allScenesReady = project.scenes.length > 0 && project.scenes.every((s) => s.status === "ready");
  const finalUrl = mediaUrl(projectId, project.finalVideoPath);

  return (
    <div>
      <button className="ghost" onClick={onBack} style={{ marginBottom: 12 }}>
        ← Back to projects
      </button>

      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", marginBottom: 20 }}>
        <div>
          <h2 style={{ margin: "0 0 4px" }}>{project.theme || project.script?.title || "Untitled video"}</h2>
          <p style={{ color: "var(--text-dim)", margin: 0, maxWidth: 600 }}>{project.idea}</p>
        </div>
        <span className={`pill dot ${project.status}`}>{project.status}</span>
      </div>

      {error && (
        <div className="card" style={{ borderColor: "var(--danger)", color: "var(--danger)", marginBottom: 16 }}>
          {error}
        </div>
      )}

      <div className="card" style={{ marginBottom: 20, display: "flex", gap: 12, flexWrap: "wrap", alignItems: "center" }}>
        <button className="primary" disabled={!!busy} onClick={() => run("pipeline", () => api.runFullPipeline(projectId))}>
          {busy === "pipeline" ? "Running full pipeline…" : "▶ Run full pipeline"}
        </button>
        <span style={{ color: "var(--text-dim)", fontSize: 13 }}>or run each step manually below ↓</span>
      </div>

      {/* Step 1: Script */}
      <Section title="1. Script & storyboard" done={hasScript}>
        {!hasScript ? (
          <button disabled={!!busy} onClick={() => run("script", () => api.generateScript(projectId))}>
            {busy === "script" ? "Writing script…" : "Generate script"}
          </button>
        ) : (
          <div>
            <p style={{ color: "var(--text-dim)", fontStyle: "italic" }}>{project.script.logline}</p>
            <button disabled={!!busy} onClick={() => run("script", () => api.generateScript(projectId))}>
              ↻ Regenerate script
            </button>
          </div>
        )}
      </Section>

      {/* Step 2: Scenes */}
      {hasScript && (
        <Section title={`2. Scenes (${project.scenes.filter((s) => s.status === "ready").length}/${project.scenes.length} rendered)`}>
          <button disabled={!!busy} onClick={() => run("scenes", () => api.generateAllScenes(projectId))} style={{ marginBottom: 16 }}>
            {busy === "scenes" ? "Generating all scenes…" : "▶ Generate all scenes"}
          </button>
          <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
            {project.scenes.map((scene, i) => (
              <SceneCard
                key={scene.id}
                scene={scene}
                index={i}
                projectId={projectId}
                busy={sceneBusy === scene.id}
                onRegenerate={() => regenerateScene(scene.id)}
              />
            ))}
          </div>
        </Section>
      )}

      {/* Step 3: Voiceover */}
      {hasScript && (
        <Section title="3. Voiceover" done={!!project.voiceoverPath}>
          <button disabled={!!busy} onClick={() => run("voice", () => api.generateVoiceover(projectId))}>
            {busy === "voice" ? "Generating voiceover…" : project.voiceoverPath ? "↻ Regenerate voiceover" : "Generate voiceover"}
          </button>
          {project.voiceoverPath && (
            <audio controls src={mediaUrl(projectId, project.voiceoverPath)} style={{ display: "block", marginTop: 12, width: "100%" }} />
          )}
        </Section>
      )}

      {/* Step 4: Assemble & export */}
      {hasScript && (
        <Section title="4. Assemble & export">
          <button
            className="primary"
            disabled={!!busy || !allScenesReady}
            onClick={() => run("assemble", () => api.assemble(projectId))}
            title={!allScenesReady ? "All scenes must be rendered first" : ""}
          >
            {busy === "assemble" ? "Rendering final video…" : "Assemble final video"}
          </button>
          {!allScenesReady && (
            <p style={{ color: "var(--text-dim)", fontSize: 13, marginTop: 8 }}>
              Render every scene above before assembling the final export.
            </p>
          )}

          {finalUrl && project.status === "ready" && (
            <div style={{ marginTop: 20 }}>
              <video
                src={finalUrl}
                controls
                style={{
                  maxWidth: 340,
                  width: "100%",
                  borderRadius: 12,
                  background: "#000",
                  display: "block",
                }}
              />
              <a href={exportUrl(projectId)} style={{ display: "inline-block", marginTop: 12 }}>
                <button className="primary">⬇ Download final video</button>
              </a>
            </div>
          )}
        </Section>
      )}

      {project.log?.length > 0 && (
        <details style={{ marginTop: 24 }}>
          <summary style={{ cursor: "pointer", color: "var(--text-dim)" }}>Activity log</summary>
          <div className="card" style={{ marginTop: 10, fontFamily: "monospace", fontSize: 12, maxHeight: 240, overflow: "auto" }}>
            {project.log.map((l, i) => (
              <div key={i} style={{ marginBottom: 4, color: "var(--text-dim)" }}>
                <span style={{ color: "var(--accent)" }}>{new Date(l.at).toLocaleTimeString()}</span> {l.message.split("\n")[0]}
              </div>
            ))}
          </div>
        </details>
      )}
    </div>
  );
}

function Section({ title, done, children }) {
  return (
    <div className="card" style={{ marginBottom: 20 }}>
      <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 14 }}>
        <h3 style={{ margin: 0 }}>{title}</h3>
        {done && <span className="pill ready dot">done</span>}
      </div>
      {children}
    </div>
  );
}
