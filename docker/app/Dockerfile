# Image Python dùng chung cho api, worker, admin-ui (stage `app`) và parser sidecar (stage `parser`).
# Python 3.12 — bản toàn bộ test suite đã chạy (code tương thích 3.11+).
FROM python:3.12-slim AS base
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1 \
    HF_HOME=/models/hf PYTHONPATH=/app
RUN apt-get update && apt-get install -y --no-install-recommends \
        ffmpeg libgl1 libglib2.0-0 curl ca-certificates postgresql-client \
    && rm -rf /var/lib/apt/lists/*
# torch bản CPU (tránh kéo CUDA ~2GB) cài trước, requirements.txt pin cùng version nên pip không cài lại
RUN pip install torch==2.13.0 --index-url https://download.pytorch.org/whl/cpu
COPY requirements.txt /tmp/requirements.txt
RUN pip install -r /tmp/requirements.txt
RUN useradd --create-home --uid 1000 app && mkdir -p /models/hf /app/data && chown -R app:app /models /app

FROM base AS app
WORKDIR /app
COPY --chown=app:app . /app
# thư mục mount volume phải tồn tại + thuộc user app trong image -> volume mới tạo kế thừa quyền ghi
RUN mkdir -p /app/data/uploads /app/data/parsework/inbox /app/data/parsework/outbox /app/data/security/audit \
    && chown -R app:app /app/data
USER app
EXPOSE 8000
CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8000"]

# Parser sidecar: KHÔNG có mạng + rootfs read-only lúc chạy -> tải sẵn model docling (layout/table) và
# faster-whisper vào thư mục cố định trong image, trỏ bằng biến môi trường (không dùng HF cache lúc chạy).
FROM app AS parser
ARG WHISPER_MODEL_SIZE=small
USER root
RUN mkdir -p /models/docling /models/whisper /work/inbox /work/outbox && chown -R app:app /models /work
USER app
RUN python -c "from pathlib import Path; from docling.utils.model_downloader import download_models; download_models(output_dir=Path('/models/docling'))" \
    && python -c "from faster_whisper import download_model; download_model('${WHISPER_MODEL_SIZE}', output_dir='/models/whisper')"
ENV HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 DOCLING_ARTIFACTS_PATH=/models/docling WHISPER_MODEL_PATH=/models/whisper
CMD ["python", "-m", "security.parser_sidecar", "--work", "/work"]
