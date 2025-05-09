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
            os.makedirs(db_dir, exist_ok=True)
            print(f"ディレクトリ {db_dir} を作成しました（または既に存在）。")
        except OSError as e:
            print(f"ディレクトリ {db_dir} の作成に失敗しました: {e}")
            raise

# --- GitHubバックアップ・リストア関連 ---
def is_db_empty(db_path_to_check=DATABASE_PATH):
    if not os.path.exists(db_path_to_check): return True
    try:
        conn = sqlite3.connect(db_path_to_check); cur = conn.cursor()
        cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='users'")
        if not cur.fetchone(): conn.close(); return True
        cur.execute("SELECT COUNT(*) FROM users")
        if cur.fetchone()[0] == 0: conn.close(); return True
        conn.close(); return False
    except Exception as e: print(f"DB空チェックエラー: {e}"); return True

def backup_database_to_github():
    if not all([GITHUB_TOKEN, GITHUB_USERNAME, GITHUB_REPOSITORY]):
        print("GitHub認証情報不足のためバックアップスキップ。"); return
    if not os.path.exists(DATABASE_PATH) or is_db_empty(DATABASE_PATH):
        print(f"DBファイル {DATABASE_PATH} 無しか空のためバックアップスキップ。"); return
    print("GitHubへのDBバックアップ開始..."); ensure_db_directory_exists(DATABASE_PATH)
    ts = datetime.now(TIMEZONE).strftime('%Y-%m-%d_%H-%M-%S'); backup_file = f"backup_{ts}_{DATABASE_FILE}"
    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            repo_url = f"https://x-access-token:{GITHUB_TOKEN}@github.com/{GITHUB_USERNAME}/{GITHUB_REPOSITORY}.git"
            try:
                subprocess.run(["git","clone","--branch","db-backup","--single-branch",repo_url,tmpdir], check=True,capture_output=True,text=True)
                print(f"db-backupブランチを {tmpdir} にクローン。")
            except subprocess.CalledProcessError as e_clone:
                err_out = e_clone.stderr.decode(errors='ignore') if e_clone.stderr else (e_clone.stdout.decode(errors='ignore') if e_clone.stdout else "")
                if any(m in err_out.lower() for m in ["couldn't find remote ref db-backup","could not checkout","does not exist"]):
                    print("db-backupブランチ無。リポジトリクローンし新規作成/チェックアウト。")
                    subprocess.run(["git","clone",repo_url,tmpdir],check=True,capture_output=True,text=True)
                    subprocess.run(["git","checkout","-B","db-backup"],cwd=tmpdir,check=True)
                else: print(f"db-backupクローン中予期せぬエラー: {err_out}"); raise
            subprocess.run(['git','config','user.email',GIT_USER_EMAIL],cwd=tmpdir,check=True)
            subprocess.run(['git','config','user.name',GIT_USER_NAME],cwd=tmpdir,check=True)
            dest_tmp = os.path.join(tmpdir,backup_file); shutil.copy2(DATABASE_PATH,dest_tmp)
            print(f"DBを {dest_tmp} にコピー。")
            with open(os.path.join(tmpdir,".gitignore"),"w") as f_git: f_git.write(f"{DATABASE_FILE}\n*{DATABASE_FILE}\n!backup_*{DATABASE_FILE}\n")
            subprocess.run(["git","add",".gitignore"],cwd=tmpdir,check=True)
            subprocess.run(["git","add",backup_file],cwd=tmpdir,check=True)
            if subprocess.run(["git","status","--porcelain"],cwd=tmpdir,capture_output=True,text=True).stdout:
                subprocess.run(["git","commit","-m",f"Auto backup: {backup_file}"],cwd=tmpdir,check=True)
                subprocess.run(["git","push","origin","db-backup"],cwd=tmpdir,check=True)
                print(f"バックアップ {backup_file} をGitHubにプッシュ。")
            else: print("コミット変更なし。プッシュスキップ。")
    except subprocess.CalledProcessError as e_proc: print(f"バックアップ中Gitエラー: {e_proc.cmd} ({e_proc.returncode})\nOutput:{(e_proc.stderr or e_proc.stdout or b'').decode(errors='ignore')}")
    except FileNotFoundError as e_fnf: print(f"バックアップ中ファイル/コマンド未検出エラー: {e_fnf}")
    except Exception as e_gen: print(f"バックアップ中予期せぬエラー: {str(e_gen)}")

