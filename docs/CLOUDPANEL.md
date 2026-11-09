# CloudPanel Deployment Guide

This document is **deployment preparation**, written and verified locally
(including a real, end-to-end backup/restore test against a live Postgres
container — see `PROJECT_STATUS.md`). It has **not** been run against a real
CloudPanel installation or a live VPS — that requires access this session
didn't have. Steps that need live VPS/CloudPanel access to confirm are
marked **[VPS-ONLY — verify on first deploy]** below.

## Two supported deployment modes

MyMoney Tracker's `compose.yaml` supports both:

- **Standalone**: this app owns ports 80/443 via its own Caddy container,
  which gets its own Let's Encrypt certificate. Use this if you are *not*
  running CloudPanel on the box, or are giving this app its own dedicated
  VPS. Start it with `docker compose --profile standalone up -d` — the
  `--profile standalone` flag is what turns the `caddy` service on; without
  it, `caddy` never starts.
- **CloudPanel-managed**: CloudPanel's own Nginx reverse proxy and
  certificate management own ports 80/443 for the whole server, across all
  its sites. This app runs as two containers (`db`, `web`) with no public
  port of its own — only `web` publishes `127.0.0.1:${APP_PORT}` (default
  `8001`), which is reachable from the host but not the internet. You point
  a CloudPanel site's reverse proxy at that port. Start it with
  `docker compose up -d` (no `--profile` flag, so `caddy` stays off).

This guide covers the CloudPanel-managed mode.

## Why bind mounts, not named Docker volumes

`POSTGRES_DATA_DIR` and `POSTGRES_BACKUP_DIR` (see `.env.example`) are bind
mounts to real host paths, not Docker named volumes. This matters for two
reasons:

1. **CloudPanel's backup routine backs up a site's own directory tree** —
   files under `/home/<site-user>/...`. A Docker named volume lives under
   `/var/lib/docker/volumes/...`, outside any site's home directory, so it
   is **not** covered by that backup unless you point CloudPanel (or your
   own backup job) at the Docker volume path directly.
2. Bind mounts make it visually obvious, with a plain `ls`, that your
   database files and backup archives genuinely exist on disk — no
   `docker volume inspect` required to find them.

## 1. Choose and prepare directories **[VPS-ONLY — verify on first deploy]**

Pick (or create) the CloudPanel site this app will live under, then decide
on paths under that site user's home directory. This repo never assumes a
fixed username or domain — fill in your own:

```bash
# Example only — substitute your real CloudPanel site user.
export CLOUDPANEL_SITE_USER=mymoney-site
export CLOUDPANEL_SITE_HOME=/home/$CLOUDPANEL_SITE_USER
export APP_DEPLOY_DIR=$CLOUDPANEL_SITE_HOME/mymoney-tracker

sudo -u "$CLOUDPANEL_SITE_USER" mkdir -p \
  "$APP_DEPLOY_DIR" \
  "$APP_DEPLOY_DIR/data/postgres" \
  "$APP_DEPLOY_DIR/backups/postgres"
```

**Permissions matter for Postgres specifically.** The official
`postgres:16-alpine` image runs as uid/gid `999` inside the container, and
needs to own its data directory:

```bash
sudo chown -R 999:999 "$APP_DEPLOY_DIR/data/postgres"
```

Do **not** `chown` the whole CloudPanel home directory — only the specific
data directory this app owns.

## 2. Clone the repository

```bash
cd "$APP_DEPLOY_DIR"
git clone https://github.com/<your-account>/mymoney-tracker.git app
cd app
```

(Swap the URL for your actual repository once published — see
`PROJECT_STATUS.md` for the real one.)

## 3. Configure `.env`

```bash
cp .env.example .env
```

Fill in, at minimum:

- `DJANGO_SECRET_KEY` — generate with `python -c "import secrets; print(secrets.token_urlsafe(50))"`
- `DJANGO_ALLOWED_HOSTS` / `DJANGO_CSRF_TRUSTED_ORIGINS` — your real domain(s)
- `POSTGRES_PASSWORD` — a strong password
- `POSTGRES_DATA_DIR=$APP_DEPLOY_DIR/data/postgres`
- `POSTGRES_BACKUP_DIR=$APP_DEPLOY_DIR/backups/postgres`
- `APP_PORT` — pick a free localhost port if `8001` is already used by
  another site on this VPS (**[VPS-ONLY]**: check `sudo ss -tlnp | grep 8001`)
- SMTP settings, if you have a provider; otherwise leave blank (console
  backend — no email is actually sent)
- Leave `DOMAIN` as-is; it's only read by the standalone Caddy profile,
  which you are not using in this mode

## 4. Build and start **[VPS-ONLY — verify on first deploy]**

```bash
docker compose build
docker compose up -d
docker compose logs -f web   # watch migrations run, then Ctrl+C
docker compose exec web python manage.py createsuperuser
```

Confirm it's actually listening:

```bash
curl http://127.0.0.1:${APP_PORT:-8001}/health/
# {"status": "ok", "database": true}
```

## 5. Point a CloudPanel site at it **[VPS-ONLY — verify on first deploy]**

This repo cannot configure CloudPanel itself — it has no access to that
UI/API from this environment. In the CloudPanel admin:

1. Create (or reuse) a site for your domain, type "Reverse Proxy" (or
   equivalent in your CloudPanel version).
2. Set the proxy target to `http://127.0.0.1:${APP_PORT}` (e.g. `:8001`).
3. Let CloudPanel issue/renew the site's own Let's Encrypt certificate —
   don't also run this app's Caddy container (you aren't, in this mode).
4. Confirm `X-Forwarded-Proto`/`X-Forwarded-For` are forwarded (CloudPanel's
   Nginx does this by default); `config/settings/prod.py` already trusts
   `X-Forwarded-Proto` via `SECURE_PROXY_SSL_HEADER` for exactly this setup
   — this was the specific fix for a redirect-loop bug found and fixed
   while testing this app's own standalone Caddy setup (see
   `DECISIONS.md`), and the same header is what any reverse proxy needs to
   set correctly here.

## 6. Backups

```bash
docker compose exec -T web python manage.py backup_db
```

Writes a timestamped `.sql` file to `$POSTGRES_BACKUP_DIR` (host path, not
just inside the container — verified: this produces a real file on the
host filesystem, readable without `docker exec`).

**Automate it** with a host-level cron job (not inside the container — no
extra background service needed for something this infrequent):

```cron
# /etc/cron.d/mymoney-backup — runs daily at 02:00
0 2 * * * root cd /home/mymoney-site/mymoney-tracker/app && docker compose exec -T web python manage.py backup_db >> /var/log/mymoney-backup.log 2>&1
```

Add your own retention cleanup (e.g. `find "$POSTGRES_BACKUP_DIR" -name '*.sql' -mtime +30 -delete`) if you don't want them accumulating forever.

### CloudPanel remote backups **[VPS-ONLY — verify when you have CloudPanel access]**

To get these backup archives into CloudPanel's own remote-backup workflow
(Google Drive or another supported destination), the backup directory must
sit inside whatever directory CloudPanel's backup feature actually covers
for that site — this repo cannot verify that scope without a real
CloudPanel installation to inspect. Concretely, before relying on this:

