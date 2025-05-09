from decimal import Decimal, ROUND_HALF_UP
import sys
import os
import shutil
import tempfile
import subprocess
from flask import Flask, render_template, request, redirect, url_for, session, flash, jsonify, send_file
from flask_bootstrap import Bootstrap
from flask_cors import CORS
from datetime import datetime, timedelta
from functools import wraps
import sqlite3
import hashlib
import calendar
import pytz
import psycopg2
from psycopg2.extras import DictCursor

# --- グローバル設定 ---
GITHUB_USERNAME = os.environ.get("GITHUB_USERNAME")
GITHUB_REPOSITORY = os.environ.get("GITHUB_REPOSITORY")
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN")
GIT_USER_EMAIL = os.environ.get("GIT_USER_EMAIL", "konosuke.hirata@gmail.com")
GIT_USER_NAME = os.environ.get("GIT_USER_NAME", "yukirin88")

DATABASE_URL = os.environ.get('DATABASE_URL')
# Render環境では、永続ディスクのパスをRENDER_DATA_DIRで指定することを推奨
# 例: /var/render/data や /opt/render/project/dataなど
# デフォルトは app.py と同じディレクトリ (attendance_system) になります。
# Renderで永続ディスクを設定し、そのマウントパスを環境変数 RENDER_DATA_DIR に設定してください。
RENDER_DATA_DIR_DEFAULT = os.path.dirname(os.path.abspath(__file__))
RENDER_DATA_DIR = os.environ.get('RENDER_DATA_DIR', RENDER_DATA_DIR_DEFAULT)
DATABASE_FILE = 'attendance.db'
DATABASE_PATH = os.path.join(RENDER_DATA_DIR, DATABASE_FILE)
TIMEZONE = pytz.timezone('Asia/Tokyo')

app = Flask(__name__, template_folder='templates')
Bootstrap(app)
CORS(app)
app.secret_key = os.environ.get('SECRET_KEY', 'your-very-secret-key-please-change')
app.config.update(
    SESSION_COOKIE_SECURE=True,
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_REFRESH_EACH_REQUEST=True,
    PERMANENT_SESSION_LIFETIME=timedelta(hours=2)
)

# --- データベースディレクトリ関連 ---
def ensure_db_directory_exists(db_path_to_check=DATABASE_PATH):
    db_dir = os.path.dirname(db_path_to_check)
    if db_dir and not os.path.exists(db_dir):
        try:
            os.makedirs(db_dir, exist_ok=True) # exist_ok=True を追加
            print(f"ディレクトリ {db_dir} を作成しました（または既に存在）。")
        except OSError as e:
            print(f"ディレクトリ {db_dir} の作成に失敗しました: {e}")
            raise

# --- GitHubバックアップ・リストア関連 ---
def is_db_empty(db_path_to_check=DATABASE_PATH):
    if not os.path.exists(db_path_to_check):
        return True
    try:
        conn_check = sqlite3.connect(db_path_to_check)
        cur_check = conn_check.cursor()
        cur_check.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='users'")
        if not cur_check.fetchone():
            conn_check.close()
            return True
        cur_check.execute("SELECT COUNT(*) FROM users")
        if cur_check.fetchone()[0] == 0:
            conn_check.close()
            return True
        conn_check.close()
        return False
    except Exception as e:
        print(f"DB空チェックエラー: {e}")
        return True