def restore_database_from_github():
    if not all([GITHUB_TOKEN, GITHUB_USERNAME, GITHUB_REPOSITORY]):
        print("GitHub認証情報不足のためリストアスキップ。"); return False
    print("GitHubからのDBリストア開始..."); ensure_db_directory_exists(DATABASE_PATH)
    with tempfile.TemporaryDirectory() as tmpdir:
        try:
            repo_url = f"https://x-access-token:{GITHUB_TOKEN}@github.com/{GITHUB_USERNAME}/{GITHUB_REPOSITORY}.git"
            subprocess.run(["git","clone","--branch","db-backup","--single-branch","--depth","1",repo_url,tmpdir],check=True,capture_output=True,text=True)
            print(f"リストア用リポジトリ(db-backup)を {tmpdir} にクローン。")
            backups = sorted([f for f in os.listdir(tmpdir) if f.startswith("backup_") and f.endswith(DATABASE_FILE)],reverse=True)
            if not backups: print(f"GitHubリポジトリに 'backup_*{DATABASE_FILE}' ファイル見つからず。"); return False
            latest_backup = backups[0]; latest_path = os.path.join(tmpdir,latest_backup)
            print(f"最新バックアップファイル: {latest_backup}")
            if os.path.exists(DATABASE_PATH): print(f"既存DBファイル {DATABASE_PATH} を上書き。")
            shutil.copy2(latest_path,DATABASE_PATH)
            print(f"{latest_backup} からDBを {DATABASE_PATH} にリストア。"); return True
        except subprocess.CalledProcessError as e_proc:
            err_out = (e_proc.stderr or e_proc.stdout or b'').decode(errors='ignore')
            if any(m in err_out.lower() for m in ["couldn't find remote ref db-backup","does not exist"]): print("db-backupブランチ見つからず。リストア不可。")
            else: print(f"リストア中Gitエラー: {e_proc.cmd} ({e_proc.returncode})\nOutput:{err_out}")
            return False
        except FileNotFoundError as e_fnf: print(f"リストア中ファイル/コマンド未検出エラー: {e_fnf}"); return False
        except Exception as e_gen: print(f"DBリストア中予期せぬエラー: {str(e_gen)}"); return False

def get_db_connection():
    if DATABASE_URL and DATABASE_URL.startswith("postgres"):
        try: return psycopg2.connect(DATABASE_URL)
        except psycopg2.OperationalError as e: print(f"PostgreSQL接続エラー: {e}。SQLiteへフォールバック。")
    ensure_db_directory_exists(DATABASE_PATH); conn = sqlite3.connect(DATABASE_PATH)
    conn.row_factory = sqlite3.Row; conn.execute("PRAGMA foreign_keys = ON"); return conn

def hash_password(p): return hashlib.sha256(p.encode('utf-8')).hexdigest()

def init_db():
    print("DB初期化 (テーブル作成・更新)...")
    with get_db_connection() as conn:
        cur = conn.cursor()
        cur.execute("CREATE TABLE IF NOT EXISTS users (id INTEGER PRIMARY KEY AUTOINCREMENT,username TEXT UNIQUE NOT NULL,password TEXT NOT NULL,is_admin BOOLEAN DEFAULT 0,is_private BOOLEAN DEFAULT 0,created_at DATETIME DEFAULT CURRENT_TIMESTAMP)")
        cur.execute("CREATE TABLE IF NOT EXISTS records (id INTEGER PRIMARY KEY AUTOINCREMENT,user_id INTEGER NOT NULL,action TEXT NOT NULL,timestamp TEXT NOT NULL,memo TEXT,is_deleted BOOLEAN DEFAULT 0,likes_count INTEGER DEFAULT 0,is_private BOOLEAN DEFAULT 0,FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE)")
        cur.execute("CREATE TABLE IF NOT EXISTS likes (id INTEGER PRIMARY KEY AUTOINCREMENT,user_id INTEGER NOT NULL,record_id INTEGER NOT NULL,timestamp DATETIME NOT NULL,FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE,FOREIGN KEY(record_id) REFERENCES records(id) ON DELETE CASCADE,UNIQUE(user_id,record_id))")
        for tbl,cols in {'users':[('is_private','BOOLEAN DEFAULT 0')],'records':[('likes_count','INTEGER DEFAULT 0'),('is_private','BOOLEAN DEFAULT 0')]}.items():
            cur.execute(f"PRAGMA table_info({tbl})"); ex_cols = [r['name'] for r in cur.fetchall()]
            for col_n,col_d in cols:
                if col_n not in ex_cols:
                    try: cur.execute(f"ALTER TABLE {tbl} ADD COLUMN {col_n} {col_d}"); print(f"テーブル'{tbl}'にカラム'{col_n}'追加。")
                    except sqlite3.OperationalError as e_alt: print(f"カラム'{col_n}'テーブル'{tbl}'に追加中エラー: {e_alt}" if 'duplicate column name' not in str(e_alt).lower() else f"カラム'{col_n}'はテーブル'{tbl}'に既に存在。")
        conn.commit()
    print("DB初期化/スキーマ確認完了。")

