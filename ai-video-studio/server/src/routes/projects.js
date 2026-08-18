import express from "express";
import fs from "fs";
import {
  listProjects,
  readProject,
  createProject,
  updateProject,
  deleteProject,
} from "../storage/projects.js";
import {
  generateScript,
  generateScene,
  generateAllScenes,
  generateVoiceover,
  assembleVideo,
  runFullPipeline,
} from "../pipeline/pipeline.js";

const router = express.Router();

router.get("/", (req, res) => {
  res.json(listProjects());
});

router.post("/", (req, res) => {
  const project = createProject(req.body || {});
  res.status(201).json(project);
});

router.get("/:id", (req, res) => {
  const project = readProject(req.params.id);
  if (!project) return res.status(404).json({ error: "Not found" });
  res.json(project);
});

router.patch("/:id", (req, res) => {
  try {
    const project = updateProject(req.params.id, req.body || {});
    res.json(project);
  } catch (e) {
    res.status(400).json({ error: e.message });
  }
});

router.delete("/:id", (req, res) => {
  deleteProject(req.params.id);
  res.status(204).end();
});

// --- pipeline actions ---
router.post("/:id/script", async (req, res) => {
  try {
    res.json(await generateScript(req.params.id));
  } catch (e) {
    res.status(500).json({ error: e.message });
  }
});

router.post("/:id/scenes/:sceneId/generate", async (req, res) => {
  try {
    res.json(await generateScene(req.params.id, req.params.sceneId));
  } catch (e) {
    res.status(500).json({ error: e.message });
  }
});

router.post("/:id/scenes/generate-all", async (req, res) => {
  try {
    res.json(await generateAllScenes(req.params.id));
  } catch (e) {
    res.status(500).json({ error: e.message });
  }
});

router.post("/:id/voiceover", async (req, res) => {
  try {
    res.json(await generateVoiceover(req.params.id));
  } catch (e) {
    res.status(500).json({ error: e.message });
  }
});

router.post("/:id/assemble", async (req, res) => {
  try {
    res.json(await assembleVideo(req.params.id));
  } catch (e) {
    res.status(500).json({ error: e.message });
  }
});

router.post("/:id/run-full-pipeline", async (req, res) => {
  try {
    res.json(await runFullPipeline(req.params.id));
  } catch (e) {
    res.status(500).json({ error: e.message });
  }
});

router.get("/:id/export", (req, res) => {
  const project = readProject(req.params.id);
  if (!project?.finalVideoPath || !fs.existsSync(project.finalVideoPath)) {
    return res.status(404).json({ error: "No export available yet" });
  }
  res.download(project.finalVideoPath, `${(project.theme || "video").replace(/\W+/g, "_")}.mp4`);
});

export default router;
