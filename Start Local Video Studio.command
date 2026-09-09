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
echo "Starting Local Video Studio..."
echo "Keep this window open while you use the tool. Press Control-C here to stop it."
python run.py