def create_admin_user():
    print("管理者ユーザー確認・作成...")
    with get_db_connection() as conn:
        cur=conn.cursor(); admin_u,admin_p='admin','admin'
        cur.execute('SELECT id FROM users WHERE username=?',(admin_u,))
        if not cur.fetchone(): cur.execute('INSERT INTO users (username,password,is_admin) VALUES (?,?,?)',(admin_u,hash_password(admin_p),1)); conn.commit(); print(f"管理者'{admin_u}'作成。")
        else: print(f"管理者'{admin_u}'は既に存在。")

def initialize_database_and_restore_if_needed():
    print(f"DBパス: {DATABASE_PATH}"); setup_new = True
    if os.path.exists(DATABASE_PATH) and os.path.getsize(DATABASE_PATH)>0 and not is_db_empty(DATABASE_PATH): print(f"既存DB {DATABASE_PATH} 使用。"); setup_new=False
    else:
        print(f"DBファイル {DATABASE_PATH} は" + ("存在も空か不完全。" if os.path.exists(DATABASE_PATH) else "存在せず。"))
        print("GitHubからのリストア試行..."); setup_new = not restore_database_from_github()
        if not setup_new: print("GitHubからのDBリストア成功。")
        else: print("GitHubからのリストア失敗またはバックアップ無。")
    if setup_new: print("新規DB作成。")
    init_db(); create_admin_user()

with app.app_context(): initialize_database_and_restore_if_needed()

def login_required(f):
    @wraps(f)
    def dec(*args,**kwargs):
        if 'user_id' not in session: flash('ログインが必要です。','error'); return redirect(url_for('login'))
        return f(*args,**kwargs)
    return dec

def admin_required(f):
    @wraps(f)
    def dec(*args,**kwargs):
        if 'user_id' not in session or not session.get('is_admin'): flash('管理者権限が必要です。','error'); return redirect(url_for('index'))
        return f(*args,**kwargs)
    return dec

def jst_now(): return datetime.now(TIMEZONE)
def eval_sleep(d): r=d/7.0; return "寝すぎ⁉" if r>=1.5 else "ちょい寝すぎか。" if 1.286<=r<1.5 else "良好だよ☻" if 1.0<=r<1.286 else "もう少し寝てほしいかも..." if 0.858<=r<1.0 else "頼むこれ以上は..." if 0.50<=r<0.858 else "いい加減もっと寝ろよ！！"
def rnd_dec(v,p=1): return Decimal(str(v)).quantize(Decimal('0.1')**p,rounding=ROUND_HALF_UP) if v is not None else None

def get_s_times(uid):
    with get_db_connection() as c: raw=c.execute("SELECT action,timestamp FROM records WHERE user_id=? AND is_deleted=0 AND action IN ('sleep','wake_up') ORDER BY timestamp ASC",(uid,)).fetchall()
    times,start=[],None
    for r in raw:
        t=datetime.fromisoformat(r['timestamp'])
        if r['action']=='sleep':start=t
        elif r['action']=='wake_up' and start:
            dur_s=(t-start).total_seconds()
            if dur_s>0: dur_h=dur_s/3600; d_obj=t.date(); times.append({'date':d_obj,'duration':dur_h,'hours':int(dur_h),'minutes':int((dur_h-int(dur_h))*60),'week':d_obj.isocalendar()[1],'month':d_obj.month,'year':d_obj.year,'timestamp':t})
            start=None
    return times

def calc_avg_generic(s_list):
    if not s_list: return {'avg_hours':0,'avg_minutes':0,'evaluation':"-",'avg_duration':0.0,'record_days':0}
    durs=[i['duration'] for i in s_list]; days=len(durs); avg_d=sum(durs)/days if days>0 else 0
    return {'avg_hours':int(avg_d),'avg_minutes':int((avg_d-int(avg_d))*60),'evaluation':eval_sleep(avg_d) if days>=3 else "-",'avg_duration':avg_d,'record_days':days}

def group_by_period_generic(s_times,key_f,name_f_detailed):
    if not s_times: return []
    grouped={}; [grouped.setdefault(key_f(i),[]).append(i['duration']) for i in s_times]
    avgs=[{**calc_avg_generic([{'duration':d} for d in durs]),'period':name_f_detailed(k)[0],'start_date':name_f_detailed(k)[1]} for k,durs in grouped.items()]
    avgs.sort(key=lambda x:x['start_date'],reverse=True); return avgs

