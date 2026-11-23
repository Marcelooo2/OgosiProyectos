FROM node:22-bookworm-slim AS frontend
WORKDIR /build
COPY frontend/package*.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM python:3.13-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 APP_ENV=production STATIC_DIR=/srv/skillgrow/static
WORKDIR /srv/skillgrow
COPY backend/requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt && useradd --system --no-create-home skillgrow
COPY backend/app ./app
COPY --from=frontend /build/dist/frontend/browser ./static
USER skillgrow
EXPOSE 8000
CMD ["sh", "-c", "exec python -m uvicorn app.main:app --host 0.0.0.0 --port \"${PORT:-8000}\""]
