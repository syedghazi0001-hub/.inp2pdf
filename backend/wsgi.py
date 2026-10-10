#!/usr/bin/env python3
"""WSGI entry point for production servers (gunicorn / uwsgi).

Usage:
    pip install -r requirements.txt
    gunicorn -w 2 -b 0.0.0.0:8080 wsgi:app

Set INPAGE2PDF_API_KEY in the environment to require an API key.
"""
from app import app

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)
