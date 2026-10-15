# Сборка из корня репозитория (docker-compose.yml: context ".") — нужен общий каталог shared/
FROM python:3.12-slim
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends tesseract-ocr tesseract-ocr-rus tesseract-ocr-kaz && rm -rf /var/lib/apt/lists/*
COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt pytesseract pillow
COPY shared ./shared
COPY backend/app ./app
ENV SAQ_SHARED_DIR=/app/shared
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
