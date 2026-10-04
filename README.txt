EmberForge Website
==================

A complete, working website for your Minecraft server software:
downloads, feature comparison against other servers, and file uploads.

WHAT IS IN HERE
---------------
  index.html    the whole site (one file, no build step)
  server.py     the backend: serves the site, the downloads, and the APIs
  downloads/    your builds live here (exe + README already included)
  uploads/      files uploaded through the site land here
  Dockerfile    for deploying to a container host
  render.yaml   one-click deploy config for Render (free tier)
  DEPLOY.md     full guide: hosting + pointing a devs.surf domain at it

RUN IT LOCALLY
--------------
  python3 server.py --port 8080
  open http://localhost:8080/

No dependencies. Python 3.9+ and nothing else.

ENDPOINTS
---------
  /                     the site
  /download/<file>      download a build
  /api/builds           JSON list of builds (name, size, sha256)
  /api/compare          JSON feature comparison
  /api/health           JSON status (uptime, build count, uploads)
  POST /api/upload      multipart file upload

PUTTING IT ON YOUR DOMAIN
-------------------------
devs.surf gives you DNS, not hosting. Two steps:

  1. Host this folder somewhere that runs Python:
       - Render: push to GitHub, then New > Blueprint (render.yaml is ready)
       - Fly.io: fly launch --dockerfile Dockerfile && fly deploy
       - Your own VPS: python3 server.py --bind 0.0.0.0 --port 8080
  2. In devs.surf, add a DNS record pointing at that host:
       A record     -> your VPS's IP
       CNAME record -> the hostname Render/Fly gives you

Full details in DEPLOY.md.

NOTE
----
On free hosting the uploads/ folder is usually wiped on redeploy.
Use a persistent volume if you want uploads to last.
