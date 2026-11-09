# --- Stage 1: compile the production Tailwind CSS build ---
FROM node:20-slim AS frontend
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci --no-fund --no-audit
COPY tailwind.config.js ./
COPY static_src ./static_src
COPY templates ./templates
RUN npm run build:css

# --- Stage 2: the Django application ---
FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DJANGO_SETTINGS_MODULE=config.settings.prod

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends postgresql-client \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .
COPY --from=frontend /app/static/css/tailwind.css ./static/css/tailwind.css

RUN mkdir -p /app/staticfiles /app/media /app/backups \
    && DJANGO_SECRET_KEY=build-time-only python manage.py collectstatic --no-input

RUN chmod +x docker/entrypoint.sh

EXPOSE 8000

ENTRYPOINT ["docker/entrypoint.sh"]
