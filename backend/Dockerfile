FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=8080

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
       libreoffice-writer \
       fontconfig \
       fonts-liberation \
       fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*

# Install the exact licensed Garamond fonts used by Microsoft Word
RUN mkdir -p /usr/local/share/fonts/garamond

COPY fonts/GARA.TTF /usr/local/share/fonts/garamond/GARA.TTF
COPY fonts/GARABD.TTF /usr/local/share/fonts/garamond/GARABD.TTF
COPY fonts/GARAIT.TTF /usr/local/share/fonts/garamond/GARAIT.TTF

# Rebuild the Linux font cache
RUN fc-cache -f -v

WORKDIR /app

COPY requirements.txt .

RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app

CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT}"]
