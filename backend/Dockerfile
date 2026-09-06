FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=8080

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
       libreoffice-writer \
       fontconfig \
       fonts-ebgaramond \
       fonts-crosextra-carlito \
       fonts-crosextra-caladea \
       fonts-liberation \
       fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*

# Font family aliases used when LibreOffice renders Office-authored DOCX files.
# No proprietary Microsoft font files are bundled in this repository.
COPY fontconfig/99-slc-font-aliases.conf /etc/fonts/conf.d/99-slc-font-aliases.conf
RUN fc-cache -f

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app

CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT}"]
