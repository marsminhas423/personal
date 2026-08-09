# Video Studio

A browser-based AI video generation tool. Write a prompt, pick a model, and get a clip back.
No build step, no server, no account — it is static HTML/CSS/ES modules that you host on
GitHub Pages and drive with your own API key.

**Live at:** `https://marsminhas423.github.io/personal/video-studio/`
(after Pages finishes building the branch you deployed).

---

## Quick start

1. Get a Gemini API key from [aistudio.google.com/apikey](https://aistudio.google.com/apikey).
2. Open the app → **Settings** → paste it into **Gemini API key** → close.
3. Type a prompt, hit **Generate**.

That is the whole setup for Google Veo. Nothing else to deploy.

---

## Providers

| Provider | Works from the browser | Notes |
| --- | --- | --- |
| **Google Veo** (Gemini API) | ✅ directly | `generativelanguage.googleapis.com` returns CORS headers for any origin and accepts `x-goog-api-key`, so the page calls it with no server in between. Verified. |
| **Runway** | ⚠️ needs the proxy | Documented as server-side only; sends no CORS headers. |
| **Luma Dream Machine** | ⚠️ needs the proxy | Same. Also needs a public image URL for image→video — it will not take an upload inline. |
| **fal.ai** | ⚠️ needs the proxy | One key reaches Veo, Kling, Hailuo, Wan… useful for comparing models. fal explicitly says not to ship its key to a browser. |

Setting up the proxy takes about two minutes and is covered in
[`worker/README.md`](worker/README.md). It is a single Cloudflare Worker, deployed under your
own account, restricted to an allowlist of provider hosts.

**On adapter accuracy:** the Veo adapter was verified end to end against the live API
contract. The Runway, Luma and fal adapters are written from those providers' published REST
shapes but have not been exercised against the real services — the network they live on was
not reachable from where this was built. If one rejects a field, the provider's own error text
appears verbatim in the queue row, and the request body is a few lines to adjust in
`js/providers/<name>.js`.

---

## What it does

- **Prompt composer** with style presets that append real production language — lens, lighting,
  camera move — rather than vague adjectives. Video models respond to those far more reliably.
- **Capability-driven controls.** Aspect ratio, resolution, duration, sample count, audio and
  seed re-shape themselves around whatever the selected model actually supports, so you cannot
  submit a combination the API will reject.
- **Image → video.** Drop a frame (or paste a URL) to use as the first frame.
- **A queue that survives a reload.** Jobs are written to IndexedDB before each state change,
  so closing the tab mid-render does not lose the job or pay twice — on reload it re-attaches
  to the same provider operation. Transient failures back off and retry; a 400 fails fast.
- **Gallery.** Finished clips are stored as blobs in IndexedDB, so they persist across sessions.
  Hover to preview, click for the lightbox, reuse a clip's exact settings, or download.
- **Storyboard.** An ordered list of shots, generated in one batch, then played back to back
  as a rough cut.
- **Cost estimate** before you spend anything.

## What it does not do

- **No stitching or export.** The storyboard plays clips in sequence; it does not render them
  into a single file. That needs `ffmpeg.wasm` (~30 MB) and is the obvious next addition.
- **No prompt rewriting.** What you type is what gets sent, plus any style clauses you picked.
- **Prices are estimates.** The per-second rates in each adapter are approximate and providers
  change them. Treat the figure as a guide and set a spend limit on your key.

---

## About your API keys

Keys are kept in `localStorage` and sent from the page straight to the provider. This is a
static site with nowhere else to put them, and it is a genuine trade-off worth being clear
about:

- Anything that can run JavaScript on this page can read them, and so can anyone with access
  to the browser profile.
- Use a key created for this project, with a spend limit set on the provider's dashboard.
- Never commit a key. Nothing in this repo reads one from a file, and none should be added.
- **Settings → Erase all local data** clears every key, setting and stored clip.

If you deploy the proxy, deploy your own. A proxy URL someone else hands you is a machine that
sees every request you make, key included.

---

## Layout

```
video-studio/
  index.html            markup
  styles.css            tokens + layout, dark theme
  js/
    app.js              bootstrap
    composer.js         prompt panel, capability-driven controls, submit
    panels.js           queue list, gallery, storyboard, lightbox
    settings.js         key vault UI, proxy, queue tuning, hard reset
    queue.js            job engine: submit → poll → download → store, with resume
    db.js               IndexedDB (jobs + clip blobs)
    state.js            localStorage settings and key vault
    prompt.js           style presets
    util.js             DOM, fetch wrappers, error shaping
    providers/
      index.js          registry, request context, the direct-vs-proxy decision
      gemini.js         Google Veo   (direct)
      runway.js         Runway       (proxy)
      luma.js           Luma         (proxy)
      fal.js            fal.ai       (proxy)
  worker/
    cors-proxy.js       Cloudflare Worker for the providers that block browsers
    wrangler.toml
```

### Adding a provider

Export an object from `js/providers/<name>.js` with `submit`, `poll` and `download`, then add
it to the array in `js/providers/index.js`. The queue handles retries, persistence, resume and
storage; an adapter only translates between the app's spec and one API.

```js
export const example = {
  id: 'example',
  label: 'Example',
  short: 'Example',
  keyLabel: 'Example API key',
  keyHelp: 'https://example.com/keys',
  direct: false,                       // true only if the host sends CORS headers
  defaultModels: [{ id: 'v1', label: 'V1', caps: { /* … */ }, pricePerSecond: 0.1 }],

  async submit(spec, ctx) { /* → a JSON-serialisable handle */ },
  async poll(handle, ctx) { /* → { done } | { done: true, videos: [{ url }] } */ },
  async download(video, ctx) { /* → Blob */ },
};
```

`ctx.resolveUrl(url)` applies the proxy when the adapter is not `direct`, and throws a clear
error if one is needed but not configured. Throw `ApiError` for anything the user should see.

---

## Running locally

```bash
python3 -m http.server 8000
# then open http://localhost:8000/video-studio/
```

Any static file server works. It must be served over HTTP rather than opened as a `file://`
URL, because ES modules and IndexedDB both need an origin.
