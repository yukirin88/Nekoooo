echo "🔍 Getting latest backup DB filename from GitHub..."

# ✅ 最初に代入（本番では $GITHUB_TOKEN を使う）
TOKEN=$GITHUB_TOKEN
# TOKEN=$GITHUB_TOKEN ← Render上で使うときはこちら

# デバッグ用：レスポンス内容を表示して中身確認
RESPONSE=$(curl -s -H "Authorization: token $TOKEN" \
                  -H "Accept: application/vnd.github.v3+json" \
                  "https://api.github.com/repos/$REPO/contents?ref=$BRANCH")

echo "🧪 TOKEN value check: ${#TOKEN} characters"
echo "🧪 GitHub API response:"
echo "$RESPONSE"

# jq処理（念のため防御付き）
LATEST_DB=$(echo "$RESPONSE" | jq -r 'select(type == "array") | .[] | select(.name | test("^attendance_.*\\.db$")) | .name' | sort -r | head -n 1)