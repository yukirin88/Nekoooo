#!/bin/bash

echo "🔧 Checking jq..."
if ! command -v jq &> /dev/null; then
  echo "⚠️ jq is not installed. Please contact admin."
  exit 1
fi

echo "🔍 Getting latest backup DB filename from GitHub..."
TOKEN=$GITHUB_TOKEN
REPO="yukirin88/Nekoooo"
BRANCH="db-backup"

RESPONSE=$(curl -s -H "Authorization: token $TOKEN" \
                  -H "Accept: application/vnd.github.v3+json" \
                  "https://api.github.com/repos/$REPO/contents?ref=$BRANCH")

echo "🧪 TOKEN value check: ${#TOKEN} characters"
echo "🧪 GitHub API response (truncated):"
echo "$RESPONSE" | head -n 20

LATEST_DB=$(echo "$RESPONSE" | jq -r 'select(type == "array") | .[] | select(.name | test("^attendance_.*\\.db$")) | .name' | sort -r | head -n 1)

if [ -z "$LATEST_DB" ]; then
  echo "⚠️ No backup DB found. Starting fresh."
else
  echo "✅ Found backup: $LATEST_DB. Downloading..."
  curl -s -H "Authorization: token $TOKEN" \
       -o attendance.db \
       "https://raw.githubusercontent.com/$REPO/$BRANCH/$LATEST_DB"
fi

# アプリ起動
gunicorn attendance_system.app:app