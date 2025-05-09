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
# 環境変数からGitHub関連情報を取得
GITHUB_USERNAME = os.environ.get("GITHUB_USERNAME")
GITHUB_REPOSITORY = os.environ.get("GITHUB_REPOSITORY") # 例: "username/repository_name"
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN")
GIT_USER_EMAIL = os.environ.get("GIT_USER_EMAIL", "konosuke.hirata@gmail.com") # フォールバック値
GIT_USER_NAME = os.environ.get("GIT_USER_NAME", "yukirin88") # フォールバック値

DATABASE_URL = os.environ.get('DATABASE_URL')
# DATABASE_URL = None  # 強制的にSQLite を使う場合 (開発時など)
RENDER_DATA_DIR = os.environ.get('RENDER_DATA_DIR', os.path.dirname(os.path.abspath(__file__)))
DATABASE_FILE = 'attendance.db'
DATABASE_PATH = os.path.join(RENDER_DATA_DIR, DATABASE_FILE)
TIMEZONE = pytz.timezone('Asia/Tokyo')

app = Flask(__name__, template_folder='templates')
Bootstrap(app)
CORS(app)
app.secret_key = os.environ.get('SECRET_KEY', 'your-default-secret-key') # 必ず環境変数で設定推奨
app.config.update(
    SESSION_COOKIE_SECURE=True,
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_REFRESH_EACH_REQUEST=True,
    PERMANENT_SESSION_LIFETIME=timedelta(hours=2)
)

# --- データベースディレクトリ関連 ---
def ensure_db_directory_exists(db_path_to_check=DATABASE_PATH): # 引数名は変更
    db_dir = os.path.dirname(db_path_to_check)
    if db_dir and not os.path.exists(db_dir):
        try:
            os.makedirs(db_dir)
            print(f"ディレクトリ {db_dir} を作成しました。")
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
            return True
        cur_check.execute("SELECT COUNT(*) FROM users")
        if cur_check.fetchone()[0] == 0: # ユーザーテーブルが空でも「空」とみなすか要件次第
            return True # 今回はユーザーがいたら空ではないとする
        conn_check.close()
        return False # usersテーブルにデータがあれば空ではない
    except Exception as e:
        print(f"DB空チェックエラー: {e}")
        return True # エラー時は安全側に倒して「空」とみなす（リストア試行のため）

def backup_database_to_github():
    """GitHubにDBをバックアップする関数 (改善版)"""
    if not GITHUB_TOKEN or not GITHUB_USERNAME or not GITHUB_REPOSITORY:
        print("GitHubの認証情報(TOKEN, USERNAME, REPOSITORY)が不足しているため、バックアップをスキップします。")
        return
    if not os.path.exists(DATABASE_PATH) or is_db_empty(DATABASE_PATH): # 実際のファイル存在もチェック
        print(f"データベースファイル {DATABASE_PATH} が存在しないか空のため、バックアップをスキップします。")
        return

    print("GitHubへのデータベースバックアップを開始します...")
    ensure_db_directory_exists(DATABASE_PATH)

    timestamp_str = datetime.now(TIMEZONE).strftime('%Y-%m-%d_%H-%M-%S')
    backup_filename_on_github = f"backup_{timestamp_str}_{DATABASE_FILE}"

    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            repo_url = f"https://x-access-token:{GITHUB_TOKEN}@github.com/{GITHUB_USERNAME}/{GITHUB_REPOSITORY}.git"
            
            # db-backupブランチが存在しない場合も考慮してクローン
            try:
                subprocess.run(["git", "clone", "--branch", "db-backup", "--single-branch", repo_url, tmpdir], check=True, capture_output=True, text=True)
                print(f"db-backupブランチを {tmpdir} にクローンしました。")
            except subprocess.CalledProcessError as e_clone_branch:
                if "couldn't find remote ref db-backup" in e_clone_branch.stderr.lower() or \
                   "could not checkout" in e_clone_branch.stderr.lower(): # GitHub Actionsなど環境によってエラーメッセージが異なる
                    print("db-backupブランチが見つかりませんでした。リポジトリをクローンして新しいブランチを作成します。")
                    subprocess.run(["git", "clone", repo_url, tmpdir], check=True, capture_output=True, text=True)
                    subprocess.run(["git", "checkout", "-b", "db-backup"], cwd=tmpdir, check=True)
                    print(f"リポジトリをクローンし、新しいdb-backupブランチを作成してチェックアウトしました: {tmpdir}")
                else:
                    print(f"db-backupブランチのクローン中に予期せぬエラー: {e_clone_branch.stderr}")
                    raise
            
            # Gitユーザー設定
            subprocess.run(['git', 'config', 'user.email', GIT_USER_EMAIL], cwd=tmpdir, check=True)
            subprocess.run(['git', 'config', 'user.name', GIT_USER_NAME], cwd=tmpdir, check=True)

            # バックアップファイルをtmpdirにコピー
            destination_in_tmpdir = os.path.join(tmpdir, backup_filename_on_github)
            shutil.copy2(DATABASE_PATH, destination_in_tmpdir)
            print(f"データベースを {destination_in_tmpdir} にコピーしました。")

            # .gitignoreの作成/追記（attendance.db自体は含めず、backup_*のみを対象とする）
            gitignore_path = os.path.join(tmpdir, ".gitignore")
            with open(gitignore_path, "a+") as f: # 追記モードで開き、存在しなければ作成
                f.seek(0) # ファイルの先頭から読み込む
                content = f.read()
                if f"{DATABASE_FILE}\n" not in content: # attendance.db を無視
                    f.write(f"{DATABASE_FILE}\n")
                if f"*{DATABASE_FILE}\n" not in content: # *.db のようなパターンも考慮
                     f.write(f"*{DATABASE_FILE}\n")
                if f"!backup_*{DATABASE_FILE}\n" not in content: # ただし backup_ で始まるものは対象
                     f.write(f"!backup_*{DATABASE_FILE}\n")
            subprocess.run(["git", "add", ".gitignore"], cwd=tmpdir, check=True)


            subprocess.run(["git", "add", backup_filename_on_github], cwd=tmpdir, check=True)
            
            status_result = subprocess.run(["git", "status", "--porcelain"], cwd=tmpdir, capture_output=True, text=True)
            if status_result.stdout: # 何か変更がある場合のみコミット
                commit_message = f"Auto backup: {backup_filename_on_github}"
                subprocess.run(["git", "commit", "-m", commit_message], cwd=tmpdir, check=True)
                
                # pull はコンフリクトの可能性があるので、バックアップのpushでは通常不要か、戦略による
                # subprocess.run(["git", "pull", "origin", "db-backup", "--rebase"], cwd=tmpdir, check=False) 
                subprocess.run(["git", "push", "origin", "db-backup"], cwd=tmpdir, check=True)
                print(f"バックアップ {backup_filename_on_github} をGitHubにプッシュしました。")
            else:
                print("コミットする変更がありませんでした。プッシュはスキップします。")

    except subprocess.CalledProcessError as e:
        error_output = e.stderr.decode(errors='ignore') if e.stderr else (e.stdout.decode(errors='ignore') if e.stdout else "No output")
        print(f"バックアップ処理中のGitコマンドエラー: {e.cmd} returned {e.returncode}\nOutput:\n{error_output}")
    except FileNotFoundError as e: # gitコマンドが見つからない場合など
        print(f"バックアップ処理中のファイル/コマンド未検出エラー: {e}")
    except Exception as e:
        print(f"バックアップ処理中に予期せぬエラーが発生しました: {str(e)}")


