#!/usr/bin/env bash
# Local quick run helper
set -e
cd "$(dirname "$0")"

if [ ! -d venv ]; then
  echo "🐍 venv ban raha hai..."
  python3 -m venv venv
fi
source venv/bin/activate
pip install -q -r requirements.txt

if [ ! -f .env ]; then
  echo "⚠️  .env nahi mila — .env.example copy karo:"
  echo "   cp .env.example .env"
  echo "   phir apne values daal ke dobara chalao."
  exit 1
fi

python bot.py
