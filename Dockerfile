FROM python:3.12-slim
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends libgomp1 && rm -rf /var/lib/apt/lists/*
COPY backend/requirements.txt backend/requirements.txt
RUN pip install --no-cache-dir -r backend/requirements.txt
COPY backend backend
COPY docs docs
ENV GEODEVELOP_DATA=/data
CMD ["sh","-c","uvicorn backend.app:app --host 0.0.0.0 --port ${PORT:-8080}"]
