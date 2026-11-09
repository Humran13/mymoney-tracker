import os
import subprocess
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = "Restores the PostgreSQL database from a backups/*.sql file produced by backup_db."

    def add_arguments(self, parser):
        parser.add_argument("filename", help="Backup filename inside the backups/ directory (not a full path).")

    def handle(self, *args, **options):
        filename = options["filename"]
        if os.path.basename(filename) != filename:
            raise CommandError("filename must not contain path separators.")

        backup_dir = Path(settings.BASE_DIR) / "backups"
        in_path = backup_dir / filename
        if not in_path.resolve().parent == backup_dir.resolve() or not in_path.exists():
            raise CommandError(f"Backup file not found: {in_path}")

        db = settings.DATABASES["default"]
        cmd = [
            "psql",
            "-h", str(db.get("HOST") or "localhost"),
            "-p", str(db.get("PORT") or "5432"),
            "-U", str(db.get("USER")),
            "-d", str(db.get("NAME")),
            "-f", str(in_path),
        ]
        full_env = {**os.environ, "PGPASSWORD": str(db.get("PASSWORD") or "")}
        try:
            subprocess.run(cmd, check=True, env=full_env)
        except FileNotFoundError as exc:
            raise CommandError("psql is not installed/available on PATH.") from exc
        except subprocess.CalledProcessError as exc:
            raise CommandError(f"Restore failed: {exc}") from exc

        self.stdout.write(self.style.SUCCESS(f"Database restored from {in_path}"))
