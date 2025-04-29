#!/bin/bash

# DBをGitHubから取得
curl -H "Authorization: token $GITHUB_TOKEN" \
     -o attendance.db \
     https://raw.githubusercontent.com/yukirin88/Nekoooo/db-backup/attendance.db

# Flask アプリ起動
gunicorn app:app