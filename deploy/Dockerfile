FROM python:3.12-slim-bookworm

# --- System deps: Tesseract (eng+ben) for OCR, tzdata for BST, tini as init ---
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends \
        tesseract-ocr \
        tesseract-ocr-eng \
        tesseract-ocr-ben \
        tzdata \
        tini \
        curl \
    && rm -rf /var/lib/apt/lists/*

ENV TZ=Asia/Dhaka
RUN ln -snf /usr/share/zoneinfo/$TZ /etc/localtime && echo $TZ > /etc/timezone

WORKDIR /app

# --- Python deps first (layer cache) ---
COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# --- App code + seed data (DB, uploads, tessdata) ---
COPY . /app

# Keep an immutable seed copy of the initial DB + uploads; the entrypoint
# copies these into the persistent volume only on first boot.
RUN mkdir -p /seed/instance /seed/uploads \
    && (cp -a /app/instance/. /seed/instance/ 2>/dev/null || true) \
    && (cp -a /app/uploads/. /seed/uploads/ 2>/dev/null || true) \
    && (cp -a /app/app/uploads/. /seed/uploads/ 2>/dev/null || true)

# Runtime config: persistent data lives under /data (a PVC).
ENV UPLOAD_FOLDER=/data/uploads \
    BACKUP_FOLDER=/data/backups \
    TESSERACT_CMD=/usr/bin/tesseract \
    TESSDATA_PREFIX=/usr/share/tesseract-ocr/5/tessdata \
    OCR_ENABLED=1 \
    PYTHONUNBUFFERED=1

COPY deploy/entrypoint.sh /usr/local/bin/entrypoint.sh
RUN sed -i 's/\r$//' /usr/local/bin/entrypoint.sh && chmod +x /usr/local/bin/entrypoint.sh

EXPOSE 5001
ENTRYPOINT ["/usr/bin/tini", "--", "/usr/local/bin/entrypoint.sh"]
CMD ["python", "serve.py"]
