- name: Get date-based filename
  run: echo "DB_NAME=attendance_$(date +'%Y-%m-%d').db" >> $GITHUB_ENV

- name: Download DB from Render
  run: curl -o $DB_NAME https://yukirin-github-io-nekooo.onrender.com/attendance.db

- name: Commit to db-backup branch
  run: |
    git config --global user.name 'github-actions'
    git config --global user.email 'github-actions@github.com'
    git init
    git remote add origin https://github.com/yukirin88/Nekoooo.git
    git fetch
    git checkout -B db-backup
    git add $DB_NAME
    git commit -m "Backup on $(date)"
    git push https://x-access-token:${{ secrets.PERSONAL_TOKEN }}@github.com/yukirin88/Nekoooo.git db-backup --force