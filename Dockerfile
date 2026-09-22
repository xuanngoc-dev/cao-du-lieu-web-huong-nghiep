# Hệ thống tổng hợp dữ liệu tuyển sinh — giao diện web (Flask + Waitress)
FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UI_PORT=8080

WORKDIR /app

# Thư viện hệ thống (tuỳ chọn, hỗ trợ một số thao tác PDF/font)
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
        libjpeg62-turbo \
        zlib1g \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN mkdir -p data/input_files data/output data/cache

EXPOSE 8080

# Waitress trên 0.0.0.0 để truy cập từ ngoài container
CMD ["python", "-c", "import os; from waitress import serve; from web.app import app; serve(app, host='0.0.0.0', port=int(os.environ.get('UI_PORT', '8080')), threads=4)"]
