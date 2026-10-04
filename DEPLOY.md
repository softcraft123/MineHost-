# Putting this site online, and pointing devs.surf at it

## First: what devs.surf actually is

`devs.surf` gives you a **subdomain and DNS records**. It is *not* hosting.
Claiming `yourname.devs.surf` gives you a name that you then point at a server
you run somewhere else. DNS answers "which IP is this name?" — it does not run
your Python program.

So there are two jobs:

1. **Host** the site somewhere that runs Python.
2. **Point** a `devs.surf` DNS record at that host.

---

## Job 1 — host the site

This folder is a complete, dependency-free Python app. Three easy routes:

### Option A — Render (free, easiest)
1. Push this folder to a GitHub or GitLab repository.
2. Go to render.com → **New → Blueprint** → pick the repo.
   `render.yaml` is already here, so Render configures itself.
3. You get a URL like `https://emberforge-site.onrender.com`.
   Test `/api/health` — it should return JSON.

*Free tier sleeps after inactivity; the first request wakes it in ~30 s.*

### Option B — Fly.io
```sh
fly launch --dockerfile Dockerfile
fly deploy
```
You get `https://<app>.fly.dev`.

### Option C — any VPS you already have
```sh
python3 server.py --bind 0.0.0.0 --port 8080
```
Put nginx/Caddy in front for TLS. On a VPS you also get a **static IP**, which
makes the DNS step below trivial.

---

## Job 2 — point devs.surf at it

Log in at devs.surf → your subdomain → **DNS records** → add:

| Type | Name | Value | TTL |
|---|---|---|---|
| `A` | `@` (or your subdomain) | *your host's IPv4* | 300 |
| `CNAME` | `www` | `yourname.devs.surf` | 300 |

Notes:

- **A record** needs an IP address. Render/Fly give you a *hostname*, not a
  fixed IP — so for those, use a **CNAME** instead of an A record:
  | Type | Name | Value |
  |---|---|---|
  | `CNAME` | `@` | `emberforge-site.onrender.com` |
  | `CNAME` | `www` | `emberforge-site.onrender.com` |
  (Some DNS providers refuse a CNAME at the root. If devs.surf refuses, use
  their redirect/flattening option, or put `www` on the CNAME and redirect the
  bare name to `www`.)
- **On a VPS with a real IP**, the A record is the clean answer.
- Propagation is usually seconds to a few minutes.

Verify once it propagates:
```sh
dig +short yourname.devs.surf
curl -s https://yourname.devs.surf/api/health
```

---

## TLS

Most hosts (Render, Fly) terminate HTTPS for you automatically, so
`https://yourname.devs.surf` just works after DNS resolves. On a bare VPS, run
Caddy and it will fetch a certificate automatically:

```
yourname.devs.surf {
    reverse_proxy 127.0.0.1:8080
}
```

---

## What to upload as your "build"

The **downloads/** folder already holds your server:

- `emberforge-server.exe` — the Windows build (3.6 MB)
- `README.txt` — instructions

Anything you drop in `uploads/` (or POST to `/api/upload`) is listed on the page
automatically, with its size and SHA-256, and gets a working download link.

---

## Honest limits

- The **upload** feature stores files on the host's disk. On Render's free tier
  that disk is **ephemeral** — uploads vanish on redeploy/restart. For a
  permanent archive, use a host with a persistent volume, or store the files in
  object storage.
- The live preview URL in this sandbox (`work-1-*.prod-runtime.all-hands.dev`)
  is **temporary**. It dies with the sandbox. Deploy properly using the steps
  above before sharing it.
