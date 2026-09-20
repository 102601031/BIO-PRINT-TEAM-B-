#!/usr/bin/env bash
# BioPrint launcher (Mac/Linux) — run with: ./run_bioprint.sh
# Installs dependencies on first run, then starts the server and
# opens it in your default browser.
set -e
cd "$(dirname "$0")"

if ! command -v python3 &> /dev/null; then
    echo "Python 3 was not found. Install it from https://python.org and re-run this script."
    exit 1
fi

echo "Installing/checking dependencies..."
python3 -m pip install -r requirements.txt --quiet --break-system-packages 2>/dev/null || \
    python3 -m pip install -r requirements.txt --quiet

( sleep 1.5 && (open http://127.0.0.1:5000/ 2>/dev/null || xdg-open http://127.0.0.1:5000/ 2>/dev/null || true) ) &
python3 app.py
