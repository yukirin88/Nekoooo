#!/bin/bash
# GitHub からバックアップDBをダウンロード
curl -H "Authorization: token $GITHUB_TOKEN" \
     -o attendance.db \
     https://raw.githubusercontent.com/あなたのユーザー名/リポジトリ名/db-backup/attendance.db

# Flask アプリを起動
gunicorn app:app