def weekly_period_name_and_date(k_val):
    year_str, week_num_str = k_val.split('-W')
    start_date_obj = datetime.strptime(f"{year_str}-W{week_num_str}-1", "%Y-W%W-%w").date()
    end_date_obj = start_date_obj + timedelta(days=6)
    period_str = f"{start_date_obj.strftime('%Y/%m/%d')}～{end_date_obj.strftime('%Y/%m/%d')}"
    return period_str, start_date_obj

def calc_weekly_avg(s_times):
    kf=lambda i:f"{i['year']}-W{i['week']:02d}"
    return group_by_period_generic(s_times,kf,weekly_period_name_and_date)

def calc_monthly_avg(s_times):
    kf=lambda i:f"{i['year']}-{i['month']:02d}"
    def monthly_period_name_and_date(k_val):
        year_str, month_str = k_val.split('-')
        year_int, month_int = int(year_str), int(month_str)
        period_str = f"{year_str}年{month_int}月"
        start_date_obj = datetime(year_int, month_int, 1).date()
        return period_str, start_date_obj
    return group_by_period_generic(s_times,kf,monthly_period_name_and_date)

def calc_s_comps(s_times):
    if not s_times: return {'yesterday':None,'last_week':None,'last_month':None}
    s_sort=sorted(s_times,key=lambda x:x['date'],reverse=True); latest=s_sort[0]; today=latest['date']
    def df_obj(c,p):
        if p is None:return None
        d=c-p;inc=d>0;d_abs=abs(d);return {'diff_hours':int(d_abs),'diff_minutes':int((d_abs-int(d_abs))*60),'is_increase':inc}
    yest_s=s_sort[1]['duration'] if len(s_sort)>1 else None
    lw_s=next((s['duration'] for s in s_sort[1:] if (today-s['date']).days>=7 and today.weekday()==s['date'].weekday()),None)
    lm_s=next((s['duration'] for s in s_sort[1:] if s['date'].day==today.day and ((today.month==1 and s['date'].month==12 and s['date'].year==today.year-1)or(s['date'].month==today.month-1 and s['date'].year==today.year))),None)
    return {'yesterday':df_obj(latest['duration'],yest_s),'last_week':df_obj(latest['duration'],lw_s),'last_month':df_obj(latest['duration'],lm_s)}

@app.route('/', methods=['GET'])
@login_required
def index():
    pg,pp,off=request.args.get('page',1,type=int),10,0; off=(pg-1)*pp
    msg=session.pop(f"user_{session['user_id']}_message",None)
    with get_db_connection() as conn:
        s_jst=TIMEZONE.localize(datetime.combine(jst_now().date(),datetime.min.time())); e_jst=s_jst+timedelta(days=1)
        s_utc,e_utc=s_jst.astimezone(pytz.utc).isoformat(),e_jst.astimezone(pytz.utc).isoformat()
        total=conn.execute("SELECT COUNT(*) FROM records WHERE user_id=? AND is_deleted=0 AND timestamp>=? AND timestamp < ?",(session['user_id'],s_utc,e_utc)).fetchone()[0]
        raw=conn.execute("SELECT id,action,memo,likes_count,timestamp,is_private FROM records WHERE user_id=? AND is_deleted=0 AND timestamp>=? AND timestamp < ? ORDER BY timestamp DESC LIMIT ? OFFSET ?",(session['user_id'],s_utc,e_utc,pp,off)).fetchall()
        recs=[{**dict(r),'formatted_date':datetime.fromisoformat(r['timestamp']).astimezone(TIMEZONE).strftime('%Y-%m-%d'),'formatted_time':datetime.fromisoformat(r['timestamp']).astimezone(TIMEZONE).strftime('%H:%M:%S')} for r in raw]
        total_pg=(total+pp-1)//pp
    return render_template("index.html",records=recs,is_private=session.get('is_private',False),user_message=msg,page=pg,total_pages=total_pg)

@app.route('/like_record/<int:record_id>', methods=['POST'])
@login_required
def like_record(record_id):
    with get_db_connection() as conn:
        try:
            if conn.execute('SELECT id FROM likes WHERE user_id=? AND record_id=?',(session['user_id'],record_id)).fetchone(): flash('すでにいいね済みです。','info')
            else: conn.execute('BEGIN'); conn.execute('INSERT INTO likes (user_id,record_id,timestamp) VALUES (?,?,?)',(session['user_id'],record_id,jst_now().isoformat())); conn.execute('UPDATE records SET likes_count=likes_count+1 WHERE id=?',(record_id,)); conn.commit(); flash('いいねしました！','success'); backup_database_to_github()
        except Exception as e: conn.rollback(); flash(f'エラー: {e}','danger')
    return redirect(request.form.get('redirect_to') or request.referrer or url_for('index'))

