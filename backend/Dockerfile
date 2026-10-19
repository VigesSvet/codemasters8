# Сборка из корня репозитория (docker-compose.yml: context ".") — нужен общий каталог shared/
FROM python:3.12-slim
WORKDIR /app
# tesseract — OCR скриншотов; ffmpeg — кадры и звук видео (OCR, распознавание речи); util-linux — prlimit (ограничения ресурсов ffmpeg)
RUN apt-get update && apt-get install -y --no-install-recommends tesseract-ocr tesseract-ocr-rus tesseract-ocr-kaz ffmpeg util-linux && rm -rf /var/lib/apt/lists/*
COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY shared ./shared
COPY backend/app ./app
# модели распознавания речи и служебные файлы HuggingFace — в /data/models (у пользователя saq нет домашнего каталога)
ENV SAQ_SHARED_DIR=/app/shared MEDIA_ROOT=/data/media TG_SESSION_PATH=/data/private/telegram.session SAQ_ASR_MODEL_DIR=/data/models HF_HOME=/data/models/.hf
# непривилегированный пользователь: недоверенные видео и файлы жителей обрабатываются не от root
RUN useradd --system --uid 10001 --home /nonexistent saq && mkdir -p /data/media /data/private /data/models && chown -R saq:saq /data && chmod 700 /data/private
USER saq
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
