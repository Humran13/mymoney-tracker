import subprocess
from datetime import datetime
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = "Dumps the PostgreSQL database to backups/<timestamp>.sql using pg_dump."

    def handle(self, *args, **options):
        db = settings.DATABASES["default"]
        backup_dir = Path(settings.BASE_DIR) / "backups"
        backup_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        out_path = backup_dir / f"mymoney_{timestamp}.sql"

        cmd = [
            "pg_dump",
            "--no-owner",
            "--no-privileges",
            "-h", str(db.get("HOST") or "localhost"),
            "-p", str(db.get("PORT") or "5432"),
            "-U", str(db.get("USER")),
            "-d", str(db.get("NAME")),
            "-f", str(out_path),
        ]
        env = {"PGPASSWORD": str(db.get("PASSWORD") or "")}
        import os

        full_env = {**os.environ, **env}
        try:
            subprocess.run(cmd, check=True, env=full_env)
        except FileNotFoundError as exc:
            raise CommandError("pg_dump is not installed/available on PATH.") from exc
        except subprocess.CalledProcessError as exc:
            raise CommandError(f"pg_dump failed: {exc}") from exc

        self.stdout.write(self.style.SUCCESS(f"Backup written to {out_path}"))
