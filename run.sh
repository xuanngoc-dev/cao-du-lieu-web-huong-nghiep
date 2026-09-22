#!/usr/bin/env bash
# Khởi chạy giao diện web: http://127.0.0.1:8080
cd "$(dirname "$0")"
exec python3 main.py --mode ui
