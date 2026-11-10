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

`POSTGRES_DATA_DIR`, `POSTGRES_BACKUP_DIR`, and `PRIVATE_MEDIA_DIR` (see
`.env.example`) are bind mounts to real host paths, not Docker named
volumes. This matters for two reasons:

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

**Permissions matter for Postgres specifically — verify the UID, don't
assume it.** Don't trust this number blindly; different base images use
different UIDs (confirmed by checking directly — `docker run --rm
postgres:16-alpine id postgres` — in an earlier version of this guide
before an actual deployment, this was assumed to be `999`, which is wrong
for this Alpine-based image):

```bash
docker run --rm postgres:16-alpine id postgres
# uid=70(postgres) gid=70(postgres) ...

sudo chown -R 70:70 "$APP_DEPLOY_DIR/data/postgres"
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
- `PRIVATE_MEDIA_DIR=$APP_DEPLOY_DIR/private_media` (Phase 11+: Help &
  Feedback screenshot attachments — see `apps/feedback/models.py` for why
  these intentionally don't live under the regular media/static paths)
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

## 5. Point a CloudPanel site at it

**Verified working** (deployed for real — `money.therealworldboosts.com`,
CloudPanel CLI 6.0.8). CloudPanel's `clpctl` exposes this entirely from the
command line, no web UI needed — useful on a server you're managing over
SSH:

```bash
# 1. Create the site itself as a reverse-proxy type, pointed at the app port.
#    This also creates the site user and its home directory.
clpctl site:add:reverse-proxy \
  --domainName=money.yourdomain.com \
  --reverseProxyUrl=http://127.0.0.1:${APP_PORT:-8001} \
  --siteUser=<chosen-site-username> \
  --siteUserPassword='<a-strong-password>'

# 2. DNS must already resolve to this VPS before the next step — Let's
#    Encrypt's HTTP-01 challenge needs it. Verify first:
nslookup money.yourdomain.com   # must return this VPS's IP

# 3. Issue the certificate.
clpctl lets-encrypt:install:certificate --domainName=money.yourdomain.com
```

`X-Forwarded-Proto`/`X-Forwarded-For` are forwarded by CloudPanel's Nginx
by default; `config/settings/prod.py` already trusts `X-Forwarded-Proto`
via `SECURE_PROXY_SSL_HEADER` for exactly this setup — this is the same
fix that was needed for a redirect-loop bug found while testing this app's
own standalone Caddy setup in an earlier phase (see `DECISIONS.md`), and
it was verified working again against CloudPanel's Nginx on this actual
deployment (no redirect loop, HSTS and `Secure` cookies present, valid
cert, `curl https://money.therealworldboosts.com/health/` returns 200).

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

### CloudPanel remote backups — checked directly on the actual VPS

**Finding: this CloudPanel installation (CLI 6.0.8) has no built-in
remote-backup command at all.** The full `clpctl` command list was
dumped directly (`clpctl` with no arguments) and inspected — its
categories are `cloudflare`, `cloudpanel`, `db`, `lets-encrypt`, `site`,
`system`, `user`, `vhost-template(s)`; there is no `backup` category, no
Google Drive integration, nothing that backs up a site's files or
database automatically. (Google Drive/remote-backup support is a
CloudPanel **Pro** feature per their own docs; this appears to be the
free Community edition.)

This isn't a gap specific to this app — it's the existing convention on
this VPS. Another application already running on this server
(`daily-figures`) handles its own backups the same way: via a dedicated
`systemd` timer/service (`/etc/systemd/system/daily-figures-backup.timer`),
not CloudPanel. MyMoney Tracker's cron-based `backup_db` schedule (above)
follows that same established pattern rather than inventing a new one.

**No remote (Google Drive or otherwise) delivery is configured.** No
credentials were available, and none were invented. `rclone` is already
installed on this server (`/usr/bin/rclone`, verified directly) but has
no remotes configured (`rclone listremotes` returns nothing) — it isn't
being used for anything yet. It's a reasonable option to set up yourself
(`rclone config` to add a Google Drive or other remote, then a cron line
like the one below) if you want backups leaving this VPS:

