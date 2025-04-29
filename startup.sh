#!/bin/bash

# jqコマンドを使うためにインストール（Render用）
apt update && apt install -y jq

TOKEN=$GITHUB_TOKEN
REPO="yukirin88/Nekoooo"
BRANCH="db-backup"

# 最新の .db ファイル名を取得（降順ソートで最初の1件を取る）
echo "🔍 Getting latest backup DB filename from GitHub..."

LATEST_DB=$(curl -s -H "Authorization: token $TOKEN" \
  "https://api.github.com/repos/$REPO/contents/?ref=$BRANCH" | \
  jq -r '.[] | select(.name | test("^attendance_.*\\.db$")) | .name' | sort -r | head -n 1)

if [ -z "$LATEST_DB" ]; then
  echo "⚠️ No backup DB found. Starting fresh."
else
  echo "✅ Found backup: $LATEST_DB. Downloading..."
  curl -H "Authorization: token $TOKEN" \
       -f -o attendance.db \
       "https://raw.githubusercontent.com/$REPO/$BRANCH/$LATEST_DB"
fi

# アプリ起動
gunicorn attendance_system.app:app