FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=8080

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
       libreoffice-writer \
       python3-uno \
       fontconfig \
       fonts-liberation \
       fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*

# Exact Garamond only. No EB Garamond or fontconfig Garamond alias is installed.
# Put properly licensed Garamond .ttf/.otf files in backend/fonts/ through your
# private deployment process. The repository package intentionally contains no
# proprietary font binaries.
COPY fonts/ /usr/local/share/fonts/slc/
RUN fc-cache -f

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app

CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT}"]
