# =============================================================================
# CREDENT — Dockerfile
# A product of Asenra | https://asenra.in
# Copyright (c) 2026 Asenra. All rights reserved.
# =============================================================================

# USE A STABLE PYTHON BASE IMAGE
FROM python:3.11-slim

# PREVENT PYTHON FROM WRITING .PYC FILES
ENV PYTHONDONTWRITEBYTECODE 1
ENV PYTHONUNBUFFERED 1

# INSTALL SYSTEM DEPENDENCIES FOR OCR AND PDF PROCESSING
# - tesseract-ocr: For OCR text extraction
# - poppler-utils: For pdf2image (PDF to image conversion)
RUN apt-get update && apt-get install -y --no-install-recommends \
    tesseract-ocr \
    poppler-utils \
    build-essential \
    gcc \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

# SET WORKING DIRECTORY
WORKDIR /app

# INSTALL PYTHON DEPENDENCIES
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# COPY APPLICATION CODE
COPY . .

# CREATE DIRECTORIES FOR PERSISTENT DATA AND TEMP UPLOADS
RUN mkdir -p /app/temp_uploads && chmod 777 /app/temp_uploads
RUN mkdir -p /app/app/database && chmod 777 /app/app/database

# EXPOSE PORT
EXPOSE 8000

# START THE APPLICATION
# Dynamically binds to $PORT provided by Render or defaults to 8000
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
