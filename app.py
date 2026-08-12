import os
import sqlite3
from datetime import datetime
from functools import wraps
from pathlib import Path
from flask import Flask, jsonify, redirect, render_template, request, session, url_for

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'change-this-in-render')
DATA_DIR = Path(os.environ.get('DATA_DIR', './data'))
DATA_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH = DATA_DIR / 'tasks.db'
APP_PASSWORD = os.environ.get('APP_PASSWORD', '')

SEED_TASKS = [
    ('payment','BB Direct','Pay overdue BB Direct invoice — $1,223.37','2026-06-30','urgent','Open','Invoice is substantially past due.',''),
    ('client','L. Grace Brands / MCN','Provide MCN impression, click and traffic verification','2026-08-01','urgent','Open','Confirm impression and click totals, traffic quality, source providers and corrected UTM / redirect routing.',''),
    ('client','Pillar Media / Dan Watts','Resolve reporting and campaign delivery issues','2026-08-05','urgent','Open','Provide the detailed reporting, pacing and campaign-delivery update previously promised.',''),
    ('payment','The Trade Desk','Send funding documents and close AR follow-up','2026-08-06','urgent','Open','Funding documents and AR follow-up remain open.',''),
    ('payment','WCMH / Nexstar','Resolve remaining Ohio RV & Boat Show balance','2026-08-07','urgent','Open','Verify current balance after partial payment and finish remaining amount.',''),
    ('payment','Extend / Advertising Platforms','Verify advertising card declines are fully resolved','2026-08-07','urgent','Open','Confirm funding and payment methods so campaigns are not interrupted.',''),
    ('payment','HighLevel / Smart 1 Suite','Fix billing card issue blocking WordPress hosting setup','2026-08-11','urgent','Open','Verify the billing issue is fully resolved.',''),
    ('payment','Capitalize Group','Resolve settlement balance shown as $33,165.32','2026-08-11','urgent','Open','Reconcile settlement balance and payment proof.',''),
    ('client','TrimGlow','Set up TrimGlow Google Business Profile (GMB)','2026-08-12','urgent','Open','Google Business Profile setup is due now.',''),
    ('payment','TriNet Payroll Funding','Fund Aug. 14 payroll wires — $55,910.22 total','2026-08-14','urgent','Open','Payroll funding required for Aug. 14 check dates.',''),
    ('payment','Erie Insurance / Haughn','Pay $3,132.94 manually and restore Auto-Pay','2026-08-21','urgent','Open','Payment required to avoid policy cancellation.',''),
    ('client','Miracle Motor Mart','Complete Google Ads transition before Dealer.com pause','2026-09-01','high','Working','Confirm access, campaign build and clean Smart 1 takeover.',''),
    ('client','L. Grace Brands','Apply $11,905.72 July underdelivery credit to August invoice','','urgent','Open','Apply requested underdelivery credit to the August invoice.',''),
    ('client',"Schmidt's",'Confirm Marketing Scorecard is loaded into Smart 1 Suite','','high','Open','Confirm the marketing scorecard is loaded and ready.',''),
    ('client','NC / Erik Accounts','Resolve July underperformance and manage August makegoods','','urgent','Working','Manage makegoods and invoice review follow-up.',''),
    ('client',"Schmidt's",'Finish menu website update and confirm completion','','high','Open','Confirm menu update is live and notify client.',''),
    ('client','MSU-Northern / New Media Broadcasters','Send 2025–26 campaign statistics','','high','Open','Provide campaign statistics or reporting link.',''),
    ('payment','AWS','Verify past-due AWS account is cured','','urgent','Open','Verify balance and payment method are current.',''),
    ('payment','OnDeck','Confirm ACH authorization and account-current status','','high','Waiting','Verify catch-up payment clears and account returns to current.',''),
    ('client','Icon Solar','Set up GPT Ads for Icon Solar','','high','Open','Build and configure the GPT Ads initiative for Icon Solar.',''),
    ('client','Icon Solar','Fix Wanda videos for Icon Solar','','high','Open','Review and correct the Wanda video assets for Icon Solar.',''),
    ('client','Text Doctor','Set up Text Doctor','','normal','Open','Task added manually.',''),
    ('client','Home Loan','Set up Home Loan','','normal','Open','Task added manually.','')
]

def db():
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    con.execute('PRAGMA foreign_keys=ON')
    return con

def init_db():
    with db() as con:
        con.executescript('''
        CREATE TABLE IF NOT EXISTS tasks (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          category TEXT NOT NULL DEFAULT 'client', party TEXT NOT NULL,
          title TEXT NOT NULL, detail TEXT DEFAULT '', due_date TEXT DEFAULT '',
          priority TEXT NOT NULL DEFAULT 'normal', status TEXT NOT NULL DEFAULT 'Open',
          email_url TEXT DEFAULT '', email_to TEXT DEFAULT '', email_subject TEXT DEFAULT '',
          completed INTEGER NOT NULL DEFAULT 0, completed_at TEXT DEFAULT '',
          created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS notes (
          id INTEGER PRIMARY KEY AUTOINCREMENT, task_id INTEGER NOT NULL,
          body TEXT NOT NULL, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          FOREIGN KEY(task_id) REFERENCES tasks(id) ON DELETE CASCADE
        );
        ''')
        if con.execute('SELECT COUNT(*) FROM tasks').fetchone()[0] == 0:
            con.executemany('''INSERT INTO tasks
              (category,party,title,due_date,priority,status,detail,email_url)
              VALUES (?,?,?,?,?,?,?,?)''', SEED_TASKS)
