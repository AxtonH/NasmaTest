"""Backend package marker for Gunicorn import path support.

This makes `backend` a Python package so `backend.wsgi:app` can be
imported by process managers in production environments.
"""


