#!/usr/bin/env bash
# CrisPRO live API demo — merck_tuvusertib (RUO)
# Requires the v3 backend on :8000 (and v2 on :8001 if mounted).
set -euo pipefail

echo '--- 1) Ayesha tumor-board bundle (v3) ---'
curl -s -X POST "http://127.0.0.1:8000/api/ayesha/therapy-fit/bundle" \
  -H 'Content-Type: application/json' -d '{}' | head -c 800; echo

echo '--- 2) 8D bilinear ranker (Stage-2A signed decoder) ---'
curl -s -X POST "http://127.0.0.1:8001/v1/ranker/8d/bilinear" \
  -H 'Content-Type: application/json' \
  -d '{"patient_8d": [0.9, 0.3, 0.2, 0.2, 0.4, 0.3, 0.2, 0.2]}' | head -c 800; echo

echo '--- 3) AACR 2026 KB search (v3 kb router) ---'
curl -s "http://127.0.0.1:8000/api/kb/aacr2026/search?q=ATR&limit=3" | head -c 800; echo
