# AI Video Studio (personal use)

A local tool for turning a one-line idea into a storyboarded, voiced, and
assembled ad/reel/short-story video. Runs entirely on your machine. Works
with **zero API keys** in mock mode (placeholder clips + silent audio, so
you can try the whole pipeline for free), and upgrades to real AI-generated
script polish, images/video, and realistic voice the moment you add keys in
Settings.

Not intended for publishing/selling — personal/experimental use only.

## How it works

```
idea + options  →  Claude writes a shot-by-shot script/storyboard
                →  each scene becomes an image → animated video clip (Replicate)
                →  dialogue becomes a voiceover track (ElevenLabs or Replicate TTS)
                →  ffmpeg concatenates scenes + muxes audio → final .mp4
```

Every step is also runnable manually and re-runnable per scene from the UI,
so you can regenerate just the one shot you don't like instead of the whole
video.

## Run it

```bash
npm run install:all   # one-time
npm run dev            # starts backend on :5174 and UI on :5173
```

Open http://localhost:5173.

## Add real AI providers (optional, costs money per generation)

Open **Settings** in the UI and paste in whichever of these you have:

- **Anthropic API key** — Claude writes/polishes the script & storyboard.
  Without it, a simple local template generator is used instead.
- **Replicate API token** — the "cheapest realistic" option: one token, pay
  per generation (a few cents per image/clip). Powers scene images, video
  animation, and (if ElevenLabs isn't set) text-to-speech. Get one at
  replicate.com. Swap `REPLICATE_IMAGE_MODEL` / `REPLICATE_VIDEO_MODEL` /
  `REPLICATE_TTS_MODEL` env vars to change which model it calls.
- **ElevenLabs API key** — best-quality, most realistic voiceover. Optional;
  used instead of Replicate's TTS when set.

Keys are saved to `data/config.json` on this machine only and are never
committed to git (`data/` is gitignored) or sent anywhere except the
provider they belong to.

## Project layout

```
server/   Express API + generation pipeline (script, scenes, voice, ffmpeg assembly)
client/   React UI (Vite) — new-project form, storyboard workspace, settings
data/     Per-project generated assets (gitignored, created at runtime)
```

## Notes on realism

There is no single model that generates a finished realistic ad from one
prompt. This tool orchestrates specialized models per step instead. Keeping
the *same* character/face consistent across multiple shots is still the
hardest part of the pipeline — for a talking spokesperson, an avatar/lip-sync
service (e.g. HeyGen, D-ID) tends to look far more convincing than hoping a
general video model holds a face steady across scenes; that's a good next
provider to wire in if that's your main use case.
