# Build context MUST be the repository root (see docker-compose.yml `build.context: ..`)
# so this Dockerfile can COPY both backend/ and frontend/ into the image.
FROM python:3.12-slim

WORKDIR /app

# psycopg2-binary needs libpq at runtime; curl is used by the healthcheck.
# WeasyPrint (PDF report export) needs Pango/Cairo/GDK-Pixbuf for text shaping
# and rendering, plus shared-mime-info for MIME detection. fonts-dejavu-core
# and fonts-noto-core supply glyph coverage for Vietnamese diacritics in PDFs.
# fonts-inter provides Inter, the HSC Design System typeface that PDF reports keep
# (the web UI has its own self-hosted fonts in frontend/vendor/). Noto / DejaVu remain
# as glyph fallbacks.
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq5 \
    curl \
    libpango-1.0-0 \
    libpangocairo-1.0-0 \
    libcairo2 \
    libgdk-pixbuf-2.0-0 \
    libffi8 \
    shared-mime-info \
    fonts-dejavu-core \
    fonts-noto-core \
    fonts-inter \
    && rm -rf /var/lib/apt/lists/*

# Commit the image was built from — shown by the MCP tools next to errors and timings,
# so a regression can be tied to a release (set by .github/workflows/build-push.yml).
ARG GIT_SHA=dev
ENV APP_VERSION=$GIT_SHA

COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY backend/app ./app
# app/main.py computes FRONTEND_DIR as three parents above main.py itself
# (i.e. Path(__file__).parent.parent.parent). With this image's layout
# (/app/app/main.py) that resolves to "/frontend" — copy it there so FastAPI
# can serve it as static files.
COPY frontend ../frontend

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD curl -f http://localhost:8000/api/health || exit 1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