@app.route('/calendar', methods=['GET'])
@login_required
def calendar_view():
    now=jst_now();y,m=request.args.get('year',now.year,type=int),request.args.get('month',now.month,type=int);cal=calendar.Calendar(6).monthdayscalendar(y,m)
    prev_d,next_d=(datetime(y,m,1)-timedelta(days=1)),(datetime(y,m,1)+timedelta(days=calendar.monthrange(y,m)[1]))
    return render_template('calendar.html',year=y,month=m,cal_data=cal,prev_year=prev_d.year,prev_month=prev_d.month,next_year=next_d.year,next_month=next_d.month,today=now.date())

@app.route('/login', methods=['GET','POST'])
def login():
    if request.method=='POST':
        u,p=request.form.get('username','').strip(),request.form.get('password','')
        if not u or not p:flash('ユーザー名とパスワードを入力してください。','error');return render_template('login.html')
        with get_db_connection() as c:user=c.execute('SELECT * FROM users WHERE username=?',(u,)).fetchone()
        if user and user['password']==hash_password(p):
            session.clear();session.permanent=True;session['user_id']=user['id'];session['username']=user['username'];session['is_admin']=bool(user['is_admin']);session['is_private']=bool(user['is_private'])
            flash('おかえりなさい！','success');return redirect(url_for('admin_dashboard' if session['is_admin'] else 'index'))
        else:flash('ユーザー名またはパスワードが間違い。','error')
    return render_template('login.html')

@app.route('/register', methods=['GET','POST'])
def register():
    if request.method=='POST':
        u,p,is_priv=request.form.get('username','').strip(),request.form.get('password',''),request.form.get('is_private')=='on'
        if not u or not p:flash('ユーザー名とパスワードを入力してください。','error');return render_template('register.html')
        with get_db_connection() as c:
            try:c.execute('INSERT INTO users(username,password,is_private,created_at)VALUES(?,?,?,?)',(u,hash_password(p),int(is_priv),jst_now().isoformat()));c.commit();flash('登録しました！ログインしてください。','success');backup_database_to_github();return redirect(url_for('login'))
            except sqlite3.IntegrityError:c.rollback();flash('このユーザー名は既に存在しています。','error')
            except sqlite3.Error as e:c.rollback();flash(f'登録中DBエラー:{e}','error')
    return render_template('register.html')

@app.route('/sleep_graph')
@login_required
def sleep_graph():return render_template('sleep_graph.html',period=request.args.get('period','daily'))

@app.route('/reset_password', methods=['GET','POST'])
def reset_password():
    if request.method=='POST':
        u,np=request.form.get('username','').strip(),request.form.get('new_password','')
        if not u or not np:flash('ユーザー名と新パスワード入力。','error');return render_template('reset_password.html')
        with get_db_connection() as c:
            if c.execute('SELECT id FROM users WHERE username=?',(u,)).fetchone():c.execute('UPDATE users SET password=? WHERE username=?',(hash_password(np),u));c.commit();flash('パスワードを更新しました。ログインしてください。','success');backup_database_to_github();return redirect(url_for('login'))
            else:flash('指定ユーザー名が見つかりません。','error')
    return render_template('reset_password.html')

@app.route('/logout')
@login_required
def logout():session.clear();flash('ログアウトしました。','info');return redirect(url_for('login'))

@app.route('/record',methods=['POST'])
@login_required
def record_action():
    act,mem,is_priv=request.form.get('action'),request.form.get('memo',''),request.form.get('is_private_record')=='on'
    if not act or act not in ['wake_up','sleep']:flash('有効な行動を選択。','danger');return redirect(url_for('index'))
    ts_save=jst_now().isoformat()
    with get_db_connection() as c:
        try:
            s_jst=TIMEZONE.localize(datetime.combine(jst_now().date(),datetime.min.time()));e_jst=s_jst+timedelta(days=1)
            s_utc,e_utc=s_jst.astimezone(pytz.utc).isoformat(),e_jst.astimezone(pytz.utc).isoformat()
            count=c.execute("SELECT COUNT(*)FROM records WHERE user_id=? AND action=? AND timestamp>=? AND timestamp < ? AND is_deleted=0",(session['user_id'],act,s_utc,e_utc)).fetchone()[0]
            if(act=='sleep' and count>=2)or(act=='wake_up' and count>=1):flash(f'本日{count+1}回目「{act}」記録できません。','warning');return redirect(url_for('index'))
            c.execute('INSERT INTO records(user_id,action,timestamp,memo,is_private)VALUES(?,?,?,?,?)',(session['user_id'],act,ts_save,mem,int(is_priv)));c.commit();flash('記録を保存しました。','success');backup_database_to_github()
        except sqlite3.Error as e:c.rollback();flash(f'DBエラー:{e}','danger');app.logger.error(f"Record DB error:{e}")
    return redirect(url_for('index'))