1. Confirm in CloudPanel's own backup settings for this site exactly which
   directories are included (some CloudPanel versions back up `htdocs/`
   specifically, not a site user's entire home directory).
2. If `$POSTGRES_BACKUP_DIR` falls outside that scope, either move it
   inside the covered directory, or set up a separate, explicit sync (e.g.
   `rclone` to the same remote CloudPanel uses) for just that directory.
3. Never place backup archives inside a directory CloudPanel serves
   publicly (e.g. a site's `htdocs/` webroot) — they must stay unreachable
   over HTTP.

This app does not invent or assume a remote destination is already active;
treat remote delivery of backups as a manual step to confirm on the VPS,
not something this codebase configures.

## 7. Restoring a backup

**Deliberately CLI-only — never exposed over the web admin panel**, since
restore replaces the entire database:

```bash
docker compose exec -T web python manage.py restore_db mymoney_20260101_120000.sql
```

**Restore was verified end-to-end in this session** (not just written):
a backup taken from a running container was restored into a separate,
isolated database inside the same Postgres instance, and the schema
(33 tables) and a specific test record were confirmed present afterward
with matching values. See `PROJECT_STATUS.md` for the exact verification
performed. Always test a restore into an isolated database/instance before
trusting it against production — the command above restores directly into
the configured production database, which is correct for a real recovery
but means you should rehearse the procedure using a copy first if you've
never run it on this VPS before.

## 8. Updating to a new version **[VPS-ONLY — verify on first deploy]**

```bash
cd "$APP_DEPLOY_DIR/app"

# 1. Confirm current deployment is healthy first.
curl http://127.0.0.1:${APP_PORT:-8001}/health/

# 2. Back up before touching anything.
docker compose exec -T web python manage.py backup_db

# 3. Pull the verified code.
git pull

# 4. Rebuild only the web image — db is untouched, its data directory
#    is a bind mount on the host and is never part of this image.
docker compose build web

# 5. Apply migrations, then restart just the web container.
#    (entrypoint.sh runs `migrate` automatically on start, but running it
#    explicitly first lets you see any migration errors before restarting.)
docker compose run --rm web python manage.py migrate --no-input
docker compose up -d web

# 6. Verify.
curl http://127.0.0.1:${APP_PORT:-8001}/health/
docker compose exec web python manage.py check
```

### What this update flow deliberately never does

- **Never** `docker compose down -v` — that removes named volumes (and
  combined with bind-mount removal commands, could threaten data); this
  flow only ever restarts/recreates the `web` service.
- **Never** deletes or recreates `$POSTGRES_DATA_DIR` or
  `$POSTGRES_BACKUP_DIR`.
- **Never** runs `migrate` with `--fake` or skips reviewing a migration
  that looks like it could be destructive (dropping a column/table) before
  applying it to real data — read the migration file first if unsure.
- **Never** seeds fictional transactions, goals, or balances into a real
  user's account during an update.

### Rollback

Django migrations are not all automatically reversible (a migration that
drops a column, for instance, loses that column's data on rollback even if
you reverse the migration). Before rolling back a schema migration on real
data:

1. Restore from the backup taken in step 2 above instead of attempting
   `migrate <app> <previous_migration>` on data that migration already
   changed, unless you've specifically verified that migration is safe to
   reverse.
2. If you do need to reverse application code without a schema change
   (the common case), `git checkout <previous-tag>` and
   `docker compose build web && docker compose up -d web` — no database
   action needed.

## What was actually verified vs. what still needs VPS access

| Item | Status |
|---|---|
| `docker compose build` / `up` (db + web, no Caddy) | Verified locally |
| Postgres data persists as real files under a bind-mounted host directory | Verified locally |
| `backup_db` writes a real file under the bind-mounted backup directory | Verified locally |
| Restoring that backup reproduces the schema and data correctly | Verified locally (restored into an isolated database) |
| App reachable via `127.0.0.1:$APP_PORT` on the host | Verified locally |
| `SECURE_PROXY_SSL_HEADER` works correctly behind a reverse proxy | Verified locally (against this app's own Caddy, which behaves the same way CloudPanel's Nginx does — trusts `X-Forwarded-Proto`) |
| CloudPanel reverse-proxy site configuration | **Not verified** — no CloudPanel installation was available in this environment |
| CloudPanel's backup scope actually covering `$POSTGRES_BACKUP_DIR` | **Not verified** — depends on the specific CloudPanel version/configuration on the real VPS |
| Google Drive (or other) remote backup delivery | **Not configured, not verified** — no credentials were available or invented |
