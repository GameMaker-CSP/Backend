# GameMaker deployment

Keep Backend and Frontend side by side. This stack owns only loopback ports
8540 (Flask) and 8431 (website); it does not change the old UESL stack.
The frontend proxies /api/ and /socket.io/ to its backend. Browser authentication
uses host-only cookies. The unsafe inherited /run/ endpoints are unavailable.
One Gunicorn worker keeps in-memory Socket.IO room state consistent.

## Local validation

From Backend, with Docker running:

```sh
python3 deploy/init_env.py local
docker compose -f deploy/compose.yml up -d --build
curl --fail http://localhost:8431/api/health
```

Open http://localhost:8431/play/. The demo username is `demo`; its generated
password is USER_PASSWORD in private deploy/.env. Existing local demo/password
and the original run scripts are unaffected. Never commit this file.
The setup script refuses to overwrite existing secrets. On networks where
Docker Hub fails TLS validation, the official mirror can be selected using
`IMAGE_REGISTRY=public.ecr.aws/docker/library` before the Compose command.
Never disable certificate verification.

## School server

Use `docker-compose` if the school server lacks the `docker compose` plugin.
Fetch and check out `main` in both repositories before building. Generate independent server secrets with:

```sh
cd ~/gamemaker-csp/Backend
python3 deploy/init_env.py production
COMPOSE_PARALLEL_LIMIT=1 docker-compose -f deploy/compose.yml build
docker-compose -f deploy/compose.yml up -d
curl --fail http://127.0.0.1:8431/api/health
```

Production cookies require HTTPS; configure DNS and TLS before browser login.
Do not switch production to insecure cookies just to test an HTTP public URL.

Teacher DNS routes: assigned frontend hostname -> this server; assigned backend
hostname -> this server. Configure the server's nginx virtual hosts using the
adjacent template, replacing FRONTEND_DOMAIN and BACKEND_DOMAIN. Only install
those two virtual hosts; test nginx before reloading it. Then run certbot for
only those two assigned names. No domain names are assumed or claimed here.

SQLite and uploads persist in the gamemaker-csp_backend-data Docker volume.
Do not use `down -v`. Stop with `docker-compose -f deploy/compose.yml stop`.
Back up the volume before upgrades; no existing classroom database is imported.
Keep previous image tags/commit IDs for rollback. This setup is deployment
preparation, not a comprehensive security audit of inherited APIs.
