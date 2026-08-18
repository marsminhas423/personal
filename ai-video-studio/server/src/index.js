import express from "express";
import cors from "cors";
import path from "path";
import { fileURLToPath } from "url";

import projectsRouter from "./routes/projects.js";
import settingsRouter from "./routes/settings.js";
import { assetsDir } from "./storage/projects.js";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const app = express();
const PORT = process.env.PORT || 5174;

app.use(cors());
app.use(express.json({ limit: "5mb" }));

app.use("/api/projects", projectsRouter);
app.use("/api/settings", settingsRouter);

// Serve generated media (scene clips, voiceover, final export) so the
// frontend can play them directly in <video>/<audio> tags.
app.use("/media/:projectId", (req, res, next) => {
  express.static(assetsDir(req.params.projectId))(req, res, next);
});

app.get("/api/health", (req, res) => res.json({ ok: true }));

app.listen(PORT, () => {
  console.log(`AI Video Studio server running on http://localhost:${PORT}`);
});
