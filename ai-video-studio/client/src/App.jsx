import { useState } from "react";
import Dashboard from "./components/Dashboard.jsx";
import NewProjectForm from "./components/NewProjectForm.jsx";
import ProjectWorkspace from "./components/ProjectWorkspace.jsx";
import SettingsPanel from "./components/SettingsPanel.jsx";

export default function App() {
  const [view, setView] = useState({ name: "dashboard" });

  return (
    <div style={{ minHeight: "100%", display: "flex", flexDirection: "column" }}>
      <header
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          padding: "16px 28px",
          borderBottom: "1px solid var(--border)",
        }}
      >
        <div
          style={{ fontWeight: 800, fontSize: 18, cursor: "pointer", display: "flex", alignItems: "center", gap: 8 }}
          onClick={() => setView({ name: "dashboard" })}
        >
          <span
            style={{
              display: "inline-block",
              width: 10,
              height: 10,
              borderRadius: 3,
              background: "linear-gradient(135deg, var(--accent), var(--accent-2))",
            }}
          />
          AI Video Studio
          <span style={{ fontWeight: 400, fontSize: 12, color: "var(--text-dim)" }}>· personal use only</span>
        </div>
        <nav style={{ display: "flex", gap: 10 }}>
          <button className="ghost" onClick={() => setView({ name: "dashboard" })}>
            Projects
          </button>
          <button className="primary" onClick={() => setView({ name: "new" })}>
            + New Video
          </button>
          <button className="ghost" onClick={() => setView({ name: "settings" })}>
            ⚙ Settings
          </button>
        </nav>
      </header>

      <main style={{ flex: 1, padding: "28px", maxWidth: 1100, width: "100%", margin: "0 auto" }}>
        {view.name === "dashboard" && (
          <Dashboard
            onOpen={(id) => setView({ name: "workspace", id })}
            onNew={() => setView({ name: "new" })}
          />
        )}
        {view.name === "new" && (
          <NewProjectForm onCreated={(id) => setView({ name: "workspace", id })} onCancel={() => setView({ name: "dashboard" })} />
        )}
        {view.name === "workspace" && (
          <ProjectWorkspace projectId={view.id} onBack={() => setView({ name: "dashboard" })} />
        )}
        {view.name === "settings" && <SettingsPanel onBack={() => setView({ name: "dashboard" })} />}
      </main>
    </div>
  );
}