def backup_database_to_github():
    if not GITHUB_TOKEN or not GITHUB_USERNAME or not GITHUB_REPOSITORY:
        print("GitHub認証情報(TOKEN, USERNAME, REPOSITORY)不足のためバックアップスキップ。")
        return
    if not os.path.exists(DATABASE_PATH) or is_db_empty(DATABASE_PATH):
        print(f"DBファイル {DATABASE_PATH} が存在しないか空のためバックアップスキップ。")
        return

    print("GitHubへのDBバックアップ開始...")
    ensure_db_directory_exists(DATABASE_PATH)
    timestamp_str = datetime.now(TIMEZONE).strftime('%Y-%m-%d_%H-%M-%S')
    backup_filename_on_github = f"backup_{timestamp_str}_{DATABASE_FILE}"

    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            repo_url = f"https://x-access-token:{GITHUB_TOKEN}@github.com/{GITHUB_USERNAME}/{GITHUB_REPOSITORY}.git"
            try:
                subprocess.run(["git", "clone", "--branch", "db-backup", "--single-branch", repo_url, tmpdir], check=True, capture_output=True, text=True)
                print(f"db-backupブランチを {tmpdir} にクローン。")
            except subprocess.CalledProcessError as e_clone_branch:
                error_output_clone = e_clone_branch.stderr.decode(errors='ignore') if e_clone_branch.stderr else (e_clone_branch.stdout.decode(errors='ignore') if e_clone_branch.stdout else "")
                if any(err_msg in error_output_clone.lower() for err_msg in ["couldn't find remote ref db-backup", "could not checkout", "does not exist"]):
                    print("db-backupブランチが見つかりません。リポジトリをクローンし新規作成/チェックアウト。")
                    subprocess.run(["git", "clone", repo_url, tmpdir], check=True, capture_output=True, text=True)
                    subprocess.run(["git", "checkout", "-B", "db-backup"], cwd=tmpdir, check=True)
                else:
                    print(f"db-backupブランチクローン中に予期せぬエラー: {error_output_clone}")
                    raise
            
            subprocess.run(['git', 'config', 'user.email', GIT_USER_EMAIL], cwd=tmpdir, check=True)
            subprocess.run(['git', 'config', 'user.name', GIT_USER_NAME], cwd=tmpdir, check=True)
            destination_in_tmpdir = os.path.join(tmpdir, backup_filename_on_github)
            shutil.copy2(DATABASE_PATH, destination_in_tmpdir)
            print(f"DBを {destination_in_tmpdir} にコピー。")

            gitignore_path = os.path.join(tmpdir, ".gitignore")
            with open(gitignore_path, "w") as f:
                f.write(f"{DATABASE_FILE}\n")
                f.write(f"*{DATABASE_FILE}\n")
                f.write(f"!backup_*{DATABASE_FILE}\n")
            subprocess.run(["git", "add", ".gitignore"], cwd=tmpdir, check=True)
            subprocess.run(["git", "add", backup_filename_on_github], cwd=tmpdir, check=True)
            
            status_result = subprocess.run(["git", "status", "--porcelain"], cwd=tmpdir, capture_output=True, text=True)
            if status_result.stdout:
                commit_message = f"Auto backup: {backup_filename_on_github}"
                subprocess.run(["git", "commit", "-m", commit_message], cwd=tmpdir, check=True)
                subprocess.run(["git", "push", "origin", "db-backup"], cwd=tmpdir, check=True)
                print(f"バックアップ {backup_filename_on_github} をGitHubにプッシュ。")
            else:
                print("コミット変更なし。プッシュスキップ。")
    except subprocess.CalledProcessError as e:
        error_output = e.stderr.decode(errors='ignore') if e.stderr else (e.stdout.decode(errors='ignore') if e.stdout else "No output")
        print(f"バックアップ中Gitエラー: {e.cmd} ({e.returncode})\nOutput:{error_output}")
    except FileNotFoundError as e:
        print(f"バックアップ中ファイル/コマンド未検出エラー: {e}")
    except Exception as e:
        print(f"バックアップ中予期せぬエラー: {str(e)}")

def restore_database_from_github():
    if not GITHUB_TOKEN or not GITHUB_USERNAME or not GITHUB_REPOSITORY:
        print("GitHub認証情報不足のためリストアスキップ。")
        return False
    print("GitHubからのDBリストア開始...")
    ensure_db_directory_exists(DATABASE_PATH)
    with tempfile.TemporaryDirectory() as tmpdir:
        try:
            repo_url = f"https://x-access-token:{GITHUB_TOKEN}@github.com/{GITHUB_USERNAME}/{GITHUB_REPOSITORY}.git"
            subprocess.run(["git", "clone", "--branch", "db-backup", "--single-branch", "--depth", "1", repo_url, tmpdir], check=True, capture_output=True, text=True)
            print(f"リストア用リポジトリ(db-backup)を {tmpdir} にクローン。")
            backup_files = [f for f in os.listdir(tmpdir) if f.startswith("backup_") and f.endswith(DATABASE_FILE)]
            if not backup_files:
                print(f"GitHubリポジトリに 'backup_*{DATABASE_FILE}' ファイル見つからず。")
                return False
            backup_files.sort(reverse=True)
            latest_backup_filename = backup_files[0]
            latest_backup_path_in_repo = os.path.join(tmpdir, latest_backup_filename)
            print(f"最新バックアップファイル: {latest_backup_filename}")
            if os.path.exists(DATABASE_PATH):
                print(f"既存DBファイル {DATABASE_PATH} を上書き。")
            shutil.copy2(latest_backup_path_in_repo, DATABASE_PATH)
            print(f"{latest_backup_filename} からDBを {DATABASE_PATH} にリストア。")
            return True
        except subprocess.CalledProcessError as e:
            error_output = e.stderr.decode(errors='ignore') if e.stderr else (e.stdout.decode(errors='ignore') if e.stdout else "No output")
            if any(err_msg in error_output.lower() for err_msg in ["couldn't find remote ref db-backup", "does not exist"]):
                print("db-backupブランチ見つからず。リストア不可。")
            else:
                print(f"リストア中Gitエラー: {e.cmd} ({e.returncode})\nOutput:{error_output}")
            return False
        except FileNotFoundError as e:
             print(f"リストア中ファイル/コマンド未検出エラー: {e}")
             return False
        except Exception as e:
            print(f"DBリストア中予期せぬエラー: {str(e)}")
            return False

# --- データベース接続・初期化 ---
def get_db_connection():
    if DATABASE_URL and DATABASE_URL.startswith("postgres"):
        try:
            conn_pg = psycopg2.connect(DATABASE_URL)
            # conn_pg.cursor_factory = DictCursor # 必要に応じて
            return conn_pg
        except psycopg2.OperationalError as e_pg:
            print(f"PostgreSQL接続エラー: {e_pg}。SQLiteにフォールバック。")
    
    ensure_db_directory_exists(DATABASE_PATH)
    conn_sqlite = sqlite3.connect(DATABASE_PATH)
    conn_sqlite.row_factory = sqlite3.Row
    conn_sqlite.execute("PRAGMA foreign_keys = ON")
    return conn_sqlite

def hash_password(password): # create_admin_user より前に定義
    return hashlib.sha256(password.encode('utf-8')).hexdigest()

