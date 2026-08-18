const BASE = "/api";

async function req(path, opts = {}) {
  const res = await fetch(`${BASE}${path}`, {
    headers: { "content-type": "application/json" },
    ...opts,
  });
  if (!res.ok) {
    let message = `Request failed (${res.status})`;
    try {
      const body = await res.json();
      message = body.error || message;
    } catch {
      // ignore
    }
    throw new Error(message);
  }
  if (res.status === 204) return null;
  return res.json();
}

export const api = {
  listProjects: () => req("/projects"),
  getProject: (id) => req(`/projects/${id}`),
  createProject: (data) => req("/projects", { method: "POST", body: JSON.stringify(data) }),
  updateProject: (id, patch) => req(`/projects/${id}`, { method: "PATCH", body: JSON.stringify(patch) }),
  deleteProject: (id) => req(`/projects/${id}`, { method: "DELETE" }),

  generateScript: (id) => req(`/projects/${id}/script`, { method: "POST" }),
  generateScene: (id, sceneId) => req(`/projects/${id}/scenes/${sceneId}/generate`, { method: "POST" }),
  generateAllScenes: (id) => req(`/projects/${id}/scenes/generate-all`, { method: "POST" }),
  generateVoiceover: (id) => req(`/projects/${id}/voiceover`, { method: "POST" }),
  assemble: (id) => req(`/projects/${id}/assemble`, { method: "POST" }),
  runFullPipeline: (id) => req(`/projects/${id}/run-full-pipeline`, { method: "POST" }),

  getSettings: () => req("/settings"),
  saveSettings: (data) => req("/settings", { method: "POST", body: JSON.stringify(data) }),
};

export function mediaUrl(projectId, filePath) {
  if (!filePath) return null;
  const filename = filePath.split("/").pop();
  return `/media/${projectId}/${filename}`;
}

export function exportUrl(projectId) {
  return `/api/projects/${projectId}/export`;
}