@app.route('/average_sleep')
@login_required
def average_sleep_view():
    p,uid=request.args.get('period','daily'),session['user_id'];s_times=get_s_times(uid)
    if not s_times:return render_template('average_sleep.html',has_records=False,period=p,evaluate_sleep=eval_sleep,round_decimal=rnd_dec)
    overall,weekly,monthly,comps=calc_avg_generic(s_times),calc_weekly_avg(s_times),calc_monthly_avg(s_times),calc_s_comps(s_times)
    s_times.sort(key=lambda x:x['date'],reverse=True)
    return render_template('average_sleep.html',has_records=True,sleep_times=s_times,overall_avg=overall,weekly_avg=weekly,monthly_avg=monthly,comparisons=comps,period=p,evaluate_sleep=eval_sleep,round_decimal=rnd_dec)

@app.route('/day_records/<date>')
@login_required
def day_records(date):
    try:p_date=datetime.strptime(date,'%Y-%m-%d').date()
    except ValueError:flash('無効な日付形式。YYYY-MM-DDで。','error');return redirect(url_for('calendar_view'))
    uid,is_adm=session['user_id'],session.get('is_admin',False);s_data=get_s_times(uid);s_info=next((s for s in s_data if s['date']==p_date),None)
    s_h,s_m,s_eval=(s_info['hours'] if s_info else None),(s_info['minutes'] if s_info else None),(eval_sleep(s_info['duration']) if s_info else None)
    with get_db_connection() as c:
        s_jst=TIMEZONE.localize(datetime.combine(p_date,datetime.min.time()));e_jst=s_jst+timedelta(days=1)
        s_utc,e_utc=s_jst.astimezone(pytz.utc).isoformat(),e_jst.astimezone(pytz.utc).isoformat()
        q="SELECT r.*,u.username FROM records r JOIN users u ON r.user_id=u.id WHERE "
        params_list = []
        if is_adm:
            q+="r.timestamp>=? AND r.timestamp < ? ORDER BY u.username ASC,r.timestamp ASC"
            params_list.extend([s_utc,e_utc])
        else:
            q+="r.user_id=? AND r.timestamp>=? AND r.timestamp < ? ORDER BY r.timestamp ASC"
            params_list.extend([uid,s_utc,e_utc])
        raw=c.execute(q,tuple(params_list)).fetchall()
        recs=[{**dict(r),'formatted_time':datetime.fromisoformat(r['timestamp']).astimezone(TIMEZONE).strftime('%H:%M:%S'),'formatted_date':datetime.fromisoformat(r['timestamp']).astimezone(TIMEZONE).strftime('%Y-%m-%d')} for r in raw]
    return render_template('day_records.html',date=p_date.strftime('%Y-%m-%d'),records=recs,is_admin=is_adm,sleep_duration_hours=s_h,sleep_duration_minutes=s_m,sleep_evaluation=s_eval,year=p_date.year,month=p_date.month)

@app.route('/all_records')
@login_required
def all_records():
    pg,pp,off=request.args.get("page",1,type=int),20,0;off=(pg-1)*pp;u_fid_s=request.args.get("user_id","all")
    with get_db_connection() as c:
        users=c.execute("SELECT id,username FROM users WHERE is_admin=0 AND is_private=0 ORDER BY username").fetchall()
        q_base="FROM records r JOIN users u ON r.user_id=u.id WHERE r.is_deleted=0 AND u.is_private=0 AND u.is_admin=0"
        p_cnt,p_data=[],[]
        if u_fid_s!="all" and u_fid_s.isdigit():u_fid=int(u_fid_s);q_base+=" AND r.user_id=?";p_cnt.append(u_fid);p_data.append(u_fid)
        total=c.execute(f"SELECT COUNT(r.id) {q_base}",tuple(p_cnt)).fetchone()[0];p_data.extend([pp,off])
        raw=c.execute(f"SELECT r.*,u.username,u.id as author_uid {q_base} ORDER BY r.timestamp DESC LIMIT ? OFFSET ?",tuple(p_data)).fetchall()
        liked={r['record_id'] for r in c.execute("SELECT record_id FROM likes WHERE user_id=?",(session['user_id'],)).fetchall()}
        fmt=[{**dict(r),'formatted_date':datetime.fromisoformat(r['timestamp']).astimezone(TIMEZONE).strftime('%Y-%m-%d'),'formatted_time':datetime.fromisoformat(r['timestamp']).astimezone(TIMEZONE).strftime('%H:%M:%S'),'liked':(r['id']in liked)} for r in raw]
        total_pg=(total+pp-1)//pp
    return render_template("all_records.html",records=fmt,users=users,current_user_filter=u_fid_s,page=pg,total_pages=total_pg)

