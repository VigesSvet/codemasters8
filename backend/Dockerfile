# Сборка из корня репозитория(docker-compose.yml: context ".") - нужен общий каталог shared/.
# Выпуск Debian закреплён(trixie): от него зависят версии Tesseract и ffmpeg, новый выпуск - только осознанно
FROM python:3.12-slim-trixie
# вывод команд в консоли(docker compose exec -T api python -m app.snapshot ..., app.speech download) - сразу, а не блоками
ENV PYTHONUNBUFFERED=1 PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /app
# tesseract - OCR скриншотов; ffmpeg - кадры и звук видео(OCR, распознавание речи); util-linux - prlimit(ограничения ресурсов ffmpeg)
RUN apt-get update && apt-get install -y --no-install-recommends tesseract-ocr tesseract-ocr-rus tesseract-ocr-kaz ffmpeg util-linux && rm -rf /var/lib/apt/lists/*
COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY shared ./shared
COPY backend/app ./app
# байт-код - при сборке: каталог кода процессу не доступен на запись, и без этого каждый запуск(api, worker,
# docker compose exec) компилировал бы все модули заново
RUN python -m compileall -q app
# модели распознавания речи и служебные файлы HuggingFace - в /data/models(у пользователя saq нет домашнего каталога)
ENV SAQ_SHARED_DIR=/app/shared MEDIA_ROOT=/data/media TG_SESSION_PATH=/data/private/telegram.session SAQ_ASR_MODEL_DIR=/data/models HF_HOME=/data/models/.hf
# непривилегированный пользователь: недоверенные видео и файлы жителей обрабатываются не от root. Код и shared/ остаются
# во владении root - процесс их не меняет. Группа с тем же номером 10001: useradd --system без --gid выдал бы группе номер
# из системного диапазона, и USER 10001:10001 не совпал бы с владельцем /data
RUN groupadd --system --gid 10001 saq \
 && useradd --system --uid 10001 --gid saq --no-create-home --no-log-init --home-dir /nonexistent --shell /usr/sbin/nologin saq \
 && mkdir -p /data/media /data/private /data/models && chown -R saq:saq /data && chmod 700 /data/private
# числовой пользователь: среда запуска(Kubernetes runAsNonRoot) проверяет по номеру, что процесс не от root
USER 10001:10001
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
