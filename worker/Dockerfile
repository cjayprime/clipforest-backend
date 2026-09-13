FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONPATH=/app/src

# FFmpeg (with libass/libx264), caption fonts and fontconfig.
RUN apt-get update \
 && apt-get install -y --no-install-recommends ffmpeg fontconfig fonts-dejavu-core fonts-inter ca-certificates curl \
 && rm -rf /var/lib/apt/lists/* \
 && fc-cache -f

# OpenCV YuNet face detector (~230 KB). If the download fails the worker falls back to Haar cascades.
RUN mkdir -p /opt/models \
 && (curl -fsSL --retry 3 -o /opt/models/face_detection_yunet_2023mar.onnx \
      https://github.com/opencv/opencv_zoo/raw/main/models/face_detection_yunet/face_detection_yunet_2023mar.onnx \
     || echo "YuNet download failed; Haar fallback will be used")

WORKDIR /app
COPY requirements.txt requirements-dev.txt ./
RUN pip install -r requirements-dev.txt

COPY pyproject.toml ./
COPY src ./src

# Least privilege: no root, only the bounded working directory is writable.
RUN useradd --create-home --uid 10001 worker \
 && mkdir -p /tmp/video-jobs \
 && chown -R worker:worker /tmp/video-jobs
USER worker

EXPOSE 9100
CMD ["python", "-m", "cliprover_worker.main"]
