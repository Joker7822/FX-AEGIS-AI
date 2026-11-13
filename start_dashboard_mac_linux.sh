#!/bin/sh
set -eu
cd "$(dirname "$0")"
printf '%s\n' 'FX Multi-Agent AI - Dashboard v1.2' 'Open http://127.0.0.1:8501 and press F11 for full screen.'
python3 dashboard.py
