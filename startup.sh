#!/bin/bash

# 今日と昨日の日付取得
TODAY=$(date +'%Y-%m-%d')
YESTERDAY=$(date -d "yesterday" +'%Y-%m-%d')
TOKEN=$GITHUB_TOKEN

# 今日のDBを試す
echo "🔍 Trying today's DB: attendance_$TODAY.db"
curl -H "Authorization: token $TOKEN" \
     -f -o attendance.db \
     https://raw.githubusercontent.com/yukirin88/Nekoooo/db-backup/attendance_$TODAY.db || {

  # 昨日のDBを試す
  echo "❗ Not found. Trying yesterday's DB: attendance_$YESTERDAY.db"
  curl -H "Authorization: token $TOKEN" \
       -f -o attendance.db \
       https://raw.githubusercontent.com/yukirin88/Nekoooo/db-backup/attendance_$YESTERDAY.db || {

    echo "⚠️ DB not found. Starting fresh."
  }
}

# アプリ起動
gunicorn attendance_system.app:app