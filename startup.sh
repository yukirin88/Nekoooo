#!/bin/bash

# jqコマンドのインストール（Renderではroot権限がないため削除）
# apt update && apt install -y jq

TOKEN=$GITHUB_TOKEN
REPO="yukirin88/Nekoooo"
BRANCH="db-backup"

echo "🔍 Getting latest backup DB filename from GitHub..."

# GitHub API から最新の .db ファイルを取得（typeチェック削除）
LATEST_DB=$(curl -s -H "Authorization: token $TOKEN" \
                 -H "Accept: application/vnd.github.v3+json" \
  "https://api.github.com/repos/$REPO/contents?ref=$BRANCH" | \
  jq -r '[.[] | select(.name | test("^attendance_.*\\.db$"))][0].name')

if [ -z "$LATEST_DB" ] || [ "$LATEST_DB" = "null" ]; then
  echo "⚠️ No backup DB found. Starting fresh."
else
  echo "✅ Found backup: $LATEST_DB. Downloading..."
  curl -H "Authorization: token $TOKEN" \
       -f -o attendance.db \
       "https://raw.githubusercontent.com/$REPO/$BRANCH/$LATEST_DB"
fi

# アプリ起動
gunicorn attendance_system.app:app