def init_db():
    print("DB初期化 (テーブル作成・更新)...")
    with get_db_connection() as conn:
        cursor = conn.cursor()
        # Users
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT UNIQUE NOT NULL,
            password TEXT NOT NULL, is_admin BOOLEAN DEFAULT 0,
            is_private BOOLEAN DEFAULT 0, created_at DATETIME DEFAULT CURRENT_TIMESTAMP)''')
        # Records
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS records (
            id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, action TEXT NOT NULL,
            timestamp TEXT NOT NULL, memo TEXT, is_deleted BOOLEAN DEFAULT 0,
            likes_count INTEGER DEFAULT 0, is_private BOOLEAN DEFAULT 0,
            FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE)''')
        # Likes
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS likes (
            id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, record_id INTEGER NOT NULL,
            timestamp DATETIME NOT NULL, FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE,
            FOREIGN KEY(record_id) REFERENCES records(id) ON DELETE CASCADE, UNIQUE(user_id, record_id))''')
        
        table_columns_to_ensure = {
            'users': [('is_private', 'BOOLEAN DEFAULT 0')],
            'records': [('likes_count', 'INTEGER DEFAULT 0'), ('is_private', 'BOOLEAN DEFAULT 0')]
        }
        for table_name, columns in table_columns_to_ensure.items():
            cursor.execute(f"PRAGMA table_info({table_name})")
            existing_cols = [row['name'] for row in cursor.fetchall()]
            for col_name, col_def in columns:
                if col_name not in existing_cols:
                    try:
                        cursor.execute(f"ALTER TABLE {table_name} ADD COLUMN {col_name} {col_def}")
                        print(f"テーブル'{table_name}'にカラム'{col_name}'追加。")
                    except sqlite3.OperationalError as e_alt:
                        if 'duplicate column name' in str(e_alt).lower():
                            print(f"カラム'{col_name}'はテーブル'{table_name}'に既に存在。")
                        else: print(f"カラム'{col_name}'をテーブル'{table_name}'に追加中エラー: {e_alt}")
        conn.commit()
    print("DB初期化/スキーマ確認完了。")

def create_admin_user():
    print("管理者ユーザー確認・作成...")
    with get_db_connection() as conn:
        cursor = conn.cursor()
        admin_username, admin_password = 'admin', 'admin'
        cursor.execute('SELECT id FROM users WHERE username = ?', (admin_username,))
        if not cursor.fetchone():
            cursor.execute('INSERT INTO users (username, password, is_admin) VALUES (?, ?, ?)',
                           (admin_username, hash_password(admin_password), 1))
            conn.commit()
            print(f"管理者ユーザー'{admin_username}'作成。")
        else:
            print(f"管理者ユーザー'{admin_username}'は既に存在。")

def initialize_database_and_restore_if_needed():
    print(f"DBパス: {DATABASE_PATH}")
    perform_initial_setup = True
    if os.path.exists(DATABASE_PATH) and os.path.getsize(DATABASE_PATH) > 0 and not is_db_empty(DATABASE_PATH):
        print(f"既存DB {DATABASE_PATH} を使用。")
        perform_initial_setup = False
    else:
        if os.path.exists(DATABASE_PATH): print(f"DBファイル {DATABASE_PATH} は存在も空か不完全。")
        else: print(f"DBファイル {DATABASE_PATH} が存在せず。")
        print("GitHubからのリストア試行...")
        if restore_database_from_github():
            print("GitHubからのDBリストア成功。")
            perform_initial_setup = False
        else:
            print("GitHubからのリストア失敗またはバックアップ無。")
            
    if perform_initial_setup: print("新規DB作成。")
    init_db()
    create_admin_user()

with app.app_context():
    initialize_database_and_restore_if_needed()

def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session:
            flash('ログインが必要です。', 'error')
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function

def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session or not session.get('is_admin'):
            flash('管理者権限が必要です。', 'error')
            return redirect(url_for('index'))
        return f(*args, **kwargs)
    return decorated_function

def jst_now(): return datetime.now(TIMEZONE)

def evaluate_sleep(duration):
    r = duration / 7.0
    if r >= 1.5: return "寝すぎ⁉"
    if 1.286 <= r < 1.5: return "ちょい寝すぎか。"
    if 1.0 <= r < 1.286: return "良好だよ☻"
    if 0.858 <= r < 1.0: return "もう少し寝てほしいかも..."
    if 0.50 <= r < 0.858: return "頼むこれ以上は..."
    return "いい加減もっと寝ろよ！！"

def round_decimal(v, p=1): return Decimal(str(v)).quantize(Decimal('0.1')**p, rounding=ROUND_HALF_UP) if v is not None else None

def get_sleep_times_for_user(user_id):
    with get_db_connection() as conn:
        raw = conn.execute("SELECT action, timestamp FROM records WHERE user_id=? AND is_deleted=0 AND action IN ('sleep','wake_up') ORDER BY timestamp ASC", (user_id,)).fetchall()
    times, start = [], None
    for r in raw:
        t = datetime.fromisoformat(r['timestamp'])
        if r['action'] == 'sleep': start = t
        elif r['action'] == 'wake_up' and start:
            dur_s = (t - start).total_seconds()
            if dur_s > 0:
                dur_h = dur_s / 3600
                d_obj = t.date()
                times.append({'date':d_obj, 'duration':dur_h, 'hours':int(dur_h), 'minutes':int((dur_h-int(dur_h))*60),
                              'week':d_obj.isocalendar()[1], 'month':d_obj.month, 'year':d_obj.year, 'timestamp':t})
            start = None
    return times

def calc_avg(s_list, key_func=None): # 汎用平均計算
    if not s_list: return {'avg_hours':0,'avg_minutes':0,'evaluation':"-",'avg_duration':0.0,'record_days':0}
    durations = [item['duration'] for item in s_list] if key_func is None else [key_func(item) for item in s_list]
    days = len(durations)
    avg_d = sum(durations)/days if days > 0 else 0
    eval_str = evaluate_sleep(avg_d) if days>=3 else "-"
    return {'avg_hours':int(avg_d),'avg_minutes':int((avg_d-int(avg_d))*60),'evaluation':eval_str,'avg_duration':avg_d,'record_days':days}

def calculate_average_sleep(sleep_times): return calc_avg(sleep_times)

def group_by_period(sleep_times, period_key_func, period_name_func):
    if not sleep_times: return []
    grouped = {}
    for item in sleep_times:
        key = period_key_func(item)
        if key not in grouped: grouped[key] = []
        grouped[key].append(item['duration'])
    
    averages = []
    for key, durations in grouped.items():
        avg_data = calc_avg([{'duration': d} for d in durations]) # calc_avgに合わせる
        period_name, start_date_obj = period_name_func(key)
        averages.append({**avg_data, 'period': period_name, 'start_date': start_date_obj})
    averages.sort(key=lambda x: x['start_date'], reverse=True)
    return averages

def calculate_weekly_average_sleep(sleep_times):
    key_f = lambda item: f"{item['year']}-W{item['week']:02d}"
    name_f = lambda k: (f"{datetime.strptime(f'{k.split('-W')[0]}-{k.split('-W')[1]}-1', '%Y-%W-%w').strftime('%Y/%m/%d')}～{(datetime.strptime(f'{k.split('-W')[0]}-{k.split('-W')[1]}-1', '%Y-%W-%w') + timedelta(days=6)).strftime('%Y/%m/%d')}",
                        datetime.strptime(f'{k.split("-W")[0]}-{k.split("-W")[1]}-1', '%Y-%W-%w').date())
    return group_by_period(sleep_times, key_f, name_f)

def calculate_monthly_average_sleep(sleep_times):
    key_f = lambda item: f"{item['year']}-{item['month']:02d}"
    name_f = lambda k: (f"{k.split('-')[0]}年{int(k.split('-')[1])}月", datetime(int(k.split('-')[0]), int(k.split('-')[1]), 1).date())
    return group_by_period(sleep_times, key_f, name_f)

def calculate_sleep_comparisons(sleep_times):
    if not sleep_times or len(sleep_times) < 1: return {'yesterday':None,'last_week':None,'last_month':None}
    s_sorted = sorted(sleep_times, key=lambda x: x['date'], reverse=True)
    latest = s_sorted[0]; today = latest['date']
    def diff_obj(cur, prev):
        if prev is None: return None
        d = cur - prev; inc = d > 0; d_abs = abs(d)
        return {'diff_hours':int(d_abs),'diff_minutes':int((d_abs-int(d_abs))*60),'is_increase':inc}
    yest_s = s_sorted[1]['duration'] if len(s_sorted)>1 else None
    last_w_s = next((s['duration'] for s in s_sorted[1:] if (today-s['date']).days>=7 and today.weekday()==s['date'].weekday()),None)
    last_m_s = next((s['duration'] for s in s_sorted[1:] if s['date'].day==today.day and ((today.month==1 and s['date'].month==12 and s['date'].year==today.year-1) or (s['date'].month==today.month-1 and s['date'].year==today.year))),None)
    return {'yesterday':diff_obj(latest['duration'],yest_s), 'last_week':diff_obj(latest['duration'],last_w_s), 'last_month':diff_obj(latest['duration'],last_m_s)}

@app.route('/', methods=['GET']) # POSTは record_action に移譲したのでGETのみ
@login_required
def index():
    page = request.args.get('page', 1, type=int); per_page = 10; offset = (page - 1) * per_page
    user_msg = session.pop(f"user_{session['user_id']}_message", None)
    with get_db_connection() as conn:
        s_today_jst = TIMEZONE.localize(datetime.combine(jst_now().date(), datetime.min.time()))
        e_today_jst = s_today_jst + timedelta(days=1)
        s_utc, e_utc = s_today_jst.astimezone(pytz.utc).isoformat(), e_today_jst.astimezone(pytz.utc).isoformat()
        total = conn.execute("SELECT COUNT(*) FROM records WHERE user_id=? AND is_deleted=0 AND timestamp>=? AND timestamp<?", (session['user_id'],s_utc,e_utc)).fetchone()[0]
        raw = conn.execute("SELECT id,action,memo,likes_count,timestamp,is_private FROM records WHERE user_id=? AND is_deleted=0 AND timestamp>=? AND timestamp<? ORDER BY timestamp DESC LIMIT ? OFFSET ?", (session['user_id'],s_utc,e_utc,per_page,offset)).fetchall()
        recs = []
        for r_raw in raw:
            r = dict(r_raw); ts_utc = datetime.fromisoformat(r['timestamp']); ts_jst = ts_utc.astimezone(TIMEZONE)
            r['formatted_date'] = ts_jst.strftime('%Y-%m-%d'); r['formatted_time'] = ts_jst.strftime('%H:%M:%S')
            recs.append(r)
        total_pg = (total+per_page-1)//per_page
    return render_template("index.html",records=recs,is_private=session.get('is_private',False),user_message=user_msg,page=page,total_pages=total_pg)

@app.route('/like_record/<int:record_id>', methods=['POST'])
@login_required
def like_record(record_id):
    # ... (前回のコードから変更なし、ただしflashメッセージやエラーハンドリングは適宜調整)
    with get_db_connection() as conn:
        try:
            if conn.execute('SELECT id FROM likes WHERE user_id=? AND record_id=?',(session['user_id'],record_id)).fetchone():
                flash('すでにいいね済み。', 'info')
            else:
                conn.execute('BEGIN')
                conn.execute('INSERT INTO likes (user_id,record_id,timestamp) VALUES (?,?,?)',(session['user_id'],record_id,jst_now().isoformat()))
                conn.execute('UPDATE records SET likes_count=likes_count+1 WHERE id=?',(record_id,))
                conn.commit(); flash('いいねしました！', 'success'); backup_database_to_github()
        except Exception as e: conn.rollback(); flash(f'エラー: {e}', 'danger')
    # リダイレクト先の決定ロジック (request.referrer や hidden inputで元のページ情報を渡すなど)
    return redirect(request.referrer or url_for('index'))


@app.route('/calendar', methods=['GET'])
@login_required
def calendar_view():
    now = jst_now(); y = request.args.get('year',now.year,type=int); m = request.args.get('month',now.month,type=int)
    cal = calendar.Calendar(6).monthdayscalendar(y,m) # Sunday as first day
    prev_d = (datetime(y,m,1)-timedelta(days=1)); next_d = (datetime(y,m,1)+timedelta(days=calendar.monthrange(y,m)[1]))
    return render_template('calendar.html',year=y,month=m,cal_data=cal,prev_year=prev_d.year,prev_month=prev_d.month,next_year=next_d.year,next_month=next_d.month,today=now.date())

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        u, p = request.form.get('username','').strip(), request.form.get('password','')
        if not u or not p: flash('ユーザー名とパスワードを入力。','error'); return render_template('login.html')
        with get_db_connection() as conn: user = conn.execute('SELECT * FROM users WHERE username=?',(u,)).fetchone()
        if user and user['password']==hash_password(p):
            session.clear(); session.permanent=True; session['user_id']=user['id']; session['username']=user['username']
            session['is_admin']=bool(user['is_admin']); session['is_private']=bool(user['is_private'])
            flash('おかえりなさい！','success'); return redirect(url_for('admin_dashboard' if session['is_admin'] else 'index'))
        else: flash('ユーザー名またはパスワードが間違い。','error')
    return render_template('login.html')

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        u, p = request.form.get('username','').strip(), request.form.get('password','')
        is_priv = request.form.get('is_private')=='on'
        if not u or not p: flash('ユーザー名とパスワードを入力。','error'); return render_template('register.html')
        with get_db_connection() as conn:
            try:
                conn.execute('INSERT INTO users (username,password,is_private,created_at) VALUES (?,?,?,?)',(u,hash_password(p),int(is_priv),jst_now().isoformat()))
                conn.commit(); flash('登録成功！ログインしてください。','success'); backup_database_to_github(); return redirect(url_for('login'))
            except sqlite3.IntegrityError: conn.rollback(); flash('このユーザー名は既に使用されています。','error')
            except sqlite3.Error as e: conn.rollback(); flash(f'登録中DBエラー: {e}','error')
    return render_template('register.html')

@app.route('/sleep_graph')
@login_required
def sleep_graph(): return render_template('sleep_graph.html', period=request.args.get('period','daily'))

@app.route('/reset_password', methods=['GET', 'POST'])
def reset_password():
    if request.method == 'POST':
        u, new_p = request.form.get('username','').strip(), request.form.get('new_password','')
        if not u or not new_p: flash('ユーザー名と新パスワードを入力。','error'); return render_template('reset_password.html')
        with get_db_connection() as conn:
            if conn.execute('SELECT id FROM users WHERE username=?',(u,)).fetchone():
                conn.execute('UPDATE users SET password=? WHERE username=?',(hash_password(new_p),u)); conn.commit()
                flash('パスワード更新。ログインしてください。','success'); backup_database_to_github(); return redirect(url_for('login'))
            else: flash('指定ユーザー名が見つかりません。','error')
    return render_template('reset_password.html')

@app.route('/logout')
@login_required
def logout(): session.clear(); flash('ログアウトしました。','info'); return redirect(url_for('login'))

@app.route('/record', methods=['POST'])
@login_required
def record_action():
    act, mem, is_priv_rec = request.form.get('action'), request.form.get('memo',''), request.form.get('is_private_record')=='on'
    if not act or act not in ['wake_up','sleep']: flash('有効な行動を選択。','danger'); return redirect(url_for('index'))
    ts_save = jst_now().isoformat()
    with get_db_connection() as conn:
        try:
            s_today_jst = TIMEZONE.localize(datetime.combine(jst_now().date(), datetime.min.time()))
            e_today_jst = s_today_jst + timedelta(days=1)
            s_utc, e_utc = s_today_jst.astimezone(pytz.utc).isoformat(), e_today_jst.astimezone(pytz.utc).isoformat()
            count = conn.execute("SELECT COUNT(*) FROM records WHERE user_id=? AND action=? AND timestamp>=? AND timestamp<? AND is_deleted=0", (session['user_id'],act,s_utc,e_utc)).fetchone()[0]
            if (act=='sleep' and count>=2) or (act=='wake_up' and count>=1):
                flash(f'本日{count+1}回目の「{act}」記録不可。','warning'); return redirect(url_for('index'))
            conn.execute('INSERT INTO records (user_id,action,timestamp,memo,is_private) VALUES (?,?,?,?,?)',(session['user_id'],act,ts_save,mem,int(is_priv_rec)))
            conn.commit(); flash('記録保存成功。','success'); backup_database_to_github()
        except sqlite3.Error as e: conn.rollback(); flash(f'DBエラー: {e}','danger'); app.logger.error(f"Record DB error: {e}")
    return redirect(url_for('index'))

@app.route('/average_sleep')
@login_required
def average_sleep_view():
    period = request.args.get('period','daily'); user_id = session['user_id']
    s_times = get_sleep_times_for_user(user_id)
    if not s_times: return render_template('average_sleep.html',has_records=False,period=period,evaluate_sleep=evaluate_sleep,round_decimal=round_decimal)
    overall, weekly, monthly, comps = calc_avg(s_times), calculate_weekly_average_sleep(s_times), calculate_monthly_average_sleep(s_times), calculate_sleep_comparisons(s_times)
    s_times.sort(key=lambda x:x['date'],reverse=True)
    return render_template('average_sleep.html',has_records=True,sleep_times=s_times,overall_avg=overall,weekly_avg=weekly,monthly_avg=monthly,comparisons=comps,period=period,evaluate_sleep=evaluate_sleep,round_decimal=round_decimal)

@app.route('/day_records/<date>')
@login_required
def day_records(date):
    try: p_date = datetime.strptime(date,'%Y-%m-%d').date()
    except ValueError: flash('無効な日付形式。YYYY-MM-DDで。','error'); return redirect(url_for('calendar_view'))
    uid = session['user_id']; is_admin = session.get('is_admin',False)
    s_data = get_sleep_times_for_user(uid); s_info = next((s for s in s_data if s['date']==p_date),None)
    s_h,s_m,s_eval = (s_info['hours'] if s_info else None), (s_info['minutes'] if s_info else None), (evaluate_sleep(s_info['duration']) if s_info else None)
    with get_db_connection() as conn:
        s_day_jst = TIMEZONE.localize(datetime.combine(p_date,datetime.min.time())); e_day_jst = s_day_jst+timedelta(days=1)
        s_utc,e_utc = s_day_jst.astimezone(pytz.utc).isoformat(), e_day_jst.astimezone(pytz.utc).isoformat()
        q = "SELECT r.id,r.action,r.memo,r.is_deleted,r.likes_count,r.is_private,u.username,r.timestamp FROM records r JOIN users u ON r.user_id=u.id WHERE "
        params = [s_utc,e_utc]
        if is_admin: q += "r.timestamp>=? AND r.timestamp<? ORDER BY u.username ASC, r.timestamp ASC"
        else: q += "r.user_id=? AND r.timestamp>=? AND r.timestamp<? ORDER BY r.timestamp ASC"; params.insert(0,uid) # 最初にuidを追加
        raw_recs = conn.execute(q, tuple(params)).fetchall() # paramsをタプルに変換
        
        recs_list = []
        for r_raw in raw_recs:
            r_d = dict(r_raw); ts_utc = datetime.fromisoformat(r_d['timestamp']); ts_jst = ts_utc.astimezone(TIMEZONE)
            r_d['formatted_time'] = ts_jst.strftime('%H:%M:%S'); r_d['formatted_date'] = ts_jst.strftime('%Y-%m-%d')
            recs_list.append(r_d)
    return render_template('day_records.html',date=p_date.strftime('%Y-%m-%d'),records=recs_list,is_admin=is_admin,sleep_duration_hours=s_h,sleep_duration_minutes=s_m,sleep_evaluation=s_eval,year=p_date.year,month=p_date.month)

@app.route('/all_records')
@login_required
def all_records():
    page = request.args.get("page",1,type=int); per_page = 20; offset = (page-1)*per_page
    u_filter_id_str = request.args.get("user_id","all")
    with get_db_connection() as conn:
        avail_users = conn.execute("SELECT id,username FROM users WHERE is_admin=0 AND is_private=0 ORDER BY username").fetchall()
        q_base = "FROM records r JOIN users u ON r.user_id=u.id WHERE r.is_deleted=0 AND u.is_private=0 AND u.is_admin=0"
        params_count, params_data = [], []
        if u_filter_id_str!="all" and u_filter_id_str.isdigit():
            u_filter_id = int(u_filter_id_str)
            q_base += " AND r.user_id=?"; params_count.append(u_filter_id); params_data.append(u_filter_id)
        total = conn.execute(f"SELECT COUNT(r.id) {q_base}", tuple(params_count)).fetchone()[0]
        params_data.extend([per_page,offset])
        raw_data = conn.execute(f"SELECT r.id,r.action,r.memo,r.likes_count,r.timestamp,r.is_private as rec_priv,u.username,u.id as author_uid {q_base} ORDER BY r.timestamp DESC LIMIT ? OFFSET ?", tuple(params_data)).fetchall()
        liked_ids = {row['record_id'] for row in conn.execute("SELECT record_id FROM likes WHERE user_id=?",(session['user_id'],)).fetchall()}
        fmt_list = []
        for r_raw in raw_data:
            r_d = dict(r_raw); ts_utc = datetime.fromisoformat(r_d['timestamp']); ts_jst = ts_utc.astimezone(TIMEZONE)
            r_d['formatted_date']=ts_jst.strftime('%Y-%m-%d'); r_d['formatted_time']=ts_jst.strftime('%H:%M:%S')
            r_d['liked']=(r_d['id'] in liked_ids); fmt_list.append(r_d)
        total_pg = (total+per_page-1)//per_page
    return render_template("all_records.html",records=fmt_list,users=avail_users,current_user_filter=u_filter_id_str,page=page,total_pages=total_pg)

@app.route('/toggle_privacy', methods=['POST'])
@login_required
def toggle_privacy():
    new_priv = request.form.get('is_private')=='on'
    with get_db_connection() as conn:
        try:
            conn.execute('UPDATE users SET is_private=? WHERE id=?',(int(new_priv),session['user_id'])); conn.commit()
            session['is_private']=new_priv; flash(f"プライバシー設定を「{'プライベート' if new_priv else 'パブリック'}」に更新。", 'success')
            backup_database_to_github()
        except sqlite3.Error as e: flash(f'プライバシー設定更新中エラー: {e}','error')
    return redirect(url_for('index'))

@app.route('/delete_record/<int:record_id>', methods=['POST'])
@login_required
def delete_record(record_id):
    redir_to, date_redir, page_num, u_filter = request.form.get('redirect_to','index'), request.form.get('date_val'), request.form.get('page_num','1'), request.form.get('user_filter_val','all')
    with get_db_connection() as conn:
        owner = conn.execute('SELECT user_id FROM records WHERE id=? AND is_deleted=0',(record_id,)).fetchone()
        if not owner or (owner['user_id']!=session['user_id'] and not session.get('is_admin')): flash('記録が見つからないか削除権限なし。','error')
        else:
            try:
                conn.execute('BEGIN'); conn.execute('DELETE FROM likes WHERE record_id=?',(record_id,))
                conn.execute('UPDATE records SET is_deleted=1 WHERE id=?',(record_id,)); conn.commit()
                flash('記録削除成功。','success'); backup_database_to_github()
            except sqlite3.Error as e: conn.rollback(); flash(f'記録削除中エラー: {e}','error')
    if redir_to=='all_records': return redirect(url_for('all_records',page=page_num,user_id=u_filter))
    if redir_to=='day_records' and date_redir: return redirect(url_for('day_records',date=date_redir))
    return redirect(url_for('index',page=page_num))

@app.route('/admin_dashboard')
@admin_required
def admin_dashboard():
    page = request.args.get('page',1,type=int); per_page = 10; offset = (page-1)*per_page
    with get_db_connection() as conn:
        users = conn.execute("SELECT id,username,created_at,is_private FROM users WHERE is_admin=0 ORDER BY created_at DESC").fetchall()
        total_recs = conn.execute('SELECT COUNT(*) FROM records WHERE is_deleted=0').fetchone()[0]
        raw_recs = conn.execute("SELECT r.*,u.username,u.is_private as u_priv FROM records r JOIN users u ON r.user_id=u.id WHERE r.is_deleted=0 ORDER BY r.timestamp DESC LIMIT ? OFFSET ?",(per_page,offset)).fetchall()
        recs = []
        for r_raw in raw_recs:
            r_d = dict(r_raw); ts_utc = datetime.fromisoformat(r_d['timestamp']); ts_jst = ts_utc.astimezone(TIMEZONE)
            r_d['formatted_time'] = ts_jst.strftime('%Y-%m-%d %H:%M:%S'); recs.append(r_d)
        total_pg = (total_recs+per_page-1)//per_page
    return render_template("admin_dashboard.html",users=users,records=recs,page=page,total_pages=total_pg)

@app.route('/admin/user_records/<int:user_id>')
@admin_required
def admin_user_records(user_id):
    page = request.args.get('page',1,type=int); per_page = 20; offset = (page-1)*per_page
    with get_db_connection() as conn:
        user = conn.execute('SELECT id,username FROM users WHERE id=?',(user_id,)).fetchone()
        if not user: flash('ユーザーが見つかりません。','error'); return redirect(url_for('admin_dashboard'))
        total_u_recs = conn.execute('SELECT COUNT(*) FROM records WHERE user_id=? AND is_deleted=0',(user_id,)).fetchone()[0]
        raw_u_recs = conn.execute("SELECT * FROM records WHERE user_id=? AND is_deleted=0 ORDER BY timestamp DESC LIMIT ? OFFSET ?",(user_id,per_page,offset)).fetchall()
        u_recs = []
        for r_raw in raw_u_recs:
            r_d = dict(r_raw); ts_utc = datetime.fromisoformat(r_d['timestamp']); ts_jst = ts_utc.astimezone(TIMEZONE)
            r_d['formatted_time'] = ts_jst.strftime('%Y-%m-%d %H:%M:%S'); u_recs.append(r_d)
        total_pg = (total_u_recs+per_page-1)//per_page
    return render_template('admin_user_records.html',user=user,records=u_recs,page=page,total_pages=total_pg)

@app.route('/admin/add_record', methods=['GET', 'POST'])
@admin_required
def admin_add_record():
    if request.method == 'POST':
        uid,act,mem,ts_str,is_priv = request.form.get('user_id'),request.form.get('action'),request.form.get('memo',''),request.form.get('timestamp'),request.form.get('is_private_record')=='on'
        if not uid or not act or not ts_str: flash('ユーザー,アクション,タイムスタンプ必須。','danger')
        else:
            try:
                dt_naive = datetime.strptime(ts_str,'%Y-%m-%dT%H:%M'); dt_jst = TIMEZONE.localize(dt_naive)
                ts_save = dt_jst.isoformat()
                with get_db_connection() as conn:
                    conn.execute('INSERT INTO records (user_id,action,timestamp,memo,is_private) VALUES (?,?,?,?,?)',(uid,act,ts_save,mem,int(is_priv))); conn.commit()
                flash('記録追加成功。','success'); session[f'user_{uid}_message']="管理者が記録追加。"; backup_database_to_github()
                return redirect(url_for('admin_user_records',user_id=uid))
            except ValueError: flash('タイムスタンプ形式無効。YYYY-MM-DDTHH:MMで。','danger')
            except sqlite3.Error as e: flash(f'記録追加中DBエラー: {e}','danger')
    with get_db_connection() as conn: users = conn.execute('SELECT id,username FROM users WHERE is_admin=0 ORDER BY username').fetchall()
    return render_template('admin_add_record.html',users=users)

@app.route('/admin/delete_record/<int:record_id>', methods=['POST'])
@admin_required
def admin_delete_record(record_id):
    uid_rec = None
    with get_db_connection() as conn:
        try:
            rec_del = conn.execute('SELECT user_id FROM records WHERE id=?',(record_id,)).fetchone()
            if not rec_del: flash('記録見つからず。','error'); return redirect(url_for('admin_dashboard'))
            uid_rec = rec_del['user_id']
            conn.execute('BEGIN'); conn.execute('DELETE FROM likes WHERE record_id=?',(record_id,)); conn.execute('DELETE FROM records WHERE id=?',(record_id,)); conn.commit()
            flash('記録完全削除成功。','success'); backup_database_to_github()
        except sqlite3.Error as e: conn.rollback(); flash(f'記録削除中エラー: {e}','danger')
    if uid_rec: return redirect(url_for('admin_user_records',user_id=uid_rec))
    return redirect(url_for('admin_dashboard'))

@app.route('/delete_user/<int:user_id>', methods=['POST'])
@admin_required
def delete_user(user_id):
    if user_id == session.get('user_id'): flash('自分自身削除不可。','error'); return redirect(url_for('admin_dashboard'))
    with get_db_connection() as conn:
        u_del = conn.execute('SELECT username,is_admin FROM users WHERE id=?',(user_id,)).fetchone()
        if not u_del: flash('削除対象ユーザー見つからず。','error')
        elif u_del['is_admin']: flash('管理者アカウント削除不可。','error')
        else:
            try:
                conn.execute('DELETE FROM users WHERE id=?',(user_id,)); conn.commit() # ON DELETE CASCADEで関連も削除
                flash(f"ユーザー'{u_del['username']}'と関連データ削除。","success"); backup_database_to_github()
            except sqlite3.Error as e: conn.rollback(); flash(f"ユーザー削除中エラー: {e}","error")
    return redirect(url_for('admin_dashboard'))

@app.route('/api/sleep_data')
@login_required
def api_sleep_data():
    period = request.args.get('period','daily'); uid = session['user_id']; all_s = get_sleep_times_for_user(uid); data = []
    if period=='daily': data=[{'date':s['date'].isoformat(),'duration':s['duration']} for s in all_s]; data.sort(key=lambda x:x['date'])
    elif period=='weekly': weekly_avg=calculate_weekly_average_sleep(all_s); data=[{'period_start':w['start_date'].isoformat(),'avg_duration':w['avg_duration']} for w in weekly_avg]; data.sort(key=lambda x:x['period_start'])
    elif period=='monthly': monthly_avg=calculate_monthly_average_sleep(all_s); data=[{'period_start':m['start_date'].isoformat(),'avg_duration':m['avg_duration']} for m in monthly_avg]; data.sort(key=lambda x:x['period_start'])
    else: return jsonify({'error':'Invalid period'}), 400
    return jsonify(data)

@app.route('/download_db')
@admin_required
def download_db_file():
    ensure_db_directory_exists(DATABASE_PATH)
    if os.path.exists(DATABASE_PATH): return send_file(DATABASE_PATH,as_attachment=True,download_name=DATABASE_FILE)
    else: flash("DBファイル見つかりません。","error"); return redirect(url_for('admin_dashboard'))

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    debug_mode = os.environ.get('FLASK_DEBUG','0').lower() in ['1','true']
    app.run(host='0.0.0.0', port=port, debug=debug_mode)