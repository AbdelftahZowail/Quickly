# multi-stage Dockerfile for quick, minimal production image

# 1. build the React frontend
FROM node:18-alpine AS frontend-builder
WORKDIR /app/frontend

# copy only package.json files first to leverage layer caching
COPY frontend/package*.json ./
RUN npm ci

# copy the rest of the frontend code and build
COPY frontend/ .
RUN npm run build


# 2. production Python image
FROM python:3.12-slim AS backend
WORKDIR /app

# install runtime dependencies
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# copy backend source code
COPY app/ ./app/
COPY README.md ./
# copy anything else the application might need (templates, etc.)
COPY static/ ./static/ 2>/dev/null || true

# copy the compiled frontend assets from the builder stage
# the backend expects the build to live under frontend/dist so that
# `app.main` can mount /assets and serve index.html
COPY --from=frontend-builder /app/frontend/dist ./frontend/dist

# expose the port our FastAPI server listens on
EXPOSE 8000

# default command; environment variables (DATABASE_URL etc.) are supplied
# at runtime rather than baked into the image
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