def restore_database_from_github():
    """GitHubから最新のデータベースバックアップをリストアする"""
    if not GITHUB_TOKEN or not GITHUB_USERNAME or not GITHUB_REPOSITORY:
        print("GitHubの認証情報が不足しているため、リストアをスキップします。")
        return False

    print("GitHubからのデータベースリストアを開始します...")
    ensure_db_directory_exists(DATABASE_PATH)
    
    with tempfile.TemporaryDirectory() as tmpdir:
        try:
            repo_url = f"https://x-access-token:{GITHUB_TOKEN}@github.com/{GITHUB_USERNAME}/{GITHUB_REPOSITORY}.git"
            subprocess.run(["git", "clone", "--branch", "db-backup", "--single-branch", repo_url, tmpdir], check=True, capture_output=True, text=True)
            print(f"リストア用リポジトリを {tmpdir} にクローンしました。")

            backup_files = [f for f in os.listdir(tmpdir) if f.startswith("backup_") and f.endswith(DATABASE_FILE)]
            if not backup_files:
                print(f"GitHubリポジトリ ({GITHUB_USERNAME}/{GITHUB_REPOSITORY}/db-backup) にバックアップファイルが見つかりません。")
                return False

            backup_files.sort(reverse=True) # 名前順で最新のものが先頭に来る想定 (YYYY-MM-DD_HH-MM-SS形式なので)
            latest_backup_filename = backup_files[0]
            latest_backup_path_in_repo = os.path.join(tmpdir, latest_backup_filename)
            print(f"最新のバックアップファイル: {latest_backup_filename}")

            if os.path.exists(DATABASE_PATH):
                print(f"既存のデータベースファイル {DATABASE_PATH} を上書きします。")
            
            shutil.copy2(latest_backup_path_in_repo, DATABASE_PATH)
            print(f"{latest_backup_filename} からデータベースを {DATABASE_PATH} にリストアしました。")
            return True

        except subprocess.CalledProcessError as e:
            error_output = e.stderr.decode(errors='ignore') if e.stderr else (e.stdout.decode(errors='ignore') if e.stdout else "No output")
            if "couldn't find remote ref db-backup" in error_output.lower():
                print("db-backupブランチが見つかりません。リストアできません。")
            else:
                print(f"リストア処理中のGitコマンドエラー: {e.cmd} returned {e.returncode}\nOutput:\n{error_output}")
            return False
        except FileNotFoundError as e: # gitコマンドが見つからない場合など
             print(f"リストア処理中のファイル/コマンド未検出エラー: {e}")
             return False
        except Exception as e:
            print(f"データベースのリストア中に予期しないエラーが発生しました: {str(e)}")
            return False

# --- データベース接続・初期化 ---
def get_db_connection():
    """環境に応じたデータベース接続を返す"""
    if DATABASE_URL and DATABASE_URL.startswith("postgres"): # PostgreSQLを使う場合
        try:
            conn_pg = psycopg2.connect(DATABASE_URL)
            # conn_pg.cursor_factory = DictCursor # DictCursorは fetchone()['column_name'] の形式
            return conn_pg
        except psycopg2.OperationalError as e_pg:
            print(f"PostgreSQL接続エラー: {e_pg}。SQLiteにフォールバックします。")
            # pass # SQLiteにフォールバック
    
    # SQLite接続 (デフォルトまたはフォールバック)
    ensure_db_directory_exists(DATABASE_PATH) # ここで呼び出す
    conn_sqlite = sqlite3.connect(DATABASE_PATH)
    conn_sqlite.row_factory = sqlite3.Row # これで user['column_name'] のようにアクセス可能
    conn_sqlite.execute("PRAGMA foreign_keys = ON")
    return conn_sqlite