init_db()

def login_required(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if APP_PASSWORD and not session.get('authenticated'):
            if request.path.startswith('/api/'):
                return jsonify({'error':'Unauthorized'}), 401
            return redirect(url_for('login'))
        return fn(*args, **kwargs)
    return wrapper

@app.get('/health')
def health(): return jsonify({'ok':True})

@app.route('/login', methods=['GET','POST'])
def login():
    if not APP_PASSWORD:
        session['authenticated']=True
        return redirect(url_for('index'))
    error=''
    if request.method=='POST':
        if request.form.get('password','') == APP_PASSWORD:
            session['authenticated']=True
            return redirect(url_for('index'))
        error='Incorrect password.'
    return render_template('login.html', error=error)

@app.get('/logout')
def logout():
    session.clear(); return redirect(url_for('login'))

@app.get('/')
@login_required
def index(): return render_template('index.html')

def serialize(row, con):
    item=dict(row)
    item['notes']=[dict(n) for n in con.execute('SELECT id,body,created_at FROM notes WHERE task_id=? ORDER BY id DESC',(row['id'],)).fetchall()]
    return item

@app.get('/api/tasks')
@login_required
def list_tasks():
    completed=1 if request.args.get('completed')=='1' else 0
    with db() as con:
        rows=con.execute('''SELECT * FROM tasks WHERE completed=? ORDER BY
          CASE WHEN due_date='' THEN 1 ELSE 0 END, due_date ASC, party ASC''',(completed,)).fetchall()
        return jsonify([serialize(r,con) for r in rows])

@app.post('/api/tasks')
@login_required
def create_task():
    p=request.get_json(force=True)
    if not p.get('title','').strip(): return jsonify({'error':'Title required'}),400
    with db() as con:
        cur=con.execute('''INSERT INTO tasks
          (category,party,title,detail,due_date,priority,status,email_url,email_to,email_subject)
          VALUES (?,?,?,?,?,?,?,?,?,?)''',(
            p.get('category','client'), p.get('party','Unassigned').strip() or 'Unassigned',
            p.get('title','').strip(), p.get('detail','').strip(), p.get('due_date',''),
            p.get('priority','normal'), p.get('status','Open'), p.get('email_url',''),
            p.get('email_to',''), p.get('email_subject','')
        ))
        row=con.execute('SELECT * FROM tasks WHERE id=?',(cur.lastrowid,)).fetchone()
        return jsonify(serialize(row,con)),201

@app.patch('/api/tasks/<int:task_id>')
@login_required
def update_task(task_id):
    p=request.get_json(force=True)
    allowed={'category','party','title','detail','due_date','priority','status','email_url','email_to','email_subject'}
    fields=[]; values=[]
    for k in allowed:
        if k in p: fields.append(f'{k}=?'); values.append(p[k])
    if not fields: return jsonify({'error':'No changes supplied'}),400
    fields.append('updated_at=CURRENT_TIMESTAMP'); values.append(task_id)
    with db() as con:
        con.execute(f"UPDATE tasks SET {', '.join(fields)} WHERE id=?", values)
        row=con.execute('SELECT * FROM tasks WHERE id=?',(task_id,)).fetchone()
        if not row: return jsonify({'error':'Task not found'}),404
        return jsonify(serialize(row,con))

@app.post('/api/tasks/<int:task_id>/notes')
@login_required
def add_note(task_id):
    body=request.get_json(force=True).get('body','').strip()
    if not body: return jsonify({'error':'Note cannot be blank'}),400
    with db() as con:
        if not con.execute('SELECT id FROM tasks WHERE id=?',(task_id,)).fetchone(): return jsonify({'error':'Task not found'}),404
        con.execute('INSERT INTO notes(task_id,body) VALUES (?,?)',(task_id,body))
        row=con.execute('SELECT * FROM tasks WHERE id=?',(task_id,)).fetchone()
        return jsonify(serialize(row,con))

@app.post('/api/tasks/<int:task_id>/complete')
@login_required
def complete_task(task_id):
    stamp=datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    with db() as con:
        con.execute("UPDATE tasks SET completed=1,completed_at=?,status='Completed',updated_at=CURRENT_TIMESTAMP WHERE id=?",(stamp,task_id))
    return jsonify({'ok':True})

@app.post('/api/tasks/<int:task_id>/restore')
@login_required
def restore_task(task_id):
    with db() as con:
        con.execute("UPDATE tasks SET completed=0,completed_at='',status='Open',updated_at=CURRENT_TIMESTAMP WHERE id=?",(task_id,))
    return jsonify({'ok':True})

@app.delete('/api/tasks/<int:task_id>')
@login_required
def delete_task(task_id):
    with db() as con: con.execute('DELETE FROM tasks WHERE id=?',(task_id,))
    return jsonify({'ok':True})

if __name__=='__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT','10000')), debug=os.environ.get('FLASK_DEBUG')=='1')
