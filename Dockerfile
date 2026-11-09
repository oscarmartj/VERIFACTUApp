FROM python:3.13-slim
RUN apt-get update && apt-get install -y --no-install-recommends \
      poppler-utils tesseract-ocr tesseract-ocr-spa qpdf zbar-tools && rm -rf /var/lib/apt/lists/*
RUN useradd --create-home --uid 10001 facturas
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app ./app
COPY scripts ./scripts
COPY docs ./docs
USER facturas
ENV VERIFACTU_DATA_DIR=/datos/data \
    VERIFACTU_DIARIO_DIR=/diario \
    VERIFACTU_CLAVE_MAESTRA_FICHERO=/secretos/clave_maestra.bin \
    VERIFACTU_COOKIE_SEGURA=1
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers"]
