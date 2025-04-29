#!/bin/bash

echo "🔍 Getting latest backup DB filename from GitHub..."

# jqがインストールされていなければエラーで停止
if ! command -v jq &> /dev/null; then
  echo "⚠️ jq is not installed. Please contact admin."
  exit 1
fi

# 環境変数からトークン取得（Renderでは自動設定されている）
TOKEN=$GITHUB_TOKEN
REPO="yukirin88/Nekoooo"
BRANCH="db-backup"

# GitHub API で contents 一覧を取得
RESPONSE=$(curl -s -H "Authorization: token $TOKEN" \
                  -H "Accept: application/vnd.github.v3+json" \
                  "https://api.github.com/repos/$REPO/contents?ref=$BRANCH")

# デバッグ情報表示
echo "🧪 TOKEN value check: ${#TOKEN} characters"
echo "🧪 GitHub API response (truncated):"
echo "$RESPONSE" | head -n 20  # レスポンスが長い場合に備えて20行まで表示

# .dbファイル名を正規表現で抽出し、降順でソートして最新の1件を取得
LATEST_DB=$(echo "$RESPONSE" | jq -r '
  select(type == "array") | .[] | select(.name | test("^attendance_.*\\.db$")) | .name' | sort -r | head -n 1)

# ファイルが存在していればダウンロード
if [ -z "$LATEST_DB" ]; then
  echo "⚠️ No backup DB found. Starting fresh."
else
  echo "✅ Found backup: $LATEST_DB. Downloading..."
  curl -s -H "Authorization: token $TOKEN" \
       -o attendance.db \
       "https://raw.githubusercontent.com/$REPO/$BRANCH/$LATEST_DB"
fi

# Flask アプリ起動（gunicornで）
gunicorn attendance_system.app:app