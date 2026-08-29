# Procfile — Heroku / Railway / Render / Fly.io deployment
#
# Starts the FastAPI application with Uvicorn using 2 workers.
# Adjust --workers based on your dyno/container CPU count.
# For Gunicorn-based deployments, swap the web: line.

web: uvicorn app.main:app --host 0.0.0.0 --port $PORT --workers 2 --log-level info
