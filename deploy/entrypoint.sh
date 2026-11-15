#!/bin/sh
# What the container runs. `api` and `ui` are the two services; anything else is run as
# given, which is how `wri-admin` and `wri-license inspect` are reached.
set -eu

case "${1:-api}" in
  api)
    # One worker, on purpose: the costed results an engagement is working with are held in
    # this process, so a second worker would answer from a different copy.
    exec uvicorn wri_engine.api.main:app --host 0.0.0.0 --port 8000 --workers 1 \
      --no-server-header
    ;;
  ui)
    exec streamlit run /app/app/streamlit_app.py \
      --server.address 0.0.0.0 --server.port 8501 --server.headless true \
      --browser.gatherUsageStats false
    ;;
  *)
    exec "$@"
    ;;
esac
