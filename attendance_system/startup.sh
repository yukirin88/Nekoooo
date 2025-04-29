#!/bin/bash

# 今日の日付を取得（例：2025-04-29）
TODAY=$(date +'%Y-%m-%d')

# GitHub上のDBファイル名（最新バックアップを指定）
FILENAME="attendance_${TODAY}.db"

# GitHubから最新DBをダウンロード（なければ昨日のも試す）
curl -H "Authorization: token $GITHUB_TOKEN" \
     -f -o attendance.db \
     "https://raw.githubusercontent.com/yukirin88/Nekoooo/db-backup/$FILENAME" || \
curl -H "Authorization: token $GITHUB_TOKEN" \
     -f -o attendance.db \
     "https://raw.githubusercontent.com/yukirin88/Nekoooo/db-backup/attendance.db"

# Flask アプリを gunicorn で起動
gunicorn app:app