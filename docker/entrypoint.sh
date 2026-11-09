#!/bin/sh
set -e

echo "Waiting for the database..."
until pg_isready -h "${DB_HOST:-db}" -p "${DB_PORT:-5432}" -U "${POSTGRES_USER:-mymoney}" >/dev/null 2>&1; do
  sleep 2
done

echo "Applying database migrations..."
python manage.py migrate --no-input

echo "Seeding default categories..."
python manage.py seed_categories

echo "Starting Gunicorn..."
exec gunicorn config.wsgi:application --bind 0.0.0.0:8000 --workers 3 --timeout 60
