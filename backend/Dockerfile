# Сборка из корня репозитория (docker-compose.yml: context ".") — нужен общий каталог shared/
FROM python:3.12-slim
WORKDIR /app
# tesseract — OCR скриншотов; ffmpeg — кадры видео для OCR; util-linux — prlimit (ограничения ресурсов ffmpeg)
RUN apt-get update && apt-get install -y --no-install-recommends tesseract-ocr tesseract-ocr-rus tesseract-ocr-kaz ffmpeg util-linux && rm -rf /var/lib/apt/lists/*
COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY shared ./shared
COPY backend/app ./app
ENV SAQ_SHARED_DIR=/app/shared MEDIA_ROOT=/data/media TG_SESSION_PATH=/data/private/telegram.session
# непривилегированный пользователь: недоверенные видео и файлы жителей обрабатываются не от root
RUN useradd --system --uid 10001 --home /nonexistent saq && mkdir -p /data/media /data/private && chown -R saq:saq /data && chmod 700 /data/private
USER saq
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