```bash
# Example only — requires your own `rclone config` remote set up first.
45 2 * * * root rclone copy /home/<site-user>/mymoney-tracker/backups/postgres remote:mymoney-backups --min-age 1h
```

Never place backup archives inside a directory CloudPanel serves
publicly (e.g. a site's `htdocs/` webroot) — `$POSTGRES_BACKUP_DIR` here
is outside any site's webroot, so this is already satisfied.

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

# 3. Pull the verified code — as the site user, not root (the app
#    directory is owned by that user; a plain `git pull` run as root hits
#    git's "dubious ownership" protection).
sudo -u $CLOUDPANEL_SITE_USER git pull

# 4. Rebuild only the web image — db is untouched, its data directory
#    is a bind mount on the host and is never part of this image.
docker compose build web

# 5. Restart just the web container — docker/entrypoint.sh runs `migrate`
#    automatically every time it starts, so this is also when migrations
#    for the new code actually get applied.
#    (`docker compose run --rm web <cmd>` does NOT work for previewing
#    this step in isolation: the image's ENTRYPOINT ignores the command
#    you pass and always runs its own full startup sequence regardless —
#    found and fixed in this exact deployment. To preview a migration
#    without applying it first, use `docker compose exec web python
#    manage.py sqlmigrate <app> <migration_name>` instead.)
docker compose up -d web

# 6. Verify.
curl http://127.0.0.1:${APP_PORT:-8001}/health/
docker compose exec web python manage.py check
```

### What this update flow deliberately never does

- **Never** `docker compose down -v` — that removes named volumes (and
  combined with bind-mount removal commands, could threaten data); this
  flow only ever restarts/recreates the `web` service.
- **Never** deletes or recreates `$POSTGRES_DATA_DIR`,
  `$POSTGRES_BACKUP_DIR`, or `$PRIVATE_MEDIA_DIR`.
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

## What was actually verified — now against a real production deployment

This section originally said these items were only verified locally,
pending real VPS access. That access happened — `money.therealworldboosts.com`,
deployed on a shared production CloudPanel VPS alongside other live
sites — and every item below was re-verified against that real
deployment, not just locally.

| Item | Status |
|---|---|
| `docker compose build` / `up` (db + web, no Caddy), unique Compose project name | **Verified live** (no conflict with the VPS's other Docker projects) |
| Postgres data persists as real files under a bind-mounted host directory, correct UID (70:70, verified — not assumed) | **Verified live** |
| `backup_db` writes a real file under the bind-mounted backup directory | **Verified live** |
| Restoring that backup reproduces the schema and data correctly | **Verified live** (restored into an isolated database on the same server) |
| App reachable via `127.0.0.1:$APP_PORT` on the host | **Verified live** |
| `SECURE_PROXY_SSL_HEADER` works correctly behind a reverse proxy | **Verified live** against CloudPanel's actual Nginx (HTTPS, HSTS, `Secure` cookies, no redirect loop) |
| CloudPanel reverse-proxy site configuration | **Verified live** via `clpctl site:add:reverse-proxy` + `lets-encrypt:install:certificate` |
| CloudPanel's backup scope covering `$POSTGRES_BACKUP_DIR` | **Checked directly: this CloudPanel edition has no backup feature at all** (see above) — not a gap in this app, a property of the installed CloudPanel edition |
| Google Drive (or other) remote backup delivery | **Not configured** — no credentials were available or invented; `rclone` is installed but has zero remotes configured |
| Full application smoke test against the live domain (login, mobile nav/overflow at 390px, income/expense, mobile money fee, transfer, borrowing+repayment, lending+repayment, daily closing, monthly report, savings goal + scenario, admin-panel isolation) | **Verified live** — 15/15 automated checks passed, using a disposable test account deleted immediately afterward |
| A real bug this deployment caught | Deleting a user with any transaction/transfer/borrowing/lending history raised `ProtectedError` — fixed in `apps/core/user_deletion.py`, regression-tested, redeployed, re-verified live |
