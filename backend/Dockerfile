# InPage → PDF converter — production image
# Build:  docker build -t inpage2pdf .
# Run:    docker run -d -p 8080:8080 -e INPAGE2PDF_API_KEY="mera-secret-key" inpage2pdf
FROM python:3.12-slim

#	gs/fonts not needed — fonts are bundled; keep the image lean
WORKDIR /srv/inpage2pdf

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt gunicorn

COPY . .

#	non-root user, read-friendly app dir
RUN useradd -m appuser && chown -R appuser:appuser /srv/inpage2pdf
USER appuser

ENV PORT=8080
EXPOSE 8080

#	2 workers is plenty (conversion is CPU-bound per request)
CMD ["sh", "-c", "gunicorn -w 2 -b 0.0.0.0:${PORT} --timeout 300 wsgi:app"]
