#!/bin/zsh
set -e

STUDIO_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$STUDIO_DIR"
export PATH="/opt/homebrew/opt/ffmpeg-full/bin:/opt/homebrew/bin:/usr/local/bin:$PATH"

echo "Updating Local Video Studio..."
if [[ ! -d .git ]]; then
  echo "This copy is not connected to GitHub. Clone the repository first."
  read -k 1 "?Press any key to close."
  exit 1
fi

if [[ -n "$(git status --porcelain)" ]]; then
  echo "Update stopped because this program folder has unsaved code changes."
  echo "Your projects and images are safe. Commit or remove only the code changes, then try again."
  read -k 1 "?Press any key to close."
  exit 1
fi

STUDIO_BRANCH="$(git branch --show-current)"
git pull --ff-only origin "$STUDIO_BRANCH"

if [[ ! -d .venv ]]; then
  python3 -m venv .venv
fi
source .venv/bin/activate
python -m unittest discover -s tests -v

echo "Update complete on branch: $STUDIO_BRANCH"
echo "Double-click Start Local Video Studio.command to launch it."
read -k 1 "?Press any key to close."