@app.route('/toggle_privacy',methods=['POST'])
@login_required
def toggle_privacy():
    new_p=request.form.get('is_private')=='on'
    with get_db_connection() as c:
        try:c.execute('UPDATE users SET is_private=? WHERE id=?',(int(new_p),session['user_id']));c.commit();session['is_private']=new_p;flash(f"プライバシー設定「{'プライベート'if new_p else'パブリック'}」更新。",'success');backup_database_to_github()
        except sqlite3.Error as e:flash(f'プライバシー設定更新中エラー:{e}','error')
    return redirect(url_for('index'))

@app.route('/delete_record/<int:record_id>', methods=['POST'])
@login_required
def delete_record(record_id):
    r_to,d_r,pg_n,u_f=request.form.get('redirect_to','index'),request.form.get('date_val'),request.form.get('page_num','1'),request.form.get('user_filter_val','all')
    with get_db_connection() as c:
        owner=c.execute('SELECT user_id FROM records WHERE id=? AND is_deleted=0',(record_id,)).fetchone()
        if not owner or(owner['user_id']!=session['user_id'] and not session.get('is_admin')):flash('削除権限がありません。','error')
        else:
            try:c.execute('BEGIN');c.execute('DELETE FROM likes WHERE record_id=?',(record_id,));c.execute('UPDATE records SET is_deleted=1 WHERE id=?',(record_id,));c.commit();flash('記録を削除しました。','success');backup_database_to_github()
            except sqlite3.Error as e:c.rollback();flash(f'記録削除中エラー:{e}','error')
    if r_to=='all_records':return redirect(url_for('all_records',page=pg_n,user_id=u_f))
    if r_to=='day_records' and d_r:return redirect(url_for('day_records',date=d_r))
    return redirect(url_for('index',page=pg_n))

@app.route('/admin_dashboard')
@admin_required
def admin_dashboard():
    pg,pp,off=request.args.get('page',1,type=int),10,0;off=(pg-1)*pp
    with get_db_connection() as c:
        users=c.execute("SELECT id,username,created_at,is_private FROM users WHERE is_admin=0 ORDER BY created_at DESC").fetchall()
        total=c.execute('SELECT COUNT(*)FROM records WHERE is_deleted=0').fetchone()[0]
        raw=c.execute("SELECT r.*,u.username,u.is_private as u_priv FROM records r JOIN users u ON r.user_id=u.id WHERE r.is_deleted=0 ORDER BY r.timestamp DESC LIMIT ? OFFSET ?",(pp,off)).fetchall()
        recs=[{**dict(r),'formatted_time':datetime.fromisoformat(r['timestamp']).astimezone(TIMEZONE).strftime('%Y-%m-%d %H:%M:%S')} for r in raw]
        total_pg=(total+pp-1)//pp
    return render_template("admin_dashboard.html",users=users,records=recs,page=pg,total_pages=total_pg)

@app.route('/admin/user_records/<int:user_id>')
@admin_required
def admin_user_records(user_id):
    pg,pp,off=request.args.get('page',1,type=int),20,0;off=(pg-1)*pp
    with get_db_connection() as c:
        user=c.execute('SELECT id,username FROM users WHERE id=?',(user_id,)).fetchone()
        if not user:flash('ユーザーが見つかりません。','error');return redirect(url_for('admin_dashboard'))
        total_u=c.execute('SELECT COUNT(*)FROM records WHERE user_id=? AND is_deleted=0',(user_id,)).fetchone()[0]
        raw_u=c.execute("SELECT * FROM records WHERE user_id=? AND is_deleted=0 ORDER BY timestamp DESC LIMIT ? OFFSET ?",(user_id,pp,off)).fetchall()
        u_recs=[{**dict(r),'formatted_time':datetime.fromisoformat(r['timestamp']).astimezone(TIMEZONE).strftime('%Y-%m-%d %H:%M:%S')} for r in raw_u]
        total_pg=(total_u+pp-1)//pp
    return render_template('admin_user_records.html',user=user,records=u_recs,page=pg,total_pages=total_pg)

