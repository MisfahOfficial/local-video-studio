#!/bin/zsh
set -e

STUDIO_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$STUDIO_DIR"
export PATH="/opt/homebrew/opt/ffmpeg-full/bin:/opt/homebrew/bin:/usr/local/bin:$PATH"

if [[ ! -d .venv ]]; then
  echo "Preparing Local Video Studio for first use..."
  python3 -m venv .venv
fi

source .venv/bin/activate

if ! python -c "import certifi, yt_dlp" >/dev/null 2>&1; then
  echo "Installing secure certificate and YouTube sourcing support..."
  python -m pip install --disable-pip-version-check --quiet "certifi>=2024.8.30" "yt-dlp>=2025.1.15"
fi

echo "Starting Local Video Studio..."
echo "Keep this window open while you use the tool. Press Control-C here to stop it."
python run.py
