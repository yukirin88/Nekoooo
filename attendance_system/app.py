# app.pyの先頭に追加
from decimal import Decimal, ROUND_HALF_UP

def round_decimal(value, precision=0):
    """Decimalクラスを使用して正確な四捨五入を行う"""
    quantize_value = Decimal(f"1.{'0' * precision}")
    return Decimal(str(value)).quantize(quantize_value, rounding=ROUND_HALF_UP)
import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from flask import Flask, render_template, request, redirect, url_for, session, flash
from flask_bootstrap import Bootstrap
from flask import jsonify
from flask_cors import CORS
from datetime import datetime, timedelta
from functools import wraps
import sqlite3
import hashlib
import calendar
import pytz
import os
import psycopg2
from psycopg2.extras import DictCursor

# 環境変数からデータベースURLを取得
DATABASE_URL = os.environ.get('DATABASE_URL')

# DATABASE_URL = os.environ.get('DATABASE_URL')  ← 一時的にコメントアウト
DATABASE_URL = None  # ← SQLite を使わせる


# アプリケーションの初期化
app = Flask(__name__, template_folder='templates')
Bootstrap(app)
CORS(app)

# セッション設定
app.secret_key = os.environ.get('SECRET_KEY', 'your-secret-key-here')
RENDER_DATA_DIR = os.environ.get('RENDER_DATA_DIR', os.path.dirname(os.path.abspath(__file__)))
DATABASE_PATH = os.path.join(RENDER_DATA_DIR, 'attendance.db')

app.config.update(
    SESSION_COOKIE_SECURE=True,
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_REFRESH_EACH_REQUEST=True,
    PERMANENT_SESSION_LIFETIME=timedelta(hours=2)
)

# ユーティリティ関数
def hash_password(password):
    return hashlib.sha256(password.encode('utf-8')).hexdigest()

def ensure_db_directory_exists():
    db_dir = os.path.dirname(DATABASE_PATH)
    if not os.path.exists(db_dir):
        os.makedirs(db_dir)

