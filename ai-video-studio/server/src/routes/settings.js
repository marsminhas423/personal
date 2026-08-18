import express from "express";
import { writeConfig, providerStatus } from "../storage/config.js";

const router = express.Router();

// Never echo back the actual key values — only whether each provider is
// configured. Keys stay in data/config.json on this machine.
router.get("/", (req, res) => {
  res.json(providerStatus());
});

router.post("/", (req, res) => {
  const { anthropicApiKey, replicateApiToken, elevenLabsApiKey } = req.body || {};
  const patch = {};
  if (typeof anthropicApiKey === "string") patch.anthropicApiKey = anthropicApiKey;
  if (typeof replicateApiToken === "string") patch.replicateApiToken = replicateApiToken;
  if (typeof elevenLabsApiKey === "string") patch.elevenLabsApiKey = elevenLabsApiKey;
  writeConfig(patch);
  res.json(providerStatus());
});

export default router;
