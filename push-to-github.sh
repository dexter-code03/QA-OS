#!/bin/bash
# Create a new GitHub repo and push this project
set -e
cd "$(dirname "$0")"

GH="./gh_2.88.1_macOS_arm64/bin/gh"
if [ ! -f "$GH" ]; then
  echo "Downloading GitHub CLI..."
  curl -sL "https://github.com/cli/cli/releases/download/v2.88.1/gh_2.88.1_macOS_arm64.zip" -o gh.zip
  unzip -o gh.zip
fi

echo "Checking GitHub authentication..."
if ! $GH auth status &>/dev/null; then
  echo ""
  echo "You need to log in to GitHub first."
  echo "Run: $GH auth login"
  echo "Then run this script again."
  exit 1
fi

REPO_NAME="${1:-QA-OS}"
echo "Creating repo: $REPO_NAME"

# Remove old origin if it points to a non-existent or different repo
git remote remove origin 2>/dev/null || true

$GH repo create "$REPO_NAME" --private --source=. --remote=origin --push

echo ""
echo "Done! Your repo is at: https://github.com/$(gh api user -q .login)/$REPO_NAME"