# データベース接続
def get_db_connection():
    """環境に応じたデータベース接続を返す"""
    if DATABASE_URL:
        try:
            # PostgreSQL接続を試みる
            conn = psycopg2.connect(DATABASE_URL)
            conn.cursor_factory = DictCursor
            return conn
        except psycopg2.OperationalError:
            # 接続エラーの場合はSQLiteにフォールバック
            print("PostgreSQL connection failed, falling back to SQLite")
    
    # SQLite接続
    ensure_db_directory_exists()
    conn = sqlite3.connect(DATABASE_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn

# データベース初期化
def init_db():
    """Initialize database with proper table structure"""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        try:
            # Create users table
            cursor.execute('''
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY,
                username TEXT UNIQUE NOT NULL,
                password TEXT NOT NULL,
                is_admin BOOLEAN DEFAULT 0,
                is_private BOOLEAN DEFAULT 0,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
            ''')
            # Create records table
            cursor.execute('''
            CREATE TABLE IF NOT EXISTS records (
                id INTEGER PRIMARY KEY,
                user_id INTEGER NOT NULL,
                action TEXT NOT NULL,
                timestamp TEXT NOT NULL,
                memo TEXT,
                is_deleted BOOLEAN DEFAULT 0,
                likes_count INTEGER DEFAULT 0,
                is_private BOOLEAN DEFAULT 0,
                FOREIGN KEY(user_id) REFERENCES users(id)
            )
            ''')
            # Create likes table
            cursor.execute('''
            CREATE TABLE IF NOT EXISTS likes (
                id INTEGER PRIMARY KEY,
                user_id INTEGER NOT NULL,
                record_id INTEGER NOT NULL,
                timestamp DATETIME NOT NULL,
                FOREIGN KEY(user_id) REFERENCES users(id),
                FOREIGN KEY(record_id) REFERENCES records(id)
            )
            ''')
            # Add missing columns if needed
            columns = [
                ('users', 'is_private', 'INTEGER DEFAULT 0'),
                ('records', 'likes_count', 'INTEGER DEFAULT 0')
            ]

            for table, column, definition in columns:
                try:
                    cursor.execute(f'ALTER TABLE {table} ADD COLUMN {column} {definition}')
                except sqlite3.OperationalError as e:
                    if 'duplicate column name' not in str(e):
                        raise
            conn.commit()
        except sqlite3.Error as e:
            print(f"Database initialization error: {e}")
            conn.rollback()

# 管理者ユーザー作成
def create_admin_user():
    """Create admin user if not exists"""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        try:
            admin = cursor.execute('SELECT * FROM users WHERE username = ?', ('admin',)).fetchone()
            if not admin:
                cursor.execute(
                    'INSERT INTO users (username, password, is_admin) VALUES (?, ?, ?)',
                    ('admin', hash_password('admin'), 1)
                )
            conn.commit()
        except sqlite3.Error as e:
            print(f"Admin user creation error: {e}")
            conn.rollback()

# Initialize app context
with app.app_context():
    init_db()
    create_admin_user()

# デコレーター
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
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function

def jst_now():
    return datetime.now(pytz.timezone('Asia/Tokyo'))

# 睡眠評価関数
def evaluate_sleep(sleep_duration):
    """睡眠時間の評価を行う"""
    optimal_sleep = 7.0  # 適正睡眠時間
    sleep_ratio = sleep_duration / optimal_sleep
    
    if sleep_ratio >= 1.5:
        return "寝すぎ⁉"
    elif 1.286 <= sleep_ratio < 1.5:
        return "ちょい寝すぎか。"
    elif 1.0 <= sleep_ratio < 1.286:
        return "良好だよ☻"
    elif 0.858 <= sleep_ratio < 1.0:
        return "もう少し寝てほしいかも..."
    elif 0.50 <= sleep_ratio < 0.858:
        return "頼むこれ以上は..."
    else:
        return "いい加減もっと寝ろよ！！"

# 睡眠時間計算関数
def calculate_average(sleep_times):
    """全期間の平均睡眠時間を計算"""
    if not sleep_times:
        return {'avg_hours': 0, 'avg_minutes': 0, 'evaluation': None}
    
    total_sleep = sum(item['duration'] for item in sleep_times)
    avg_sleep = total_sleep / len(sleep_times)
    avg_hours = int(avg_sleep)
    avg_minutes = int((avg_sleep - avg_hours) * 60)
    
    # 循環インポートを避けるため、直接evaluate_sleep関数を使用
    evaluation = evaluate_sleep(avg_sleep)
    
    return {
        'avg_hours': avg_hours,
        'avg_minutes': avg_minutes,
        'avg_duration': avg_sleep,
        'evaluation': evaluation if len(sleep_times) >= 3 else None
    }

def calculate_weekly_average(sleep_times):
    if not sleep_times:
        return []  # ← ここが大事！
    # 以下略...

    # 週ごとにグループ化
    weeks = {}
    for item in sleep_times:
        week_key = f"{item['year']}-W{item['week']:02d}"
        if week_key not in weeks:
            weeks[week_key] = []
        weeks[week_key].append(item)

    # 各週の平均を計算
    weekly_avgs = []
    for week_key, items in weeks.items():
        total_sleep = sum(item['duration'] for item in items)
        avg_sleep = total_sleep / len(items)
        avg_hours = int(avg_sleep)
        avg_minutes = int((avg_sleep - avg_hours) * 60)

        year, week = week_key.split('-W')
        start_date = datetime.strptime(f'{year}-{week}-1', '%Y-%W-%w').date()
        end_date = start_date + timedelta(days=6)

        weekly_avgs.append({
            'period': f"{start_date.strftime('%Y/%m/%d')}～{end_date.strftime('%Y/%m/%d')}",
            'avg_hours': avg_hours,
            'avg_minutes': avg_minutes,
            'avg_duration': avg_sleep,
            'evaluation': evaluate_sleep(avg_sleep),
            'start_date': start_date,
            'week_key': week_key,
            'record_days': len(items)  # ✅ 追加ポイント
        })

    weekly_avgs.sort(key=lambda x: x['start_date'], reverse=True)
    return weekly_avgs

def calculate_monthly_average(sleep_times):
    if not sleep_times:
        return []  # ← 同様にここも！
    # 以下略...

    # 月ごとにグループ化
    months = {}
    for item in sleep_times:
        month_key = f"{item['year']}-{item['month']:02d}"
        if month_key not in months:
            months[month_key] = []
        months[month_key].append(item)

    # 各月の平均を計算
    monthly_avgs = []
    for month_key, items in months.items():
        total_sleep = sum(item['duration'] for item in items)
        avg_sleep = total_sleep / len(items)
        avg_hours = int(avg_sleep)
        avg_minutes = int((avg_sleep - avg_hours) * 60)

        year, month = map(int, month_key.split('-'))
        start_date = datetime(year, month, 1).date()

        monthly_avgs.append({
            'period': f"{year}年{month}月",
            'avg_hours': avg_hours,
            'avg_minutes': avg_minutes,
            'avg_duration': avg_sleep,
            'evaluation': evaluate_sleep(avg_sleep),
            'start_date': start_date,
            'record_days': len(items)  # ✅ 追加
        })

    monthly_avgs.sort(key=lambda x: x['start_date'], reverse=True)
    return monthly_avgs

def calculate_comparisons(sleep_times):
    """前日比、先週比、先月比を計算"""
    if not sleep_times:
        return {
            'yesterday': {'diff_hours': 0, 'diff_minutes': 0, 'is_increase': False},
            'last_week': {'diff_hours': 0, 'diff_minutes': 0, 'is_increase': False},
            'last_month': {'diff_hours': 0, 'diff_minutes': 0, 'is_increase': False}
        }
    
    # 日付でソート
    sorted_times = sorted(sleep_times, key=lambda x: x['date'], reverse=True)
    
    # 現在の日付を取得（sorted_times[0]['date']がすでにdatetime.dateオブジェクト）
    today = sorted_times[0]['date']
    
    # 前日比
    yesterday_diff = calculate_diff(sorted_times, 0, 1)
    
    # 先週比（同じ曜日）
    last_week_idx = next((i for i, item in enumerate(sorted_times) if (today - item['date']).days >= 7 and today.weekday() == item['date'].weekday()), None)
    last_week_diff = calculate_diff(sorted_times, 0, last_week_idx) if last_week_idx else {'diff_hours': 0, 'diff_minutes': 0, 'is_increase': False}
    
    # 先月比（同じ日）
    last_month_day = today.day
    last_month = today.month - 1 if today.month > 1 else 12
    last_month_year = today.year if today.month > 1 else today.year - 1
    last_month_idx = next((i for i, item in enumerate(sorted_times) if item['date'].year == last_month_year and item['date'].month == last_month and item['date'].day == last_month_day), None)
    last_month_diff = calculate_diff(sorted_times, 0, last_month_idx) if last_month_idx else {'diff_hours': 0, 'diff_minutes': 0, 'is_increase': False}
    
    return {
        'yesterday': yesterday_diff,
        'last_week': last_week_diff,
        'last_month': last_month_diff
    }

def calculate_diff(sorted_times, current_idx, compare_idx):
    """2つの睡眠時間の差分を計算"""
    if not sorted_times or compare_idx is None or current_idx >= len(sorted_times) or compare_idx >= len(sorted_times):
        return {'diff_hours': 0, 'diff_minutes': 0, 'is_increase': False}
    
    current = sorted_times[current_idx]['duration']
    compare = sorted_times[compare_idx]['duration']
    diff = current - compare
    is_increase = diff > 0
    
    diff_abs = abs(diff)
    diff_hours = int(diff_abs)
    diff_minutes = int((diff_abs - diff_hours) * 60)
    
    return {
        'diff_hours': diff_hours,
        'diff_minutes': diff_minutes,
        'is_increase': is_increase
    }  # 閉じ括弧を追加

def sort_sleep_graph_data(sleep_data):
    sorted_data = sorted(sleep_data, key=lambda x: x['timestamp'], reverse=True)
    return sorted_data

def prevent_message_duplication(current_page):
    pages_with_message = ['daily', 'weekly', 'monthly', 'all']
    if current_page not in pages_with_message:
        return None  # ホーム画面にはメッセージを表示しない
    
def index():
    user_message_key = f"user_{session['user_id']}_message"
    user_message = session.pop(user_message_key, None)
    return render_template("index.html", user_message=user_message)

# ルート定義
@app.route('/', methods=['GET', 'POST'])
@login_required
def index():
    page = request.args.get('page', 1, type=int)
    per_page = 10
    offset = (page - 1) * per_page
    
    with get_db_connection() as conn:
        try:
            # 総レコード数を取得
            total_records = conn.execute('''
                SELECT COUNT(*) FROM records
                WHERE user_id = ? AND is_deleted = 0 AND DATE(timestamp, '+9 hours') = DATE('now', '+9 hours')
            ''', (session['user_id'],)).fetchone()[0]
            
            # ページネーション付きでレコードを取得
            records = conn.execute('''
                SELECT id, action,
                       strftime('%Y-%m-%d', datetime(timestamp, '+9 hours')) as formatted_date,
                       strftime('%H:%M:%S', datetime(timestamp, '+9 hours')) as formatted_time,
                       memo, likes_count
                FROM records
                WHERE user_id = ? AND is_deleted = 0 AND DATE(timestamp, '+9 hours') = DATE('now', '+9 hours')
                ORDER BY timestamp DESC
                LIMIT ? OFFSET ?
            ''', (session['user_id'], per_page, offset)).fetchall()
            
            total_pages = (total_records + per_page - 1) // per_page
            
        except sqlite3.Error as e:
            flash(f"データベースエラーが発生しました: {e}", "error")
            records = []
            total_pages = 0
    
    return render_template("index.html",
                           records=records,
                           is_private=session.get('is_private', False),
                           page=page,
                           total_pages=total_pages)

@app.route('/like_record/<int:record_id>', methods=['POST'])
@login_required
def like_record(record_id):
    from_page = request.args.get('from_page', 'index')
    
    try:
        with get_db_connection() as conn:
            # すでにいいね済みか確認
            existing_like = conn.execute(
                'SELECT id FROM likes WHERE user_id = ? AND record_id = ?',
                (session['user_id'], record_id)
            ).fetchone()
            
            if existing_like:
                flash('すでにいいね済みです。', 'info')
            else:
                # likesテーブルに新しいいいねを追加
                conn.execute(
                    'INSERT INTO likes (user_id, record_id, timestamp) VALUES (?, ?, ?)',
                    (session['user_id'], record_id, jst_now())
                )
                
                # recordsテーブルのlikes_countを更新
                conn.execute(
                    'UPDATE records SET likes_count = likes_count + 1 WHERE id = ?',
                    (record_id,)
                )
                
                conn.commit()
                flash('いいねしました！', 'success')
    except sqlite3.Error as e:
        flash(f'エラーが発生しました: {e}', 'error')
    
    # from_pageに基づいてリダイレクト
    if from_page == 'index':
        return redirect(url_for('index'))
    elif from_page == 'all_records':
        return redirect(url_for('all_records'))
    else:
        return redirect(url_for('index'))

@app.route('/calendar', methods=['GET'])
@login_required
def calendar_view():
    # 現在の日本時間を取得
    now = datetime.now(pytz.timezone('Asia/Tokyo'))
    year = request.args.get('year', now.year, type=int)
    month = request.args.get('month', now.month, type=int)
    
    # カレンダー生成
    cal = calendar.monthcalendar(year, month)
    
    # 前月/次月計算
    prev_year, prev_month = (year, month-1) if month > 1 else (year-1, 12)
    next_year, next_month = (year, month+1) if month < 12 else (year+1, 1)
    
    return render_template(
        'calendar.html',
        year=year,
        month=month,
        cal=cal,
        prev_year=prev_year,
        prev_month=prev_month,
        next_year=next_year,
        next_month=next_month,
        today=now.date()
    )

def generate_calendar(year, month):
    cal = calendar.monthcalendar(year, month)
    return cal

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form.get('username').strip()
        password = request.form.get('password')
        
        with get_db_connection() as conn:
            user = conn.execute(
                'SELECT * FROM users WHERE username = ?', (username,)
            ).fetchone()
            
            if user:
                if user['password'] == hash_password(password):
                    session.clear()  # セッションをクリア
                    session.permanent = True
                    session['user_id'] = user['id']
                    session['username'] = user['username']
                    session['is_admin'] = bool(user['is_admin'])
                    session['is_private'] = bool(user['is_private'] if 'is_private' in user.keys() else 0)
                    
                    flash('おかえりなさい！', 'success')
                    
                    if session['is_admin']:
                        return redirect(url_for('admin_dashboard'))
                    else:
                        return redirect(url_for('index'))
                else:
                    flash('ユーザー名またはパスワードが間違っています。', 'error')
            else:
                flash('ユーザーが存在しません。新規登録してください。', 'error')
                
    return render_template('login.html')

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        username = request.form.get('username').strip()
        password = request.form.get('password')
        is_private = request.form.get('is_private') == 'on'  # チェックボックスの値を取得
        
        if not username or not password:
            flash('ユーザー名とパスワードを入力してください。', 'error')
            return render_template('register.html')
            
        with get_db_connection() as conn:
            existing_user = conn.execute(
                'SELECT id FROM users WHERE username = ?', (username,)
            ).fetchone()
            
            if existing_user:
                flash('このユーザー名は既に使用されています。', 'error')
                return render_template('register.html')
                
            conn.execute(
                'INSERT INTO users (username, password, is_private) VALUES (?, ?, ?)',
                (username, hash_password(password), int(is_private))
            )
            conn.commit()
            
        flash('登録しました！', 'success')
        return redirect(url_for('login'))
        
    return render_template('register.html')

@app.route('/sleep_graph')
@login_required
def sleep_graph():
    period = request.args.get('period', 'daily')  # デフォルトは日別
    return render_template('sleep_graph.html', period=period)

@app.route('/reset_password', methods=['GET', 'POST'])
def reset_password():
    if request.method == 'POST':
        username = request.form.get('username').strip()
        new_password = request.form.get('new_password')
        
        with get_db_connection() as conn:
            user = conn.execute(
                'SELECT * FROM users WHERE username = ?', (username,)
            ).fetchone()
            
            if user:
                conn.execute(
                    'UPDATE users SET password = ? WHERE username = ?',
                    (hash_password(new_password), username)
                )
                conn.commit()
                flash('パスワードが更新されました。ログインしてください。', 'success')
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
def record():
    action = request.form.get('action')
    memo = request.form.get('memo', '')
    
    if not action or action not in ['wake_up', 'sleep']:
        flash('有効な行動を選択してください', 'danger')
        return redirect(url_for('index'))

    try:
        # 日本時間のタイムスタンプを明示的に生成
        timestamp = datetime.now(pytz.timezone('Asia/Tokyo'))
        
        with get_db_connection() as conn:
            # 同じ日に同じアクションの記録があるかチェック
            existing_record = conn.execute('''
                SELECT * FROM records
                WHERE user_id = ? AND action = ? AND DATE(timestamp, '+9 hours') = DATE(?, '+9 hours')
                AND is_deleted = 0
            ''', (session['user_id'], action, timestamp.isoformat())).fetchone()

            if existing_record:
                flash('既に本日分は登録されています', 'warning')
                return redirect(url_for('index'))

            conn.execute('BEGIN TRANSACTION')
            # レコード挿入
            conn.execute(
                '''INSERT INTO records
                (user_id, action, timestamp, memo)
                VALUES (?, ?, ?, ?)''',
                (session['user_id'], action, timestamp.isoformat(), memo)
            )
            # トランザクションコミット
            conn.commit()
            flash('記録が正常に保存されました', 'success')
    except sqlite3.Error as e:
        conn.rollback()
        error_message = f'データベースエラー: {str(e)}'
        app.logger.error(error_message)
        flash(error_message, 'danger')
    except Exception as e:
        error_message = f'予期せぬエラー: {str(e)}'
        app.logger.error(error_message)
        flash(error_message, 'danger')

    return redirect(url_for('index'))

@app.route('/average_sleep')
@login_required
def average_sleep():
    try:
        period = request.args.get('period', 'daily')
        with get_db_connection() as conn:
            data = conn.execute('''
                SELECT date(timestamp, '+9 hours') as date,
                       action,
                       timestamp
                FROM records
                WHERE user_id = ? AND is_deleted = 0
                  AND (action = 'sleep' OR action = 'wake_up')
                ORDER BY timestamp
            ''', (session['user_id'],)).fetchall()

            if not data:
                # 0件でもエラーにせず、空リスト・デフォルト値で描画
                return render_template(
                    'average_sleep.html',
                    sleep_times=[],
                    daily_avg={'avg_hours': 0, 'avg_minutes': 0, 'evaluation': "-"},
                    weekly_avg=[],
                    monthly_avg=[],
                    overall_avg={'avg_hours': 0, 'avg_minutes': 0, 'evaluation': "-"},
                    comparisons=None,
                    period=period,
                    evaluate_sleep=evaluate_sleep,
                    round_decimal=round_decimal
                )

            # 睡眠時間を計算
            sleep_times = []
            sleep_start = None
            for row in data:
                if row['action'] == 'sleep':
                    sleep_start = datetime.fromisoformat(row['timestamp'])
                elif row['action'] == 'wake_up' and sleep_start:
                    wake_time = datetime.fromisoformat(row['timestamp'])
                    sleep_duration = (wake_time - sleep_start).total_seconds() / 3600
                    sleep_hours = int(sleep_duration)
                    sleep_minutes = int((sleep_duration - sleep_hours) * 60)
                    sleep_date = datetime.fromisoformat(row['timestamp']).date()
                    sleep_times.append({
                        'date': sleep_date,
                        'duration': sleep_duration,
                        'hours': sleep_hours,
                        'minutes': sleep_minutes,
                        'week': sleep_date.isocalendar()[1],
                        'month': sleep_date.month,
                        'year': sleep_date.year
                    })
                    sleep_start = None

            daily_avg = calculate_average(sleep_times)
            overall_avg = calculate_overall_average(sleep_times)
            weekly_avg = calculate_weekly_average(sleep_times)
            monthly_avg = calculate_monthly_average(sleep_times)
            comparisons = calculate_comparisons(sleep_times)
            sleep_times.sort(key=lambda x: x['date'], reverse=True)

            return render_template(
                'average_sleep.html',
                daily_avg=daily_avg,
                weekly_avg=weekly_avg,
                monthly_avg=monthly_avg,
                overall_avg=overall_avg,
                comparisons=comparisons,
                sleep_times=sleep_times,
                period=period,
                evaluate_sleep=evaluate_sleep,
                round_decimal=round_decimal
            )
    except Exception as e:
        app.logger.error(f"エラーが発生しました: {str(e)}")
        flash("データの取得に失敗しました。再度お試しください。", "danger")
        return redirect(url_for('index'))


def calculate_average(sleep_times):
    if not sleep_times:
        return {'avg_hours': 0, 'avg_minutes': 0, 'evaluation': "-"}
    total_sleep = sum(item['duration'] for item in sleep_times)
    avg_sleep = total_sleep / len(sleep_times)
    avg_hours = int(avg_sleep)
    avg_minutes = int((avg_sleep - avg_hours) * 60)
    evaluation = evaluate_sleep(avg_sleep) if len(sleep_times) >= 3 else "-"
    return {
        'avg_hours': avg_hours,
        'avg_minutes': avg_minutes,
        'avg_duration': avg_sleep,
        'evaluation': evaluation
    }

def calculate_overall_average(sleep_times):
    if not sleep_times:
        return {'avg_hours': 0, 'avg_minutes': 0, 'evaluation': "-"}
    total_sleep = sum(item['duration'] for item in sleep_times)
    avg_sleep = total_sleep / len(sleep_times)
    avg_hours = int(avg_sleep)
    avg_minutes = int((avg_sleep - avg_hours) * 60)
    evaluation = evaluate_sleep(avg_sleep) if len(sleep_times) >= 3 else "-"
    return {
        'avg_hours': avg_hours,
        'avg_minutes': avg_minutes,
        'avg_duration': avg_sleep,
        'evaluation': evaluation
    }

def calculate_weekly_average(sleep_times):
    if not sleep_times:
        return []  # ← ここが大事！
    # 以下略...

    # 週ごとにグループ化
    weeks = {}
    for item in sleep_times:
        week_key = f"{item['year']}-W{item['week']:02d}"
        if week_key not in weeks:
            weeks[week_key] = []
        weeks[week_key].append(item)

    # 各週の平均を計算
    weekly_avgs = []
    for week_key, items in weeks.items():
        total_sleep = sum(item['duration'] for item in items)
        avg_sleep = total_sleep / len(items)
        avg_hours = int(avg_sleep)
        avg_minutes = int((avg_sleep - avg_hours) * 60)

        year, week = week_key.split('-W')
        start_date = datetime.strptime(f'{year}-{week}-1', '%Y-%W-%w').date()
        end_date = start_date + timedelta(days=6)

        weekly_avgs.append({
            'period': f"{start_date.strftime('%Y/%m/%d')}～{end_date.strftime('%Y/%m/%d')}",
            'avg_hours': avg_hours,
            'avg_minutes': avg_minutes,
            'avg_duration': avg_sleep,
            'evaluation': evaluate_sleep(avg_sleep),
            'start_date': start_date,
            'week_key': week_key,
            'record_days': len(items)  # ✅ 追加ポイント
        })

    weekly_avgs.sort(key=lambda x: x['start_date'], reverse=True)
    return weekly_avgs

def calculate_monthly_average(sleep_times):
    if not sleep_times:
        return []  # ← 同様にここも！
    # 以下略...

    # 月ごとにグループ化
    months = {}
    for item in sleep_times:
        month_key = f"{item['year']}-{item['month']:02d}"
        if month_key not in months:
            months[month_key] = []
        months[month_key].append(item)

    # 各月の平均を計算
    monthly_avgs = []
    for month_key, items in months.items():
        total_sleep = sum(item['duration'] for item in items)
        avg_sleep = total_sleep / len(items)
        avg_hours = int(avg_sleep)
        avg_minutes = int((avg_sleep - avg_hours) * 60)

        year, month = map(int, month_key.split('-'))
        start_date = datetime(year, month, 1).date()

        monthly_avgs.append({
            'period': f"{year}年{month}月",
            'avg_hours': avg_hours,
            'avg_minutes': avg_minutes,
            'avg_duration': avg_sleep,
            'evaluation': evaluate_sleep(avg_sleep),
            'start_date': start_date,
            'record_days': len(items)  # ✅ 追加
        })

    monthly_avgs.sort(key=lambda x: x['start_date'], reverse=True)
    return monthly_avgs

def calculate_comparisons(sleep_times):
    if not sleep_times:
        return {
            'yesterday': {'diff_hours': 0, 'diff_minutes': 0, 'is_increase': False},
            'last_week': {'diff_hours': 0, 'diff_minutes': 0, 'is_increase': False},
            'last_month': {'diff_hours': 0, 'diff_minutes': 0, 'is_increase': False}
        }
    
    # 日付でソート
    sorted_times = sorted(sleep_times, key=lambda x: x['date'], reverse=True)
    
    # 現在の日付を取得（sorted_times[0]['date']がすでにdatetime.dateオブジェクト）
    today = sorted_times[0]['date']
    
    # 前日比
    yesterday_diff = calculate_diff(sorted_times, 0, 1)
    
    # 先週比（同じ曜日）
    last_week_idx = next((i for i, item in enumerate(sorted_times) if (today - item['date']).days >= 7 and today.weekday() == item['date'].weekday()), None)
    last_week_diff = calculate_diff(sorted_times, 0, last_week_idx) if last_week_idx else {'diff_hours': 0, 'diff_minutes': 0, 'is_increase': False}
    
    # 先月比（同じ日）
    last_month_day = today.day
    last_month = today.month - 1 if today.month > 1 else 12
    last_month_year = today.year if today.month > 1 else today.year - 1
    last_month_idx = next((i for i, item in enumerate(sorted_times) if item['date'].year == last_month_year and item['date'].month == last_month and item['date'].day == last_month_day), None)
    last_month_diff = calculate_diff(sorted_times, 0, last_month_idx) if last_month_idx else {'diff_hours': 0, 'diff_minutes': 0, 'is_increase': False}
    
    return {
        'yesterday': yesterday_diff,
        'last_week': last_week_diff,
        'last_month': last_month_diff
    }

def calculate_diff(sorted_times, current_idx, compare_idx):
    """2つの睡眠時間の差分を計算"""
    if not sorted_times or compare_idx is None or current_idx >= len(sorted_times) or compare_idx >= len(sorted_times):
        return {'diff_hours': 0, 'diff_minutes': 0, 'is_increase': False}
    
    current = sorted_times[current_idx]['duration']
    compare = sorted_times[compare_idx]['duration']
    diff = current - compare
    is_increase = diff > 0
    
    diff_abs = abs(diff)
    diff_hours = int(diff_abs)
    diff_minutes = int((diff_abs - diff_hours) * 60)
    
    return {
        'diff_hours': diff_hours,
        'diff_minutes': diff_minutes,
        'is_increase': is_increase
    }

def evaluate_sleep(sleep_duration):
    """睡眠時間の評価を行う"""
    optimal_sleep = 7.0  # 適正睡眠時間
    sleep_ratio = sleep_duration / optimal_sleep
    
    if sleep_ratio >= 1.5:
        return "寝すぎ⁉"
    elif 1.286 <= sleep_ratio < 1.5:
        return "ちょい寝すぎか。"
    elif 1.0 <= sleep_ratio < 1.286:
        return "良好だよ☻"
    elif 0.858 <= sleep_ratio < 1.0:
        return "もう少し寝てほしいかも..."
    elif 0.50 <= sleep_ratio < 0.858:
        return "頼むこれ以上は..."
    else:
        return "いい加減もっと寝ろよ！！"
    
def revise_previous_day_comparison(sleep_times):
    comparisons = []
    for i in range(len(sleep_times)):
        if i == 0:
            comparisons.append("-")
        else:
            diff = sleep_times[i]['duration'] - sleep_times[i-1]['duration']
            diff_hours = int(abs(diff))
            diff_minutes = int((abs(diff) - diff_hours) * 60)
            comparison = f"{'+' if diff > 0 else '-'}{diff_hours}時間{diff_minutes}分"
            comparisons.append(comparison)
    return comparisons

# カレンダーの日付クリック時に「今日の評価」と当日の睡眠時間表示
@app.route('/day_records/<date>')  # パラメータを明示的に指定
@login_required
def day_records(date):  # パラメータを受け取る
    # 既存のコード
    try:
        # 日付形式の検証を追加
        parsed_date = datetime.strptime(date, '%Y-%m-%d').date()
        # 年と月の情報を取得して保持
        year = parsed_date.year
        month = parsed_date.month
    except ValueError:
        flash('無効な日付形式です', 'error')
        return redirect(url_for('calendar_view'))
    
    # デフォルト値を設定
    sleep_hours = None
    sleep_minutes = None
    sleep_evaluation = None
    
    with get_db_connection() as conn:
        try:
            if session.get('is_admin'):
                records = conn.execute('''
                    SELECT r.id, r.action, r.memo, r.is_deleted, r.likes_count, u.username,
                    strftime('%Y-%m-%d %H:%M:%S', datetime(r.timestamp, '+9 hours')) as formatted_time
                    FROM records r
                    JOIN users u ON r.user_id = u.id
                    WHERE DATE(r.timestamp, '+9 hours') = ?
                    ORDER BY r.timestamp ASC
                ''', (date,)).fetchall()
            else:
                records = conn.execute('''
                    SELECT id, action, memo, is_deleted, likes_count,
                    strftime('%Y-%m-%d %H:%M:%S', datetime(timestamp, '+9 hours')) as formatted_time
                    FROM records
                    WHERE user_id = ?
                    AND DATE(timestamp, '+9 hours') = ?
                    AND is_deleted = 0
                    ORDER BY timestamp ASC
                ''', (session['user_id'], date)).fetchall()
            
            # 当日の睡眠時間を計算
            sleep_data = conn.execute('''
                SELECT action, timestamp
                FROM records
                WHERE user_id = ? 
                AND DATE(timestamp, '+9 hours') = ?
                AND (action = 'sleep' OR action = 'wake_up')
                AND is_deleted = 0
                ORDER BY timestamp
            ''', (session['user_id'], date)).fetchall()
            
            sleep_duration = None
            sleep_evaluation = None
            
            # 睡眠データがある場合のみ計算
            if sleep_data:
                sleep_start = None
                for row in sleep_data:
                    if row['action'] == 'sleep' and not sleep_start:
                        sleep_start = datetime.fromisoformat(row['timestamp'])
                    elif row['action'] == 'wake_up' and sleep_start:
                        wake_time = datetime.fromisoformat(row['timestamp'])
                        sleep_duration = (wake_time - sleep_start).total_seconds() / 3600  # 時間単位

                        # 時間と分に分割
                        sleep_hours = int(sleep_duration)
                        sleep_minutes = int((sleep_duration - sleep_hours) * 60)

                        
                        # 評価メッセージを決定
                        optimal_sleep = 7.0  # 適正睡眠時間
                        sleep_ratio = sleep_duration / optimal_sleep
                        
                        if sleep_ratio >= 1.5:
                            sleep_evaluation = "寝すぎ⁉"
                        elif 1.286 <= sleep_ratio < 1.5:
                            sleep_evaluation = "ちょい寝すぎか。"
                        elif 1.0 <= sleep_ratio < 1.286:
                            sleep_evaluation = "良好だよ☻"
                        elif 0.858 <= sleep_ratio < 1.0:
                            sleep_evaluation = "もう少し寝てほしいかも..."
                        elif 0.50 <= sleep_ratio < 0.858:
                            sleep_evaluation = "頼むこれ以上は..."
                        else:
                            sleep_evaluation = "いい加減もっと寝ろよ！！"
                        
                        break
            
            return render_template('day_records.html',
                     date=parsed_date.strftime('%Y-%m-%d'),
                     records=records,
                     is_admin=session.get('is_admin'),
                     sleep_duration_hours=sleep_hours,
                     sleep_duration_minutes=sleep_minutes,
                     sleep_evaluation=sleep_evaluation,
                     year=year,
                     month=month)

        
        except sqlite3.Error as e:
            flash(f'データベースエラー: {str(e)}', 'error')
            return redirect(url_for('calendar_view'))
        
@app.route('/all_records')
@login_required
def all_records():
    page = request.args.get("page", 1, type=int)
    per_page = 20
    offset = (page - 1) * per_page
    user_filter = request.args.get("user_id", "all")

    with get_db_connection() as conn:
        # ユーザーリストを取得（adminや非公開ユーザーは除外）
        users = conn.execute(
            "SELECT id, username FROM users WHERE is_admin = 0 AND is_private = 0 ORDER BY username"
        ).fetchall()

        # クエリ条件を構築（adminユーザーも除外）
        query_conditions = "records.is_deleted=0 AND users.is_private=0 AND users.username != 'admin'"
        query_params = []

        if user_filter != "all" and user_filter.isdigit():
            query_conditions += " AND records.user_id=?"
            query_params.append(int(user_filter))

        # 総レコード数を取得
        count_query = f"SELECT COUNT(*) FROM records JOIN users ON records.user_id=users.id WHERE {query_conditions}"
        total_records = conn.execute(count_query, query_params).fetchone()[0]

        # レコードを取得
        records_query = f"""
            SELECT users.username, users.id as user_id, records.id, records.action,
                strftime('%Y-%m-%d', datetime(records.timestamp, '+9 hours')) as formatted_date,
                strftime('%H:%M:%S', datetime(records.timestamp, '+9 hours')) as formatted_time,
                records.memo, records.likes_count
            FROM records JOIN users ON records.user_id=users.id
            WHERE {query_conditions}
            ORDER BY records.timestamp DESC LIMIT ? OFFSET ?"""

        records = conn.execute(records_query, query_params + [per_page, offset]).fetchall()

        # いいね情報を取得
        liked_ids = [row["record_id"] for row in conn.execute(
            "SELECT record_id FROM likes WHERE user_id=?",
            (session["user_id"],)
        ).fetchall()]

        formatted_records = []
        for record in records:
            formatted_records.append({
                "username": record["username"],
                "user_id": record["user_id"],
                "formatted_date": record["formatted_date"],
                "formatted_time": record["formatted_time"],
                "action": record["action"],
                "memo": record["memo"],
                "likes_count": record["likes_count"],
                "id": record["id"],
                "liked": record["id"] in liked_ids
            })

        total_pages = (total_records + per_page - 1) // per_page

        return render_template("all_records.html",
                              records=formatted_records,
                              users=users,
                              current_user=user_filter,
                              page=page,
                              total_pages=total_pages)

@app.route('/toggle_privacy', methods=['POST'])
@login_required
def toggle_privacy():
    if 'user_id' not in session:
        return redirect(url_for('login'))
        
    is_private = request.form.get('is_private') == 'on'
    
    try:
        with get_db_connection() as conn:
            conn.execute(
                'UPDATE users SET is_private = ? WHERE id = ?',
                (int(is_private), session['user_id'])
            )
            conn.commit()
            
        # セッションの値も更新
        session['is_private'] = is_private
        
        if is_private:
            flash('プライベートモードに設定しました。あなたの記録は他のユーザーには表示されません。', 'success')
        else:
            flash('パブリックモードに設定しました。あなたの記録は他のユーザーにも表示されます。', 'success')
            
    except sqlite3.Error as e:
        flash(f'プライバシー設定更新中にエラーが発生しました: {e}', 'error')
        
    return redirect(url_for('index'))

@app.route('/delete_record/<int:record_id>', methods=['POST'])  # パラメータを明示的に指定
@login_required
def delete_record(record_id):  # パラメータを受け取る
    # 既存のコード
    try:
        # リダイレクト先を取得（デフォルトはday_records）
        redirect_to = request.form.get('redirect_to', 'day_records')
        
        with get_db_connection() as conn:
            # 記録が自分のものかチェック
            record = conn.execute('''
                SELECT * FROM records
                WHERE id = ? AND user_id = ?
            ''', (record_id, session['user_id'])).fetchone()
            
            if not record:
                flash('記録が見つからないか、削除権限がありません。', 'error')
                return redirect(url_for('index'))
            
            # 記録の日付を取得
            record_date = datetime.fromisoformat(record['timestamp']).date()
            
            # 記録を論理削除
            conn.execute('''
                UPDATE records
                SET is_deleted = 1
                WHERE id = ? AND user_id = ?
            ''', (record_id, session['user_id']))
            conn.commit()
            
            flash('記録が削除されました。', 'success')
            
            # リダイレクト先の判断
            if redirect_to == 'index':
                return redirect(url_for('index'))
            elif redirect_to == 'all_records':
                # ページ番号とユーザーフィルターを保持
                page = request.form.get('page', 1)
                user_filter = request.form.get('user_filter', 'all')
                return redirect(url_for('all_records', page=page, user_id=user_filter))
            else:
                return redirect(url_for('day_records', date=record_date.strftime('%Y-%m-%d')))
                
    except sqlite3.Error as e:
        flash(f'記録の削除中にエラーが発生しました: {e}', 'error')
        return redirect(url_for('index'))

# 管理者画面の記録一覧ページネーション（20件ずつ）
@app.route('/admin_dashboard')
@admin_required
def admin_dashboard():
    page = request.args.get('page', 1, type=int)
    per_page = 20
    offset = (page - 1) * per_page
    
    with get_db_connection() as conn:
        # 一般ユーザーのみ取得（管理者以外）
        users = conn.execute(
            "SELECT id, username, created_at, is_private "
            "FROM users WHERE is_admin = 0 "
            "ORDER BY created_at DESC"
        ).fetchall()
        
        # 総レコード数を取得
        total_records = conn.execute('''
        SELECT COUNT(*) FROM records
        ''').fetchone()[0]
        
        # 記録リスト取得（ページネーション付き）
        records = conn.execute('''
        SELECT r.*, u.username, u.is_private,
        strftime('%Y-%m-%d %H:%M:%S', datetime(r.timestamp, '+9 hours')) as formatted_time
        FROM records r
        JOIN users u ON r.user_id = u.id
        ORDER BY r.timestamp DESC
        LIMIT ? OFFSET ?
        ''', (per_page, offset)).fetchall()
        
        total_pages = (total_records + per_page - 1) // per_page
        
        return render_template("admin_dashboard.html",
                              users=users,
                              records=records,
                              page=page,
                              total_pages=total_pages)

@app.route('/admin/user_records/<int:user_id>')  # パラメータを明示的に指定
@admin_required
def admin_user_records(user_id):  # パラメータを受け取る
    # 既存のコード
    page = request.args.get('page', 1, type=int)
    per_page = 20
    offset = (page - 1) * per_page
    
    with get_db_connection() as conn:
        # ユーザー情報を取得
        user = conn.execute('SELECT username FROM users WHERE id = ?', (user_id,)).fetchone()
        if not user:
            flash('ユーザーが見つかりません', 'error')
            return redirect(url_for('admin_dashboard'))
            
        # 総レコード数を取得
        total_records = conn.execute('''
            SELECT COUNT(*) FROM records
            WHERE user_id = ? AND is_deleted = 0
        ''', (user_id,)).fetchone()[0]
        
        # ページネーション付きでレコードを取得
        records = conn.execute('''
            SELECT id, action,
            strftime('%Y-%m-%d', datetime(timestamp, '+9 hours')) as formatted_date,
            strftime('%H:%M:%S', datetime(timestamp, '+9 hours')) as formatted_time,
            memo, likes_count, is_deleted
            FROM records
            WHERE user_id = ?
            ORDER BY timestamp DESC
            LIMIT ? OFFSET ?
        ''', (user_id, per_page, offset)).fetchall()
        
        total_pages = (total_records + per_page - 1) // per_page
        
        return render_template('admin_user_records.html',
                              user=user,
                              user_id=user_id,
                              records=records,
                              page=page,
                              total_pages=total_pages)
    
@app.route('/admin/add_record', methods=['GET', 'POST'])
@admin_required
def admin_add_record():
    if request.method == 'GET':
        # ユーザーリストを取得（管理者を除外）
        with get_db_connection() as conn:
            users = conn.execute('SELECT id, username FROM users WHERE is_admin = 0').fetchall()
        return render_template('admin_add_record.html', users=users)

    # POSTリクエスト時の処理
    user_id = request.form.get('user_id')
    action = request.form.get('action')
    memo = request.form.get('memo', '')
    timestamp_input = request.form.get('timestamp')  # フォームから取得したタイムスタンプ

    if not user_id or not action or action not in ['wake_up', 'sleep']:
        flash('有効なユーザーと行動を選択してください', 'danger')
        return redirect(url_for('admin_add_record'))

    try:
        # タイムスタンプを JST に変換
        if timestamp_input:
            timestamp = datetime.strptime(timestamp_input, '%Y-%m-%dT%H:%M')
            timestamp = pytz.timezone('Asia/Tokyo').localize(timestamp)
        else:
            timestamp = jst_now()  # フォーム未入力の場合は現在時刻

        with get_db_connection() as conn:
            conn.execute('''
                INSERT INTO records (user_id, action, timestamp, memo)
                VALUES (?, ?, ?, ?)
            ''', (user_id, action, timestamp.isoformat(), memo))  # JST のタイムスタンプを保存
            
            conn.commit()

        # 該当ユーザーに通知メッセージを設定
        session[f'user_{user_id}_message'] = "管理者が記録を追加しました。"

        flash('記録が追加されました。', 'success')
    except Exception as e:
        flash(f'記録追加中にエラーが発生しました: {e}', 'danger')

    return redirect(url_for('admin_dashboard'))

@app.route('/admin/delete_record/<int:record_id>', methods=['POST'])
@admin_required
def admin_delete_record(record_id):
    try:
        with get_db_connection() as conn:
            # 記録が存在するか確認
            record = conn.execute('SELECT * FROM records WHERE id = ?', (record_id,)).fetchone()
            if not record:
                flash('記録が見つかりません。', 'error')
                return redirect(url_for('admin_dashboard'))

            # 記録を論理削除
            conn.execute('UPDATE records SET is_deleted = 1 WHERE id = ?', (record_id,))
            conn.commit()

            flash('記録が削除されました。', 'success')

        # 削除後、ユーザーの「記録を見る」画面にリダイレクト
        return redirect(url_for('admin_user_records', user_id=record['user_id']))
    except Exception as e:
        flash(f'記録削除中にエラーが発生しました: {e}', 'danger')
        return redirect(url_for('admin_dashboard'))

@app.route('/delete_user/<int:user_id>', methods=['POST'])  # パラメータを明示的に指定
@admin_required
def delete_user(user_id):  # パラメータを受け取る
    # 既存のコード
    # 関数の内容
    # 自分自身は削除できない
    if user_id == session.get('user_id'):
        flash('自分自身を削除することはできません。', 'error')
        return redirect(url_for('admin_dashboard'))
        
    with get_db_connection() as conn:
        # 削除対象が管理者かチェック
        target_user = conn.execute('SELECT is_admin FROM users WHERE id = ?', (user_id,)).fetchone()
        if target_user and target_user['is_admin']:
            flash('管理者ユーザーは削除できません。', 'error')
            return redirect(url_for('admin_dashboard'))
            
        try:
            # トランザクション開始
            conn.execute('BEGIN TRANSACTION')
            
            # ユーザーのいいねを削除
            conn.execute('DELETE FROM likes WHERE user_id = ?', (user_id,))
            
            # ユーザーの記録に対するいいねも削除
            conn.execute('DELETE FROM likes WHERE record_id IN (SELECT id FROM records WHERE user_id = ?)', (user_id,))
            
            # ユーザーの記録を削除
            conn.execute('DELETE FROM records WHERE user_id = ?', (user_id,))
            
            # ユーザーを削除
            conn.execute('DELETE FROM users WHERE id = ?', (user_id,))
            
            # トランザクションコミット
            conn.commit()
            
            flash('ユーザーが削除されました。', 'success')
        except sqlite3.Error as e:
            # エラー時はロールバック
            conn.rollback()
            flash(f'ユーザー削除中にエラーが発生しました: {e}', 'error')
            
    return redirect(url_for('admin_dashboard'))

@app.route('/api/sleep_data')
@login_required
def sleep_data():
    try:
        # 選択された期間（日別、週別、月別）を取得
        period = request.args.get('period', 'daily')  # デフォルトは日別
        
        with get_db_connection() as conn:
            if period == 'daily':
                # 日別データを取得
                data = conn.execute('''
                    SELECT date(timestamp, '+9 hours') as date,
                           action,
                           timestamp
                    FROM records
                    WHERE user_id = ? AND is_deleted = 0
                    AND (action = 'sleep' OR action = 'wake_up')
                    ORDER BY timestamp
                ''', (session['user_id'],)).fetchall()
                
                # 睡眠時間を計算
                sleep_times = []
                sleep_start = None
                
                for row in data:
                    if row['action'] == 'sleep':
                        sleep_start = datetime.fromisoformat(row['timestamp'])
                    elif row['action'] == 'wake_up' and sleep_start:
                        wake_time = datetime.fromisoformat(row['timestamp'])
                        sleep_duration = (wake_time - sleep_start).total_seconds() / 3600  # 時間単位
                        
                        # 時間と分に分割
                        sleep_hours = int(sleep_duration)
                        sleep_minutes = int((sleep_duration - sleep_hours) * 60)
                        
                        sleep_times.append({
                            'date': row['date'],
                            'duration': sleep_duration,
                            'hours': sleep_hours,
                            'minutes': sleep_minutes
                        })
                        sleep_start = None
                
            elif period == 'weekly':
                # 週別平均データを取得
                weekly_avg = calculate_weekly_average(get_sleep_times(conn, session['user_id']))
                sleep_times = []
                
                for item in weekly_avg:
                    sleep_times.append({
                        'period': item['period'],
                        'avg_duration': item['avg_duration'],
                        'avg_hours': item['avg_hours'],
                        'avg_minutes': item['avg_minutes']
                    })
                
            elif period == 'monthly':
                # 月別平均データを取得
                monthly_avg = calculate_monthly_average(get_sleep_times(conn, session['user_id']))
                sleep_times = []
                
                for item in monthly_avg:
                    sleep_times.append({
                        'period': item['period'],
                        'avg_duration': item['avg_duration'],
                        'avg_hours': item['avg_hours'],
                        'avg_minutes': item['avg_minutes']
                    })
        
        return jsonify(sleep_times)
    except Exception as e:
        app.logger.error(f"エラーが発生しました: {str(e)}")
        return jsonify({'error': 'データ取得中にエラーが発生しました。'}), 500

# 睡眠時間データを取得するヘルパー関数
def get_sleep_times(conn, user_id):
    data = conn.execute('''
        SELECT date(timestamp, '+9 hours') as date,
               action,
               timestamp
        FROM records
        WHERE user_id = ? AND is_deleted = 0
        AND (action = 'sleep' OR action = 'wake_up')
        ORDER BY timestamp
    ''', (user_id,)).fetchall()
    
    sleep_times = []
    sleep_start = None
    
    for row in data:
        if row['action'] == 'sleep':
            sleep_start = datetime.fromisoformat(row['timestamp'])
        elif row['action'] == 'wake_up' and sleep_start:
            wake_time = datetime.fromisoformat(row['timestamp'])
            sleep_duration = (wake_time - sleep_start).total_seconds() / 3600  # 時間単位
            
            sleep_date = datetime.fromisoformat(row['timestamp']).date()
            sleep_times.append({
                'date': sleep_date,
                'duration': sleep_duration,
                'hours': int(sleep_duration),
                'minutes': int((sleep_duration - int(sleep_duration)) * 60),
                'week': sleep_date.isocalendar()[1],  # ISO週番号
                'month': sleep_date.month,
                'year': sleep_date.year
            })
            sleep_start = None
    
    return sleep_times

from flask import send_file

@app.route('/download_db')
def download_db():
    return send_file(DATABASE_PATH, as_attachment=True)

# その他のルートと関数は変更なし（適切なwith文を使用してデータベース接続を管理）

if __name__ == '__main__':
    app.run(debug=True)