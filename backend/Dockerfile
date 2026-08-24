FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=8080

# Install LibreOffice and required fonts.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
       libreoffice-writer \
       fontconfig \
       fonts-ebgaramond \
       fonts-liberation \
       fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*

# Tell Linux/LibreOffice to use EB Garamond whenever
# a Word document requests the family "Garamond".
RUN mkdir -p /etc/fonts/conf.d \
    && printf '%s\n' \
       '<?xml version="1.0"?>' \
       '<!DOCTYPE fontconfig SYSTEM "fonts.dtd">' \
       '<fontconfig>' \
       '  <alias>' \
       '    <family>Garamond</family>' \
       '    <prefer>' \
       '      <family>EB Garamond</family>' \
       '    </prefer>' \
       '  </alias>' \
       '</fontconfig>' \
       > /etc/fonts/local.conf \
    && fc-cache -f -v

WORKDIR /app

COPY requirements.txt .

RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app

CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT}"]
