FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1

# gettext нужен для compilemessages; poppler-utils понадобится для превью PDF (этап 4).
RUN apt-get update \
    && apt-get install -y --no-install-recommends gettext \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Переводы и статика собираются в образ. Значения ниже нужны только для сборки.
RUN DJANGO_DEBUG=1 SECRET_KEY=build-only PUBLIC_BASE_URL=http://localhost python manage.py compilemessages \
    && DJANGO_DEBUG=1 SECRET_KEY=build-only PUBLIC_BASE_URL=http://localhost python manage.py collectstatic --noinput

RUN useradd --system --uid 10001 --home /app app \
    && mkdir -p /data/private_media \
    && chown -R app:app /data
USER app

# Формат журнала без query string: в ссылках приглашений есть токен.
EXPOSE 8000
CMD ["gunicorn", "dataroom.wsgi:application", "--bind", "0.0.0.0:8000", "--workers", "3", "--access-logfile", "-",
     "--access-logformat", "%(h)s %(t)s \"%(m)s %(U)s %(H)s\" %(s)s %(b)s"]