def init_db():
    """データベース構造を初期化"""
    print("データベースを初期化します (テーブル作成)...")
    with get_db_connection() as conn:
        cursor = conn.cursor()
        # Usersテーブル
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password TEXT NOT NULL,
            is_admin BOOLEAN DEFAULT 0,
            is_private BOOLEAN DEFAULT 0,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
        ''')
        # Recordsテーブル
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS records (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            action TEXT NOT NULL,
            timestamp TEXT NOT NULL,
            memo TEXT,
            is_deleted BOOLEAN DEFAULT 0,
            likes_count INTEGER DEFAULT 0,
            is_private BOOLEAN DEFAULT 0,
            FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
        )
        ''')
        # Likesテーブル
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS likes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            record_id INTEGER NOT NULL,
            timestamp DATETIME NOT NULL,
            FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE,
            FOREIGN KEY(record_id) REFERENCES records(id) ON DELETE CASCADE,
            UNIQUE(user_id, record_id)
        )
        ''')
        
        # 既存テーブルへのカラム追加 (冪等性を持たせる)
        table_columns = {
            'users': [('is_private', 'BOOLEAN DEFAULT 0')],
            'records': [('likes_count', 'INTEGER DEFAULT 0'), ('is_private', 'BOOLEAN DEFAULT 0')]
        }
        for table_name, columns_to_add in table_columns.items():
            cursor.execute(f"PRAGMA table_info({table_name})")
            existing_columns = [row['name'] for row in cursor.fetchall()]
            for col_name, col_definition in columns_to_add:
                if col_name not in existing_columns:
                    try:
                        cursor.execute(f"ALTER TABLE {table_name} ADD COLUMN {col_name} {col_definition}")
                        print(f"テーブル '{table_name}' にカラム '{col_name}' を追加しました。")
                    except sqlite3.OperationalError as e_alter:
                        # ALTER TABLE ADD COLUMN は SQLite 3.25.0 以降で IF NOT EXISTS をサポート
                        # それ以前のバージョンでは duplicate column name エラーになる可能性がある
                        if 'duplicate column name' in str(e_alter).lower():
                            print(f"カラム '{col_name}' はテーブル '{table_name}' に既に存在します。")
                        else:
                            raise # それ以外のエラーは再送出
        conn.commit()
    print("データベースの初期化完了。")

def create_admin_user():
    """管理者ユーザー (admin/admin) を作成 (存在しない場合)"""
    print("管理者ユーザーの存在を確認・作成します...")
    with get_db_connection() as conn:
        cursor = conn.cursor()
        admin_username = 'admin'
        admin_password = 'admin' # 本番環境ではより強固なパスワードと設定方法を推奨
        cursor.execute('SELECT id FROM users WHERE username = ?', (admin_username,))
        if not cursor.fetchone():
            cursor.execute(
                'INSERT INTO users (username, password, is_admin) VALUES (?, ?, ?)',
                (admin_username, hash_password(admin_password), 1)
            )
            conn.commit()
            print(f"管理者ユーザー '{admin_username}' を作成しました。")
        else:
            print(f"管理者ユーザー '{admin_username}' は既に存在します。")

def initialize_database_and_restore_if_needed():
    """アプリ起動時にDB初期化処理（リストア試行後に新規作成）"""
    print(f"データベースパス: {DATABASE_PATH}")
    if os.path.exists(DATABASE_PATH) and os.path.getsize(DATABASE_PATH) > 0 and not is_db_empty(DATABASE_PATH): # ファイルが存在し、空でなく、中身もある
        print(f"既存のデータベースファイル {DATABASE_PATH} を使用します。")
        # ここでスキーママイグレーションが必要な場合は実行する (init_dbは冪等性があるので呼んでも良い)
        init_db() # 既存DBに対してもスキーマ変更を適用できるように
        create_admin_user() # 管理者も確認
    else:
        if os.path.exists(DATABASE_PATH):
            print(f"データベースファイル {DATABASE_PATH} は存在しますが、空または不完全です。")
        else:
            print(f"データベースファイル {DATABASE_PATH} が存在しません。")
        
        print("GitHubからのリストアを試みます...")
        if restore_database_from_github():
            print("GitHubからのデータベースリストアに成功しました。")
            # リストア後もスキーマ確認・管理者確認
            init_db() 
            create_admin_user()
        else:
            print("GitHubからのリストアに失敗、またはバックアップが存在しませんでした。新しいデータベースを作成します。")
            init_db()
            create_admin_user()

# --- アプリケーションコンテキスト初期化 ---
with app.app_context():
    initialize_database_and_restore_if_needed()

# --- ヘルパー関数・デコレーター ---
def hash_password(password):
    return hashlib.sha256(password.encode('utf-8')).hexdigest()

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
            return redirect(url_for('login')) # 管理者でなければ適切なページへ
        return f(*args, **kwargs)
    return decorated_function

def jst_now():
    return datetime.now(TIMEZONE)

# 睡眠評価関数 (内容はユーザー提供のものと同じ)
def evaluate_sleep(sleep_duration):
    optimal_sleep = 7.0
    sleep_ratio = sleep_duration / optimal_sleep
    if sleep_ratio >= 1.5: return "寝すぎ⁉"
    elif 1.286 <= sleep_ratio < 1.5: return "ちょい寝すぎか。"
    elif 1.0 <= sleep_ratio < 1.286: return "良好だよ☻"
    elif 0.858 <= sleep_ratio < 1.0: return "もう少し寝てほしいかも..."
    elif 0.50 <= sleep_ratio < 0.858: return "頼むこれ以上は..."
    else: return "いい加減もっと寝ろよ！！"

def round_decimal(value, places=1): # テンプレートで使われる可能性のある関数
    if value is None: return None
    return Decimal(str(value)).quantize(Decimal('0.1') ** places, rounding=ROUND_HALF_UP)

# 睡眠時間計算関連 (ユーザー提供のものをベースに)
def get_sleep_times_for_user(user_id):
    """指定ユーザーの睡眠記録を取得・計算してリストで返す"""
    with get_db_connection() as conn:
        records_raw = conn.execute('''
            SELECT action, timestamp FROM records
            WHERE user_id = ? AND is_deleted = 0 AND (action = 'sleep' OR action = 'wake_up')
            ORDER BY timestamp ASC
        ''', (user_id,)).fetchall()

    calculated_sleep_times = []
    sleep_start_time = None
    for row in records_raw:
        current_time = datetime.fromisoformat(row['timestamp'])
        if row['action'] == 'sleep':
            sleep_start_time = current_time
        elif row['action'] == 'wake_up' and sleep_start_time:
            sleep_duration_seconds = (current_time - sleep_start_time).total_seconds()
            if sleep_duration_seconds > 0: # マイナスや0は無効
                sleep_duration_hours = sleep_duration_seconds / 3600
                sleep_date_obj = current_time.date() # 起床日で記録
                calculated_sleep_times.append({
                    'date': sleep_date_obj,
                    'duration': sleep_duration_hours,
                    'hours': int(sleep_duration_hours),
                    'minutes': int((sleep_duration_hours - int(sleep_duration_hours)) * 60),
                    'week': sleep_date_obj.isocalendar()[1],
                    'month': sleep_date_obj.month,
                    'year': sleep_date_obj.year,
                    'timestamp': current_time # wake_up のタイムスタンプ
                })
            sleep_start_time = None # ペア成立でリセット
    return calculated_sleep_times

def calculate_average_sleep(sleep_times_list):
    if not sleep_times_list:
        return {'avg_hours': 0, 'avg_minutes': 0, 'evaluation': "-", 'avg_duration': 0.0}
    total_duration = sum(item['duration'] for item in sleep_times_list)
    avg_duration = total_duration / len(sleep_times_list)
    avg_hours = int(avg_duration)
    avg_minutes = int((avg_duration - avg_hours) * 60)
    evaluation = evaluate_sleep(avg_duration) if len(sleep_times_list) >= 3 else "-" # 3日以上の記録で評価
    return {
        'avg_hours': avg_hours, 
        'avg_minutes': avg_minutes, 
        'evaluation': evaluation,
        'avg_duration': avg_duration
    }

def calculate_weekly_average_sleep(sleep_times_list):
    if not sleep_times_list: return []
    weeks_data = {}
    for item in sleep_times_list:
        week_key = f"{item['year']}-W{item['week']:02d}"
        if week_key not in weeks_data: weeks_data[week_key] = []
        weeks_data[week_key].append(item['duration'])
    
    weekly_averages = []
    for week_key, durations in weeks_data.items():
        avg_duration = sum(durations) / len(durations)
        avg_hours = int(avg_duration)
        avg_minutes = int((avg_duration - avg_hours) * 60)
        year_str, week_str = week_key.split('-W')
        # 週の開始日と終了日を計算
        # datetime.strptime は %W (月曜始まりの週番号) と %Y (年) から日付を生成
        # %w (曜日、0が日曜) と合わせて週の初めの日 (月曜) を求める
        week_start_date = datetime.strptime(f'{year_str}-{week_str}-1', '%Y-%W-%w').date() # 週の月曜日
        week_end_date = week_start_date + timedelta(days=6) # 週の日曜日
        weekly_averages.append({
            'period': f"{week_start_date.strftime('%Y/%m/%d')}～{week_end_date.strftime('%Y/%m/%d')}",
            'avg_hours': avg_hours, 'avg_minutes': avg_minutes, 'avg_duration': avg_duration,
            'evaluation': evaluate_sleep(avg_duration),
            'start_date': week_start_date, # ソート用
            'record_days': len(durations)
        })
    weekly_averages.sort(key=lambda x: x['start_date'], reverse=True)
    return weekly_averages

def calculate_monthly_average_sleep(sleep_times_list):
    if not sleep_times_list: return []
    months_data = {}
    for item in sleep_times_list:
        month_key = f"{item['year']}-{item['month']:02d}"
        if month_key not in months_data: months_data[month_key] = []
        months_data[month_key].append(item['duration'])

    monthly_averages = []
    for month_key, durations in months_data.items():
        avg_duration = sum(durations) / len(durations)
        avg_hours = int(avg_duration)
        avg_minutes = int((avg_duration - avg_hours) * 60)
        year_val, month_val = map(int, month_key.split('-'))
        month_start_date = datetime(year_val, month_val, 1).date()
        monthly_averages.append({
            'period': f"{year_val}年{month_val}月",
            'avg_hours': avg_hours, 'avg_minutes': avg_minutes, 'avg_duration': avg_duration,
            'evaluation': evaluate_sleep(avg_duration),
            'start_date': month_start_date, # ソート用
            'record_days': len(durations)
        })
    monthly_averages.sort(key=lambda x: x['start_date'], reverse=True)
    return monthly_averages

def calculate_sleep_comparisons(sleep_times_list):
    if not sleep_times_list or len(sleep_times_list) < 1:
        return {'yesterday': None, 'last_week': None, 'last_month': None}

    # 日付でソート (最新が先頭)
    sorted_sleep_times = sorted(sleep_times_list, key=lambda x: x['date'], reverse=True)
    
    latest_sleep = sorted_sleep_times[0]
    today_date = latest_sleep['date']
    
    def get_diff_obj(current_duration, compare_duration):
        if compare_duration is None: return None
        diff_val = current_duration - compare_duration
        is_increase_val = diff_val > 0
        diff_abs_val = abs(diff_val)
        return {
            'diff_hours': int(diff_abs_val),
            'diff_minutes': int((diff_abs_val - int(diff_abs_val)) * 60),
            'is_increase': is_increase_val
        }

    # 前日比
    yesterday_sleep = sorted_sleep_times[1]['duration'] if len(sorted_sleep_times) > 1 else None
    yesterday_comp = get_diff_obj(latest_sleep['duration'], yesterday_sleep)

    # 先週比 (同じ曜日)
    last_week_sleep_duration = None
    for past_sleep in sorted_sleep_times[1:]: # 最新以外と比較
        if (today_date - past_sleep['date']).days >= 7 and today_date.weekday() == past_sleep['date'].weekday():
            last_week_sleep_duration = past_sleep['duration']
            break
    last_week_comp = get_diff_obj(latest_sleep['duration'], last_week_sleep_duration)
    
    # 先月比 (同じ日)
    last_month_sleep_duration = None
    for past_sleep in sorted_sleep_times[1:]:
        if past_sleep['date'].day == today_date.day: # 日が同じ
            # 月が1ヶ月前かチェック
            if today_date.month == 1 and past_sleep['date'].month == 12 and past_sleep['date'].year == today_date.year -1:
                last_month_sleep_duration = past_sleep['duration']
                break
            elif past_sleep['date'].month == today_date.month - 1 and past_sleep['date'].year == today_date.year:
                last_month_sleep_duration = past_sleep['duration']
                break
    last_month_comp = get_diff_obj(latest_sleep['duration'], last_month_sleep_duration)

    return {
        'yesterday': yesterday_comp,
        'last_week': last_week_comp,
        'last_month': last_month_comp
    }

# --- ルート定義 ---
@app.route('/', methods=['GET', 'POST']) # index関数は1つに統合
@login_required
def index():
    page = request.args.get('page', 1, type=int)
    per_page = 10 # 1ページあたりの表示件数
    offset = (page - 1) * per_page

    user_message_key = f"user_{session['user_id']}_message" # 管理者からのメッセージ用
    user_message = session.pop(user_message_key, None)

    with get_db_connection() as conn:
        # 今日の日付の記録を取得 (JST基準)
        # SQLのDATE関数はUTCなので、JSTで今日の日付を計算して渡す
        today_jst_str = jst_now().strftime('%Y-%m-%d')
        
        # 総レコード数を取得 (今日の記録のみ)
        total_records_today = conn.execute('''
            SELECT COUNT(*) FROM records
            WHERE user_id = ? AND is_deleted = 0 AND date(timestamp) = ? 
        ''', (session['user_id'], today_jst_str)).fetchone()[0] # timestamp は ISO8601 UTC で保存されている前提

        # ページネーション付きで今日の記録を取得
        records_today = conn.execute('''
            SELECT id, action, memo, likes_count,
                   strftime('%Y-%m-%d', timestamp) as formatted_date, 
                   strftime('%H:%M:%S', timestamp) as formatted_time 
            FROM records
            WHERE user_id = ? AND is_deleted = 0 AND date(timestamp) = ?
            ORDER BY timestamp DESC
            LIMIT ? OFFSET ?
        ''', (session['user_id'], today_jst_str, per_page, offset)).fetchall()
        
        total_pages_today = (total_records_today + per_page - 1) // per_page

    return render_template("index.html",
                           records=records_today,
                           is_private=session.get('is_private', False),
                           user_message=user_message,
                           page=page,
                           total_pages=total_pages_today)

@app.route('/like_record/<int:record_id>', methods=['POST'])
@login_required
def like_record(record_id):
    from_page = request.form.get('from_page', 'index') # formから取得
    page_num = request.form.get('page_num', '1')
    user_filter_val = request.form.get('user_filter_val', 'all')

    with get_db_connection() as conn:
        try:
            # 既にいいねしているか確認
            existing_like = conn.execute(
                'SELECT id FROM likes WHERE user_id = ? AND record_id = ?',
                (session['user_id'], record_id)
            ).fetchone()

            if existing_like:
                flash('すでにいいね済みです。', 'info')
            else:
                conn.execute('BEGIN')
                conn.execute(
                    'INSERT INTO likes (user_id, record_id, timestamp) VALUES (?, ?, ?)',
                    (session['user_id'], record_id, jst_now().isoformat()) # JSTで保存
                )
                conn.execute(
                    'UPDATE records SET likes_count = likes_count + 1 WHERE id = ?',
                    (record_id,)
                )
                conn.commit()
                flash('いいねしました！', 'success')
                backup_database_to_github() # いいね後にもバックアップ
        except sqlite3.IntegrityError: # UNIQUE制約違反など
            conn.rollback()
            flash('すでにいいね済みか、データベースエラーが発生しました。', 'warning')
        except sqlite3.Error as e:
            conn.rollback()
            flash(f'データベースエラーが発生しました: {e}', 'error')
            app.logger.error(f"Like record DB error: {e}")
        except Exception as e_global:
            conn.rollback()
            flash(f'予期せぬエラーが発生しました: {e_global}', 'error')
            app.logger.error(f"Like record unexpected error: {e_global}")
            
    # 元のページにリダイレクト
    if from_page == 'all_records':
        return redirect(url_for('all_records', page=page_num, user_id=user_filter_val))
    elif from_page == 'day_records':
        # day_records には日付が必要なので、record_idから日付を取得するか、フォームで渡す
        date_val = request.form.get('date_val') # フォームで日付を渡すことを想定
        if date_val:
            return redirect(url_for('day_records', date=date_val))
    return redirect(url_for('index', page=page_num))


@app.route('/calendar', methods=['GET'])
@login_required
def calendar_view():
    now_jst = jst_now()
    year = request.args.get('year', now_jst.year, type=int)
    month = request.args.get('month', now_jst.month, type=int)
    
    cal_obj = calendar.monthcalendar(year, month)
    
    prev_month_date = datetime(year, month, 1) - timedelta(days=1)
    next_month_date = datetime(year, month, calendar.monthrange(year, month)[1]) + timedelta(days=1)
    
    return render_template(
        'calendar.html',
        year=year, month=month, cal=cal_obj,
        prev_year=prev_month_date.year, prev_month=prev_month_date.month,
        next_year=next_month_date.year, next_month=next_month_date.month,
        today=now_jst.date()
    )

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '')
        if not username or not password:
            flash('ユーザー名とパスワードを入力してください。', 'error')
            return render_template('login.html')

        with get_db_connection() as conn:
            user = conn.execute('SELECT * FROM users WHERE username = ?', (username,)).fetchone()
        
        if user and user['password'] == hash_password(password):
            session.clear()
            session.permanent = True # PERMANENT_SESSION_LIFETIME が適用される
            session['user_id'] = user['id']
            session['username'] = user['username']
            session['is_admin'] = bool(user['is_admin'])
            session['is_private'] = bool(user['is_private'])
            flash('おかえりなさい！', 'success')
            return redirect(url_for('admin_dashboard' if session['is_admin'] else 'index'))
        else:
            flash('ユーザー名またはパスワードが間違っています。', 'error')
    return render_template('login.html')

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '')
        is_private = request.form.get('is_private') == 'on'
        if not username or not password:
            flash('ユーザー名とパスワードを入力してください。', 'error')
            return render_template('register.html')

        with get_db_connection() as conn:
            try:
                conn.execute(
                    'INSERT INTO users (username, password, is_private) VALUES (?, ?, ?)',
                    (username, hash_password(password), int(is_private))
                )
                conn.commit()
                flash('登録しました！ログインしてください。', 'success')
                backup_database_to_github() # 登録後バックアップ
                return redirect(url_for('login'))
            except sqlite3.IntegrityError: # UNIQUE制約違反 (username)
                conn.rollback()
                flash('このユーザー名は既に使用されています。', 'error')
            except sqlite3.Error as e:
                conn.rollback()
                flash(f'登録中にデータベースエラーが発生しました: {e}', 'error')
                app.logger.error(f"Register DB error: {e}")
    return render_template('register.html')

@app.route('/sleep_graph')
@login_required
def sleep_graph():
    period = request.args.get('period', 'daily')
    return render_template('sleep_graph.html', period=period)

@app.route('/reset_password', methods=['GET', 'POST'])
def reset_password():
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        new_password = request.form.get('new_password', '')
        if not username or not new_password:
            flash('ユーザー名と新しいパスワードを入力してください。', 'error')
            return render_template('reset_password.html')
        
        with get_db_connection() as conn:
            user_exists = conn.execute('SELECT id FROM users WHERE username = ?', (username,)).fetchone()
            if user_exists:
                conn.execute('UPDATE users SET password = ? WHERE username = ?', (hash_password(new_password), username))
                conn.commit()
                flash('パスワードが更新されました。ログインしてください。', 'success')
                backup_database_to_github() # パスワード変更後バックアップ
                return redirect(url_for('login'))
            else:
                flash('指定されたユーザー名が見つかりませんでした。', 'error')
    return render_template('reset_password.html')

@app.route('/logout')
@login_required
def logout():
    session.clear()
    flash('ログアウトしました。', 'info')
    return redirect(url_for('login'))

@app.route('/record', methods=['POST'])
@login_required
def record_action(): # 関数名を変更 (record だと request オブジェクトと被る可能性)
    action = request.form.get('action')
    memo = request.form.get('memo', '')
    if not action or action not in ['wake_up', 'sleep']:
        flash('有効な行動を選択してください。', 'danger')
        return redirect(url_for('index'))

    timestamp_jst = jst_now()
    timestamp_iso = timestamp_jst.isoformat() # JSTのままISOフォーマットで保存

    with get_db_connection() as conn:
        try:
            # 同じ日の同じアクションの記録回数制限
            today_str_for_query = timestamp_jst.strftime('%Y-%m-%d') # JSTでの今日の日付
            existing_count = conn.execute('''
                SELECT COUNT(*) FROM records
                WHERE user_id = ? AND action = ? AND date(timestamp) = ? AND is_deleted = 0
            ''', (session['user_id'], action, today_str_for_query)).fetchone()[0]

            if (action == 'sleep' and existing_count >= 2) or \
               (action == 'wake_up' and existing_count >= 1):
                flash(f'本日は{existing_count+1}回目の{action}記録はできません。', 'warning')
                return redirect(url_for('index'))

            conn.execute(
                'INSERT INTO records (user_id, action, timestamp, memo) VALUES (?, ?, ?, ?)',
                (session['user_id'], action, timestamp_iso, memo)
            )
            conn.commit()
            flash('記録が正常に保存されました。', 'success')
            backup_database_to_github() # 記録後バックアップ
        except sqlite3.Error as e:
            conn.rollback()
            flash(f'データベースエラーが発生しました: {e}', 'danger')
            app.logger.error(f"Record action DB error: {e}")
    return redirect(url_for('index'))


@app.route('/average_sleep')
@login_required
def average_sleep_view(): # 関数名変更
    period = request.args.get('period', 'daily') # daily, weekly, monthly
    user_sleep_times = get_sleep_times_for_user(session['user_id'])

    if not user_sleep_times:
        return render_template('average_sleep.html', has_records=False, period=period,
                               evaluate_sleep=evaluate_sleep, round_decimal=round_decimal,
                               daily_avg=None, weekly_avg=[], monthly_avg=[], overall_avg=None, comparisons=None)

    overall_avg_data = calculate_average_sleep(user_sleep_times)
    weekly_avg_data = calculate_weekly_average_sleep(user_sleep_times)
    monthly_avg_data = calculate_monthly_average_sleep(user_sleep_times)
    comparisons_data = calculate_sleep_comparisons(user_sleep_times)
    
    # 表示用に日付降順でソート
    user_sleep_times.sort(key=lambda x: x['date'], reverse=True)

    return render_template('average_sleep.html',
                           has_records=True,
                           sleep_times=user_sleep_times,
                           overall_avg=overall_avg_data,
                           weekly_avg=weekly_avg_data,
                           monthly_avg=monthly_avg_data,
                           comparisons=comparisons_data,
                           period=period,
                           evaluate_sleep=evaluate_sleep, # テンプレートで使えるように渡す
                           round_decimal=round_decimal)   # 同上


@app.route('/day_records/<date>') # <date> パラメータを追加
@login_required
def day_records(date):
    try:
        parsed_date = datetime.strptime(date, '%Y-%m-%d').date()
    except ValueError:
        flash('無効な日付形式です。YYYY-MM-DD形式で指定してください。', 'error')
        return redirect(url_for('calendar_view'))

    user_id_to_query = session['user_id']
    
    # 管理者の場合は全ユーザーの記録を見れるようにするか、の考慮は省略 (現在はログインユーザーのみ)
    day_sleep_times = get_sleep_times_for_user(user_id_to_query)
    
    # 特定の日付の睡眠記録をフィルタリング
    sleep_info_for_day = None
    for st in day_sleep_times:
        if st['date'] == parsed_date:
            sleep_info_for_day = st # 該当日の最初の睡眠セグメントを採用（複数ある場合）
            break
            
    sleep_hours = sleep_info_for_day['hours'] if sleep_info_for_day else None
    sleep_minutes = sleep_info_for_day['minutes'] if sleep_info_for_day else None
    sleep_evaluation = evaluate_sleep(sleep_info_for_day['duration']) if sleep_info_for_day else None
    
    with get_db_connection() as conn:
        # その日の全アクション記録を取得
        records_for_day = conn.execute('''
            SELECT r.id, r.action, r.memo, r.is_deleted, r.likes_count, u.username,
                   strftime('%Y-%m-%d %H:%M:%S', r.timestamp) as formatted_time 
            FROM records r JOIN users u ON r.user_id = u.id
            WHERE r.user_id = ? AND date(r.timestamp) = ? 
            ORDER BY r.timestamp ASC 
        ''', (user_id_to_query, date)).fetchall() if not session.get('is_admin') else \
        conn.execute('''
            SELECT r.id, r.action, r.memo, r.is_deleted, r.likes_count, u.username,
                   strftime('%Y-%m-%d %H:%M:%S', r.timestamp) as formatted_time 
            FROM records r JOIN users u ON r.user_id = u.id
            WHERE date(r.timestamp) = ? 
            ORDER BY r.timestamp ASC
        ''', (date,)).fetchall()


    return render_template('day_records.html',
                           date=parsed_date.strftime('%Y-%m-%d'),
                           records=records_for_day,
                           is_admin=session.get('is_admin'),
                           sleep_duration_hours=sleep_hours,
                           sleep_duration_minutes=sleep_minutes,
                           sleep_evaluation=sleep_evaluation,
                           year=parsed_date.year, # カレンダーに戻るため
                           month=parsed_date.month)


@app.route('/all_records')
@login_required
def all_records():
    page = request.args.get("page", 1, type=int)
    per_page = 20
    offset = (page - 1) * per_page
    user_filter_id = request.args.get("user_id", "all") # フィルターするユーザーID

    with get_db_connection() as conn:
        # ユーザーリストを取得（非公開ユーザーとadminは除く）
        available_users = conn.execute(
            "SELECT id, username FROM users WHERE is_admin = 0 AND is_private = 0 ORDER BY username"
        ).fetchall()
        
        base_query = """
            FROM records r JOIN users u ON r.user_id = u.id
            WHERE r.is_deleted = 0 AND u.is_private = 0 AND u.is_admin = 0
        """
        query_params = []

        if user_filter_id != "all" and user_filter_id.isdigit():
            base_query += " AND r.user_id = ?"
            query_params.append(int(user_filter_id))

        total_records = conn.execute(f"SELECT COUNT(r.id) {base_query}", query_params).fetchone()[0]
        
        records_data = conn.execute(f"""
            SELECT r.id, r.action, r.memo, r.likes_count, u.username, u.id as author_user_id,
                   strftime('%Y-%m-%d', r.timestamp) as formatted_date,
                   strftime('%H:%M:%S', r.timestamp) as formatted_time
            {base_query}
            ORDER BY r.timestamp DESC LIMIT ? OFFSET ?
        """, query_params + [per_page, offset]).fetchall()
        
        # 現在のユーザーがいいねした記録IDのリストを取得
        liked_record_ids = {
            row['record_id'] for row in conn.execute(
                "SELECT record_id FROM likes WHERE user_id = ?", (session['user_id'],)
            ).fetchall()
        }

        formatted_records_list = [dict(row, liked=(row['id'] in liked_record_ids)) for row in records_data]
        total_pages = (total_records + per_page - 1) // per_page

    return render_template("all_records.html",
                           records=formatted_records_list,
                           users=available_users,
                           current_user_filter=user_filter_id,
                           page=page,
                           total_pages=total_pages)

@app.route('/toggle_privacy', methods=['POST'])
@login_required
def toggle_privacy():
    new_is_private_status = request.form.get('is_private') == 'on'
    with get_db_connection() as conn:
        try:
            conn.execute('UPDATE users SET is_private = ? WHERE id = ?', (int(new_is_private_status), session['user_id']))
            conn.commit()
            session['is_private'] = new_is_private_status # セッション情報も更新
            flash(f"プライバシー設定を「{'プライベート' if new_is_private_status else 'パブリック'}」に更新しました。", 'success')
            backup_database_to_github() # 設定変更後バックアップ
        except sqlite3.Error as e:
            flash(f'プライバシー設定更新中にエラー: {e}', 'error')
    return redirect(url_for('index'))


@app.route('/delete_record/<int:record_id>', methods=['POST'])
@login_required
def delete_record(record_id):
    redirect_to_page = request.form.get('redirect_to', 'day_records') # どのページに戻るか
    date_for_redirect = request.form.get('date_val') # day_recordsに戻る場合の日付
    
    with get_db_connection() as conn:
        # 削除権限の確認 (自分の記録か、または管理者か)
        record_owner = conn.execute('SELECT user_id FROM records WHERE id = ?', (record_id,)).fetchone()
        if not record_owner or (record_owner['user_id'] != session['user_id'] and not session.get('is_admin')):
            flash('記録が見つからないか、削除権限がありません。', 'error')
        else:
            try:
                # いいねも削除（参照整合性のため）
                conn.execute('DELETE FROM likes WHERE record_id = ?', (record_id,))
                # 記録を論理削除
                conn.execute('UPDATE records SET is_deleted = 1 WHERE id = ?', (record_id,))
                conn.commit()
                flash('記録を削除しました。', 'success')
                backup_database_to_github() # 削除後バックアップ
            except sqlite3.Error as e:
                conn.rollback()
                flash(f'記録削除中にエラー: {e}', 'error')

    if redirect_to_page == 'index':
        return redirect(url_for('index'))
    elif redirect_to_page == 'all_records':
        page = request.form.get('page_num', 1)
        user_filter = request.form.get('user_filter_val', 'all')
        return redirect(url_for('all_records', page=page, user_id=user_filter))
    elif redirect_to_page == 'day_records' and date_for_redirect:
        return redirect(url_for('day_records', date=date_for_redirect))
    # デフォルトはindex
    return redirect(url_for('index'))


# --- 管理者向けルート ---
@app.route('/admin_dashboard')
@admin_required
def admin_dashboard():
    page = request.args.get('page', 1, type=int)
    per_page = 20
    offset = (page - 1) * per_page
    with get_db_connection() as conn:
        users = conn.execute("SELECT id, username, created_at, is_private FROM users WHERE is_admin = 0 ORDER BY created_at DESC").fetchall()
        total_db_records = conn.execute('SELECT COUNT(*) FROM records').fetchone()[0] # 全記録数
        records = conn.execute('''
            SELECT r.*, u.username, u.is_private,
                   strftime('%Y-%m-%d %H:%M:%S', r.timestamp) as formatted_time
            FROM records r JOIN users u ON r.user_id = u.id
            ORDER BY r.timestamp DESC LIMIT ? OFFSET ?
        ''', (per_page, offset)).fetchall()
        total_pages = (total_db_records + per_page - 1) // per_page
    return render_template("admin_dashboard.html", users=users, records=records, page=page, total_pages=total_pages)

@app.route('/admin/user_records/<int:user_id>') # <int:user_id> パラメータ
@admin_required
def admin_user_records(user_id):
    page = request.args.get('page', 1, type=int)
    per_page = 20
    offset = (page - 1) * per_page
    with get_db_connection() as conn:
        user = conn.execute('SELECT id, username FROM users WHERE id = ?', (user_id,)).fetchone()
        if not user:
            flash('ユーザーが見つかりません。', 'error')
            return redirect(url_for('admin_dashboard'))
        
        total_user_records = conn.execute('SELECT COUNT(*) FROM records WHERE user_id = ?', (user_id,)).fetchone()[0] # is_deleted も考慮するなら追加
        user_records = conn.execute('''
            SELECT id, action, memo, likes_count, is_deleted,
                   strftime('%Y-%m-%d %H:%M:%S', timestamp) as formatted_time
            FROM records WHERE user_id = ? ORDER BY timestamp DESC LIMIT ? OFFSET ?
        ''', (user_id, per_page, offset)).fetchall()
        total_pages = (total_user_records + per_page - 1) // per_page
    return render_template('admin_user_records.html', user=user, records=user_records, page=page, total_pages=total_pages)


@app.route('/admin/add_record', methods=['GET', 'POST'])
@admin_required
def admin_add_record():
    if request.method == 'POST':
        user_id_form = request.form.get('user_id')
        action_form = request.form.get('action')
        memo_form = request.form.get('memo', '')
        timestamp_str_form = request.form.get('timestamp') # YYYY-MM-DDTHH:MM 形式を想定

        if not user_id_form or not action_form or not timestamp_str_form:
            flash('ユーザー、アクション、タイムスタンプは必須です。', 'danger')
        else:
            try:
                # フォームからのタイムスタンプをdatetimeオブジェクトに変換し、JSTとして解釈
                dt_naive = datetime.strptime(timestamp_str_form, '%Y-%m-%dT%H:%M')
                dt_aware_jst = TIMEZONE.localize(dt_naive)
                timestamp_to_save = dt_aware_jst.isoformat() # ISO形式で保存

                with get_db_connection() as conn:
                    conn.execute(
                        'INSERT INTO records (user_id, action, timestamp, memo) VALUES (?, ?, ?, ?)',
                        (user_id_form, action_form, timestamp_to_save, memo_form)
                    )
                    conn.commit()
                flash('記録が追加されました。', 'success')
                # 該当ユーザーに通知メッセージを設定 (オプション)
                session[f'user_{user_id_form}_message'] = "管理者があなたの記録を追加しました。"
                backup_database_to_github()
                return redirect(url_for('admin_user_records', user_id=user_id_form))
            except ValueError:
                flash('タイムスタンプの形式が無効です。YYYY-MM-DDTHH:MM形式で入力してください。', 'danger')
            except sqlite3.Error as e:
                flash(f'記録追加中にデータベースエラー: {e}', 'danger')
    
    # GETリクエスト時の処理 (フォーム表示)
    with get_db_connection() as conn:
        users_list = conn.execute('SELECT id, username FROM users WHERE is_admin = 0 ORDER BY username').fetchall()
    return render_template('admin_add_record.html', users=users_list)


@app.route('/admin/delete_record/<int:record_id>', methods=['POST']) # <int:record_id> パラメータ
@admin_required
def admin_delete_record(record_id):
    user_id_of_record = None
    with get_db_connection() as conn:
        try:
            # 記録が存在するか、またどのユーザーの記録か確認
            record_to_delete = conn.execute('SELECT user_id FROM records WHERE id = ?', (record_id,)).fetchone()
            if not record_to_delete:
                flash('記録が見つかりません。', 'error')
                return redirect(url_for('admin_dashboard'))
            
            user_id_of_record = record_to_delete['user_id']
            # 関連するいいねも削除
            conn.execute('DELETE FROM likes WHERE record_id = ?', (record_id,))
            # 記録を物理削除 (または論理削除 is_deleted=1)
            conn.execute('DELETE FROM records WHERE id = ?', (record_id,)) # ここでは物理削除
            # conn.execute('UPDATE records SET is_deleted = 1 WHERE id = ?', (record_id,)) # 論理削除の場合
            conn.commit()
            flash('記録が削除されました。', 'success')
            backup_database_to_github()
        except sqlite3.Error as e:
            conn.rollback()
            flash(f'記録削除中にエラー: {e}', 'danger')
    
    if user_id_of_record:
        return redirect(url_for('admin_user_records', user_id=user_id_of_record))
    return redirect(url_for('admin_dashboard'))

@app.route('/delete_user/<int:user_id>', methods=['POST']) # <int:user_id> パラメータ
@admin_required
def delete_user(user_id):
    if user_id == session.get('user_id'): # 自分自身は削除不可
        flash('自分自身を削除することはできません。', 'error')
        return redirect(url_for('admin_dashboard'))
    
    with get_db_connection() as conn:
        user_to_delete = conn.execute('SELECT username, is_admin FROM users WHERE id = ?', (user_id,)).fetchone()
        if not user_to_delete:
            flash('削除対象のユーザーが見つかりません。', 'error')
        elif user_to_delete['is_admin']: # 管理者アカウントは削除不可
            flash('管理者アカウントは削除できません。', 'error')
        else:
            try:
                conn.execute('BEGIN')
                # 関連レコード削除 (ON DELETE CASCADEが設定されていれば不要な場合もあるが明示的に行う)
                conn.execute('DELETE FROM likes WHERE user_id = ?', (user_id,))
                conn.execute('DELETE FROM likes WHERE record_id IN (SELECT id FROM records WHERE user_id = ?)', (user_id,))
                conn.execute('DELETE FROM records WHERE user_id = ?', (user_id,))
                conn.execute('DELETE FROM users WHERE id = ?', (user_id,))
                conn.commit()
                flash(f"ユーザー '{user_to_delete['username']}' と関連データを削除しました。", 'success')
                backup_database_to_github()
            except sqlite3.Error as e:
                conn.rollback()
                flash(f"ユーザー削除中にエラー: {e}", 'error')
    return redirect(url_for('admin_dashboard'))

# --- APIエンドポイント ---
@app.route('/api/sleep_data')
@login_required
def api_sleep_data():
    period = request.args.get('period', 'daily')
    user_id = session['user_id']
    all_sleeps = get_sleep_times_for_user(user_id)

    if period == 'daily':
        # 日付と睡眠時間のペアを返す
        data_to_return = [{'date': s['date'].isoformat(), 'duration': s['duration']} for s in all_sleeps]
        data_to_return.sort(key=lambda x: x['date']) # チャート用に日付昇順
    elif period == 'weekly':
        weekly_avg_list = calculate_weekly_average_sleep(all_sleeps)
        # 週の開始日と平均睡眠時間のペア
        data_to_return = [{'period_start': w['start_date'].isoformat(), 'avg_duration': w['avg_duration']} for w in weekly_avg_list]
        data_to_return.sort(key=lambda x: x['period_start'])
    elif period == 'monthly':
        monthly_avg_list = calculate_monthly_average_sleep(all_sleeps)
        # 月の開始日と平均睡眠時間のペア
        data_to_return = [{'period_start': m['start_date'].isoformat(), 'avg_duration': m['avg_duration']} for m in monthly_avg_list]
        data_to_return.sort(key=lambda x: x['period_start'])
    else:
        return jsonify({'error': 'Invalid period specified'}), 400
        
    return jsonify(data_to_return)

# --- DBダウンロード (開発用) ---
@app.route('/download_db')
@admin_required # 管理者のみアクセス可能にする
def download_db_file():
    ensure_db_directory_exists(DATABASE_PATH)
    if os.path.exists(DATABASE_PATH):
        return send_file(DATABASE_PATH, as_attachment=True, download_name=DATABASE_FILE)
    else:
        flash("データベースファイルが見つかりません。", "error")
        return redirect(url_for('admin_dashboard'))

# --- アプリ実行 ---
if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000)) # PORTはRender等で自動設定される
    # debug=True は開発時のみ。本番環境ではWSGIサーバー (Gunicornなど) を使用
    app.run(host='0.0.0.0', port=port, debug=os.environ.get('FLASK_DEBUG', '0') == '1')