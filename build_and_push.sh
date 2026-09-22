#!/usr/bin/env bash
set -euo pipefail

docker build --platform linux/amd64,linux/arm64 -t huongnghiep_crawl:latest .
docker tag huongnghiep_crawl:latest 103.226.249.128:8142/huongnghiep/huongnghiep_crawl:latest
docker push 103.226.249.128:8142/huongnghiep/huongnghiep_crawl:latest
