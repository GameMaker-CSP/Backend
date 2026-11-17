# Restore login on the UESL custom domain

Run these in Cockpit Terminal on the Docker host. Keep the current TLS certificate and the existing UESL database. The frontend publishes separately through GitHub Pages.

1. Find the running web container and its Compose project directory.

   ```bash
   docker ps --filter publish=8540 --format 'table {{.Names}}\t{{.Ports}}'
   docker inspect --format 'directory={{ index .Config.Labels "com.docker.compose.project.working_dir" }} service={{ index .Config.Labels "com.docker.compose.service" }} config={{ index .Config.Labels "com.docker.compose.project.config_files" }}' NAME_FROM_ABOVE
   ```

2. Change into the printed directory. Verify the remote is GameMaker-CSP/Backend and the branch is main. For service `web`, use the root checkout with `docker-compose.yml`. For service `backend` using `deploy/compose.yml`, work in the checkout's `deploy` directory, which has its own `.env`. If the output describes a different stack, identify the active checkout before rebuilding.

   ```bash
   git remote get-url origin
   git status -sb
   git pull --ff-only origin main
   sudoedit .env
   ```

   Set `IS_PRODUCTION=true`. If `CORS_ORIGINS` already exists, append `https://ueslgame.opencodingsociety.com` to its comma-separated list. With no override, the new Flask default includes this origin. Accounts that use emailed codes require working `SMTP_USER` and `SMTP_PASSWORD` settings. Keep existing secrets and database settings.

3. Rebuild the running service. For service `web`, run the first command from the root checkout. For service `backend`, use `docker compose -f compose.yml up -d --build --no-deps backend` from the `deploy` directory instead. Use `docker-compose` in place of `docker compose` on older installations.

   ```bash
   docker compose up -d --build --no-deps web
   curl -fsS http://127.0.0.1:8540/api/health
   curl -si -X OPTIONS http://127.0.0.1:8540/api/otp/send \
     -H 'Origin: https://ueslgame.opencodingsociety.com' \
     -H 'Access-Control-Request-Method: POST' \
     -H 'Access-Control-Request-Headers: content-type,x-origin'
   ```

   Wait for health to report ok. Check for the exact frontend origin and `Access-Control-Allow-Credentials: true` on OPTIONS before proceeding.

4. Install the API location from the root of the checkout and identify the existing HTTPS site file.

   ```bash
   cd "$(git rev-parse --show-toplevel)"
   sudo install -m 0644 deploy/ueslgame-api-location.conf /etc/nginx/snippets/ueslgame-api-location.conf
   sudo grep -lF 'ueslgame-api.opencodingsociety.com' /etc/nginx/sites-enabled/*
   ```

   Use the matching filename below, first resolving its symlink. Save the backup outside sites-enabled so nginx won't load it as a second site.

   ```bash
   site=$(readlink -f /etc/nginx/sites-enabled/MATCHING_FILENAME)
   sudo cp -p "$site" "/var/backups/ueslgame-api-nginx-$(date +%Y%m%d-%H%M%S).conf"
   sudoedit "$site"
   ```

   Inside the `server { ... }` for `ueslgame-api.opencodingsociety.com` that listens on **443**, add the line below alongside the other locations. If an API location already exists, replace that location with the include. If a server-level OPTIONS rule intercepts every request, remove that interception for the API. Preserve the TLS directives, HTTP redirect, and other sites.

   ```nginx
   include /etc/nginx/snippets/ueslgame-api-location.conf;
   ```

5. Test before reloading. Run both public checks.

   ```bash
   sudo nginx -t && sudo systemctl reload nginx
   curl -si -X OPTIONS https://ueslgame-api.opencodingsociety.com/api/otp/send \
     -H 'Origin: https://ueslgame.opencodingsociety.com' \
     -H 'Access-Control-Request-Method: POST' \
     -H 'Access-Control-Request-Headers: content-type,x-origin'
   curl -si https://ueslgame-api.opencodingsociety.com/api/id \
     -H 'Origin: https://ueslgame.opencodingsociety.com'
   ```

   Expect OPTIONS 2xx with POST and requested headers allowed. Both responses should contain one exact frontend Allow-Origin and Allow-Credentials true. A signed-out identity check returns 401. Sign in with an existing production account at https://ueslgame.opencodingsociety.com/login, refresh the profile, then log out to verify the full session.
