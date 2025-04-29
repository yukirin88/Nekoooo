#!/bin/bash

echo "🔧 Installing jq (if not already installed)..."
apt-get update && apt-get install -y jq

echo "🔍 Getting latest backup DB filename from GitHub..."

# 環境変数からトークン取得（Renderではここに自動で設定）
TOKEN=$GITHUB_TOKEN
REPO="yukirin88/Nekoooo"
BRANCH="db-backup"

# ✅ ファイル一覧取得URLを明示的に指定（tree API使用）
RESPONSE=$(curl -s -H "Authorization: token $TOKEN" \
                  -H "Accept: application/vnd.github.v3+json" \
                  "https://api.github.com/repos/$REPO/git/trees/$BRANCH?recursive=1")

# デバッグ情報
echo "🧪 TOKEN value check: ${#TOKEN} characters"
echo "🧪 GitHub API response (truncated):"
echo "$RESPONSE" | head -n 20

# .db ファイル名の最新を抽出
LATEST_DB=$(echo "$RESPONSE" | jq -r '.tree[] | select(.path | test("^attendance_.*\\.db$")) | .path' | sort -r | head -n 1)

# ファイルが存在していればダウンロード
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