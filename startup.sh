#!/bin/bash

echo "🔧 Checking for jq..."
if ! command -v jq &> /dev/null; then
  echo "jq not found. Installing..."
  if command -v apt-get &> /dev/null; then
    apt-get update && apt-get install -y jq
  else
    echo "jqの自動インストールに未対応の環境です。手動でインストールしてください。"
    exit 1
  fi
fi

echo "🔍 Getting latest backup DB filename from GitHub..."

TOKEN=$GITHUB_TOKEN
REPO="yukirin88/Nekoooo"
BRANCH="db-backup"

RESPONSE=$(curl -s -H "Authorization: token $TOKEN" \
                  -H "Accept: application/vnd.github.v3+json" \
                  "https://api.github.com/repos/$REPO/contents/?ref=$BRANCH")

echo "🧪 TOKEN value check: ${#TOKEN} characters"
echo "🧪 GitHub API response (truncated):"
echo "$RESPONSE" | head -n 20

# attendance_YYYY-MM-DD.db形式の最新ファイル名を取得
LATEST_DB=$(echo "$RESPONSE" | jq -r 'select(type == "array") | .[] | select(.name | test("^attendance_\\d{4}-\\d{2}-\\d{2}\\.db$")) | .name' | sort -r | head -n 1)

if [ -z "$LATEST_DB" ]; then
  echo "⚠️ No backup DB found. Starting fresh."
else
  echo "✅ Found backup: $LATEST_DB. Downloading..."
  curl -s -H "Authorization: token $TOKEN" \
       -o attendance.db \
       "https://raw.githubusercontent.com/$REPO/$BRANCH/$LATEST_DB"
  # ファイルサイズが1KB未満なら空DBとみなして削除
  if [ -f attendance.db ] && [ $(stat -c%s "attendance.db") -lt 1024 ]; then
    echo "⚠️ ダウンロードしたDBが空または異常です。attendance.dbを削除します。"
    rm attendance.db
  fi
fi

gunicorn attendance_system.app:app