@app.route('/admin/add_record',methods=['GET','POST'])
@admin_required
def admin_add_record():
    if request.method=='POST':
        uid,act,mem,ts_s,is_p=request.form.get('user_id'),request.form.get('action'),request.form.get('memo',''),request.form.get('timestamp'),request.form.get('is_private_record')=='on'
        if not uid or not act or not ts_s:flash('ユーザー,アクション,タイムスタンプ必須。','danger')
        else:
            try:
                dt_n=datetime.strptime(ts_s,'%Y-%m-%dT%H:%M');dt_j=TIMEZONE.localize(dt_n);ts_save=dt_j.isoformat()
                with get_db_connection() as c:c.execute('INSERT INTO records(user_id,action,timestamp,memo,is_private)VALUES(?,?,?,?,?)',(uid,act,ts_save,mem,int(is_p)));c.commit()
                flash('記録追加。','success');session[f'user_{uid}_message']="管理者が記録追加。";backup_database_to_github();return redirect(url_for('admin_user_records',user_id=uid))
            except ValueError:flash('タイムスタンプ形式無効。YYYY-MM-DDTHH:MMで。','danger')
            except sqlite3.Error as e:flash(f'記録追加中DBエラー:{e}','danger')
    with get_db_connection() as c:users=c.execute('SELECT id,username FROM users WHERE is_admin=0 ORDER BY username').fetchall()
    return render_template('admin_add_record.html',users=users)

@app.route('/admin/delete_record/<int:record_id>', methods=['POST'])
@admin_required
def admin_delete_record(record_id):
    uid_r=None
    with get_db_connection() as c:
        try:
            rec_d=c.execute('SELECT user_id FROM records WHERE id=?',(record_id,)).fetchone()
            if not rec_d:flash('記録が見つかりません。','error');return redirect(url_for('admin_dashboard'))
            uid_r=rec_d['user_id']
            c.execute('BEGIN');c.execute('DELETE FROM likes WHERE record_id=?',(record_id,));c.execute('DELETE FROM records WHERE id=?',(record_id,));c.commit()
            flash('記録完全削除。','success');backup_database_to_github()
        except sqlite3.Error as e:c.rollback();flash(f'記録削除中エラー:{e}','danger')
    if uid_r:return redirect(url_for('admin_user_records',user_id=uid_r))
    return redirect(url_for('admin_dashboard'))

@app.route('/delete_user/<int:user_id>', methods=['POST'])
@admin_required
def delete_user(user_id):
    if user_id==session.get('user_id'):flash('自分自身削除不可。','error');return redirect(url_for('admin_dashboard'))
    with get_db_connection() as c:
        u_d=c.execute('SELECT username,is_admin FROM users WHERE id=?',(user_id,)).fetchone()
        if not u_d:flash('削除対象ユーザー見つからず。','error')
        elif u_d['is_admin']:flash('管理者アカウント削除不可。','error')
        else:
            try:c.execute('DELETE FROM users WHERE id=?',(user_id,));c.commit();flash(f"ユーザー'{u_d['username']}'と関連データ削除。","success");backup_database_to_github()
            except sqlite3.Error as e:c.rollback();flash(f"ユーザー削除中エラー:{e}","error")
    return redirect(url_for('admin_dashboard'))

@app.route('/api/sleep_data')
@login_required
def api_sleep_data():
    p,uid,all_s,data=request.args.get('period','daily'),session['user_id'],get_s_times(uid),[]
    if p=='daily':data=[{'date':s['date'].isoformat(),'duration':s['duration']} for s in all_s];data.sort(key=lambda x:x['date'])
    elif p=='weekly':w_avg=calc_weekly_avg(all_s);data=[{'period_start':w['start_date'].isoformat(),'avg_duration':w['avg_duration']} for w in w_avg];data.sort(key=lambda x:x['period_start'])
    elif p=='monthly':m_avg=calc_monthly_avg(all_s);data=[{'period_start':m['start_date'].isoformat(),'avg_duration':m['avg_duration']} for m in m_avg];data.sort(key=lambda x:x['period_start'])
    else:return jsonify({'error':'Invalid period'}),400
    return jsonify(data)

@app.route('/download_db')
@admin_required
def download_db_file():
    ensure_db_directory_exists(DATABASE_PATH)
    if os.path.exists(DATABASE_PATH):return send_file(DATABASE_PATH,as_attachment=True,download_name=DATABASE_FILE)
    else:flash("DBファイル見つかりません。","error");return redirect(url_for('admin_dashboard'))

if __name__=='__main__':
    port=int(os.environ.get('PORT',5000))
    debug_mode=os.environ.get('FLASK_DEBUG','0').lower()in['1','true']
    app.run(host='0.0.0.0',port=port,debug=debug_mode)