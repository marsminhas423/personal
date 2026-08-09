# CORS proxy

Google Veo works straight from the browser — `generativelanguage.googleapis.com` returns
`access-control-allow-origin` for any origin and accepts the `x-goog-api-key` header, so
the page can call it with no server at all.

Runway, Luma and fal do not. Their APIs are documented as server-side only and send no CORS
headers, so a browser request to them fails before it ever reaches the provider. This Worker
is the smallest thing that fixes that.

## Deploy

```bash
npm install -g wrangler
wrangler login

cd video-studio/worker
wrangler deploy
```

`wrangler.toml` already points at `cors-proxy.js`. Deploy prints a URL like
`https://video-studio-proxy.<your-subdomain>.workers.dev` — paste it into **Settings → CORS
proxy URL** in the app.

## Lock it to your own page

By default the Worker answers any origin, which means anyone who finds the URL can use your
bandwidth. Restrict it:

```bash
wrangler secret put ALLOWED_ORIGIN
# enter: https://marsminhas423.github.io
```

Requests from any other origin then get a 403.

## What it does and does not do

- Forwards only to the hosts in `ALLOWED_HOSTS`, so it cannot be pointed at arbitrary URLs.
- Passes your `Authorization` header through untouched. It never stores or logs a key.
- Still sits in the path of your API traffic, so run it under **your own** Cloudflare
  account. Do not use a proxy URL someone else gives you — that is handing them your keys.

Cloudflare's free tier covers 100k requests/day, far more than this app generates. Video
downloads flow through the Worker only when a provider's CDN itself refuses cross-origin
reads; the app tries a direct download first.

## Other runtimes

The handler is a standard `fetch(request, env)` export, so it also runs on Deno Deploy or
Vercel Edge Functions with minor changes to how the env variable is read.
