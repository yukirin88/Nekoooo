#!/bin/bash

# GitHub からバックアップDBを取得（固定ファイル名）
curl -H "Authorization: token $GITHUB_TOKEN" \
     -f -o attendance.db \
     https://raw.githubusercontent.com/yukirin88/Nekoooo/db-backup/attendance.db || echo "DB not found, starting fresh."

# Flask アプリを gunicorn で起動（attendance_system内の app.py を指定）
gunicorn attendance_system.app:app