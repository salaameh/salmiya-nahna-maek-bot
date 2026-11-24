import os, time, threading, sqlite3, requests
from functools import wraps
from flask import Flask, request, redirect, url_for, session, render_template_string, flash, abort
from werkzeug.security import generate_password_hash, check_password_hash

APP = Flask(__name__)
APP.secret_key = os.getenv('SECRET_KEY', 'change-this-secret-key')
DB_PATH = os.getenv('DB_PATH', 'data.sqlite3')
BOT_TOKEN = os.getenv('BOT_TOKEN', '')
ADMIN_USER = os.getenv('ADMIN_USER', 'admin')
ADMIN_PASSWORD = os.getenv('ADMIN_PASSWORD', '')
CATEGORIES = ['دليل المحلات','فرص العمل','الإعلانات','عقارات للبيع والإيجار','مناسبات وأخبار','خدمات المدينة']
SERVICE_CATEGORIES = ['أرقام ضرورية','صيدليات المناوبة','المشافي والمراكز الطبية','الخدمات العامة','خدمات الطوارئ']


def tg(method, **data):
    if not BOT_TOKEN: return {}
    try: return requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/{method}", json=data, timeout=35).json()
    except Exception: return {}

def send_message(chat_id, text, keyboard=None):
    payload={"chat_id":chat_id,"text":text}
    if keyboard: payload["reply_markup"]={"keyboard":keyboard,"resize_keyboard":True}
    tg("sendMessage", **payload)

def bot_loop():
    offset=None
    while True:
        try:
            params={"timeout":25}
            if offset is not None: params["offset"]=offset
            result=requests.get(f"https://api.telegram.org/bot{BOT_TOKEN}/getUpdates",params=params,timeout=35).json()
            for update in result.get("result",[]):
                offset=update["update_id"]+1
                msg=update.get("message",{}); chat_id=msg.get("chat",{}).get("id")
                user_id=msg.get("from",{}).get("id",""); text=(msg.get("text") or "").strip()
                if not chat_id: continue
                if text in ("/start","القائمة الرئيسية"):
                    keys=[[{"text":x}] for x in SERVICE_CATEGORIES] + [[{"text":x}] for x in CATEGORIES]
                    send_message(chat_id,"أهلاً بك في بوت سلمية نحنا معك 🌿\nاختر من الدليل الخدمي أو الأقسام التالية:",keys)
                elif text in SERVICE_CATEGORIES:
                    with db() as c: items=c.execute('SELECT * FROM service_entries WHERE category=? AND active=1 ORDER BY name',(text,)).fetchall()
                    if not items:
                        send_message(chat_id, f"لا توجد بيانات منشورة حالياً في قسم {text}. نعمل على تحديث الدليل باستمرار.")
                    else:
                        lines=[f"📌 {x['name']}\n📍 {x['area'] or x['address'] or 'العنوان غير محدد'}\n☎️ {x['phone'] or 'لا يوجد رقم'}\n🕒 {x['hours'] or 'الدوام غير محدد'}\n{x['notes'] or ''}\nآخر تحديث: {x['updated_at']}" for x in items]
                        send_message(chat_id, f"{text} — سلمية والقرى المحيطة\n\n"+'\n\n'.join(lines))
                elif text in CATEGORIES:
                    with db() as c: c.execute("INSERT INTO submissions(telegram_id,name,category,details,status) VALUES(?,?,?,?,?)",(str(user_id),msg.get("from",{}).get("first_name","مستخدم"),text,"طلب جديد بانتظار التفاصيل والتواصل","pending"))
                    send_message(chat_id,f"تم تسجيل طلبك ضمن قسم: {text}\nأرسل التفاصيل ورقم التواصل برسالة لاحقة.")
                elif text and not text.startswith("/"):
                    with db() as c: c.execute("INSERT INTO submissions(telegram_id,name,category,details) VALUES(?,?,?,?)",(str(user_id),msg.get("from",{}).get("first_name","مستخدم"),"رسالة / تفاصيل إضافية",text))
                    send_message(chat_id,"وصلتنا رسالتك بنجاح، وسيتم مراجعتها من الإدارة. شكراً لك 🌷")
        except Exception: time.sleep(3)

def db():
    c=sqlite3.connect(DB_PATH); c.row_factory=sqlite3.Row; return c

def init_db():
    with db() as c:
        c.execute('''CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT UNIQUE NOT NULL, password_hash TEXT NOT NULL, role TEXT NOT NULL DEFAULT 'moderator', active INTEGER NOT NULL DEFAULT 1, created_at TEXT DEFAULT CURRENT_TIMESTAMP)''')
        c.execute('''CREATE TABLE IF NOT EXISTS submissions(id INTEGER PRIMARY KEY AUTOINCREMENT, telegram_id TEXT, name TEXT, category TEXT, details TEXT, contact TEXT, status TEXT DEFAULT 'pending', created_at TEXT DEFAULT CURRENT_TIMESTAMP)''')
        c.execute('''CREATE TABLE IF NOT EXISTS service_entries(id INTEGER PRIMARY KEY AUTOINCREMENT, category TEXT NOT NULL, name TEXT NOT NULL, phone TEXT, address TEXT, area TEXT, hours TEXT, notes TEXT, active INTEGER NOT NULL DEFAULT 1, updated_at TEXT DEFAULT CURRENT_TIMESTAMP)''')
        if ADMIN_PASSWORD and not c.execute('SELECT id FROM users WHERE username=?',(ADMIN_USER,)).fetchone():
            c.execute('INSERT INTO users(username,password_hash,role,active) VALUES(?,?,?,1)',(ADMIN_USER,generate_password_hash(ADMIN_PASSWORD),'owner'))

def me():
    if not session.get('uid'): return None
    with db() as c: return c.execute('SELECT * FROM users WHERE id=? AND active=1',(session['uid'],)).fetchone()

def login_required(f):
    @wraps(f)
    def w(*a,**k):
        if not me(): session.clear(); return redirect(url_for('login'))
        return f(*a,**k)
    return w

def owner_required(f):
    @wraps(f)
    @login_required
    def w(*a,**k):
        if me()['role']!='owner': abort(403)
        return f(*a,**k)
    return w

STYLE='''<style>body{font-family:Arial;background:#f3f6f3;margin:0;color:#193629}.top{background:#176b45;color:white;padding:18px}.wrap{max-width:950px;margin:20px auto;padding:0 12px}.card{background:white;padding:16px;border-radius:14px;margin:12px 0;box-shadow:0 2px 10px #0001}.stats{display:flex;gap:10px;flex-wrap:wrap}.stat{flex:1;min-width:100px;background:white;padding:14px;border-radius:12px}.num{font-size:25px;color:#176b45;font-weight:bold}a,button{background:#176b45;color:white;padding:10px 13px;border:0;border-radius:8px;text-decoration:none;cursor:pointer}input{padding:11px;border:1px solid #ddd;border-radius:8px;margin:5px;max-width:95%}.row{display:flex;gap:8px;flex-wrap:wrap}.muted{color:#68786f;font-size:13px}.danger{background:#9b3333}.nav{margin:12px 0;display:flex;gap:8px;flex-wrap:wrap}</style>'''
LOGIN='''<!doctype html><html lang="ar" dir="rtl"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>دخول الإدارة</title>'''+STYLE+'''<body><div class="wrap"><div class="card" style="max-width:380px;margin:12vh auto"><h2>سلمية نحنا معك</h2><p>تسجيل دخول لوحة التحكم</p>{% for m in get_flashed_messages() %}<p style="color:#b22">{{m}}</p>{% endfor %}<form method="post"><input name="user" placeholder="اسم المستخدم" required style="width:90%"><input name="password" type="password" placeholder="كلمة المرور" required style="width:90%"><button style="width:96%">دخول</button></form></div></div></body></html>'''
DASH='''<!doctype html><html lang="ar" dir="rtl"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'''+STYLE+'''<body><div class="top"><b>سلمية نحنا معك</b><span style="float:left"><a href="/logout">خروج</a></span><div class="muted" style="color:#e8eee9">{{u.username}} — {{'مدير' if u.role=='owner' else 'مشرف'}}</div></div><div class="wrap"><div class="nav"><a href="/dashboard">الطلبات</a><a href="/directory">الدليل الخدمي</a>{% if u.role=='owner' %}<a href="/moderators">إدارة المشرفين</a>{% endif %}</div><h2>لوحة التحكم</h2><div class="stats"><div class="stat">كل الطلبات<div class="num">{{total}}</div></div><div class="stat">بانتظار المراجعة<div class="num">{{pending}}</div></div><div class="stat">المقبولة<div class="num">{{approved}}</div></div></div><h3>الطلبات</h3>{% for x in rows %}<div class="card"><b>#{{x.id}} — {{x.category}}</b> | {{'بانتظار المراجعة' if x.status=='pending' else 'مقبول' if x.status=='approved' else 'مرفوض'}}<p>{{x.details}}</p><div class="muted">{{x.name or 'غير محدد'}} | {{x.contact or 'لا يوجد تواصل'}} | {{x.created_at}}</div><div class="row"><form method="post" action="/status/{{x.id}}/approved"><button>قبول</button></form><form method="post" action="/status/{{x.id}}/rejected"><button class="danger">رفض</button></form>{% if u.role=='owner' %}<form method="post" action="/delete/{{x.id}}"><button style="background:#555">حذف</button></form>{% endif %}</div></div>{% else %}<p>لا توجد طلبات.</p>{% endfor %}</div></body></html>'''
MODS='''<!doctype html><html lang="ar" dir="rtl"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'''+STYLE+'''<body><div class="top">إدارة المشرفين — سلمية نحنا معك <span style="float:left"><a href="/logout">خروج</a></span></div><div class="wrap"><div class="nav"><a href="/dashboard">العودة للطلبات</a></div>{% for m in get_flashed_messages() %}<p>{{m}}</p>{% endfor %}<div class="card"><h3>إضافة مشرف</h3><form method="post"><input name="username" placeholder="اسم المستخدم" required><input name="password" type="password" placeholder="كلمة المرور (8 أحرف على الأقل)" minlength="8" required><button>إضافة</button></form></div><h3>المشرفون</h3>{% for x in mods %}<div class="card"><b>{{x.username}}</b> — {{'مفعّل' if x.active else 'موقوف'}}<div class="row"><form method="post" action="/moderators/{{x.id}}/toggle"><button>{{'إيقاف' if x.active else 'تفعيل'}}</button></form><form method="post" action="/moderators/{{x.id}}/delete" onsubmit="return confirm('حذف المشرف؟')"><button class="danger">حذف</button></form></div></div>{% else %}<p>لم تتم إضافة مشرفين بعد.</p>{% endfor %}</div></body></html>'''

@APP.route('/')
def home(): return '<div dir="rtl" style="font-family:Arial;text-align:center;margin:12vh auto"><h1>سلمية نحنا معك</h1><p>منصة الدليل الخدمي والإعلاني لسلمية والقرى المحيطة</p><p><a href="/services">تصفح الدليل الخدمي</a></p><a href="/login">دخول لوحة الإدارة</a></div>'
@APP.route('/login',methods=['GET','POST'])
def login():
    if request.method=='POST':
        with db() as c: u=c.execute('SELECT * FROM users WHERE username=? AND active=1',(request.form.get('user','').strip(),)).fetchone()
        if u and check_password_hash(u['password_hash'],request.form.get('password','')):
            session.clear(); session['uid']=u['id']; return redirect(url_for('dashboard'))
        flash('اسم المستخدم أو كلمة المرور غير صحيحة.')
    return render_template_string(LOGIN)
@APP.route('/dashboard')
@login_required
def dashboard():
    with db() as c:
        rows=c.execute('SELECT * FROM submissions ORDER BY id DESC').fetchall(); total=c.execute('SELECT COUNT(*) FROM submissions').fetchone()[0]; pending=c.execute("SELECT COUNT(*) FROM submissions WHERE status='pending'").fetchone()[0]; approved=c.execute("SELECT COUNT(*) FROM submissions WHERE status='approved'").fetchone()[0]
    return render_template_string(DASH,rows=rows,total=total,pending=pending,approved=approved,u=me())
@APP.post('/status/<int:item_id>/<status>')
@login_required
def status(item_id,status):
    if status in ('approved','rejected'):
        with db() as c: c.execute('UPDATE submissions SET status=? WHERE id=?',(status,item_id))
    return redirect(url_for('dashboard'))
@APP.post('/delete/<int:item_id>')
@owner_required
def delete(item_id):
    with db() as c: c.execute('DELETE FROM submissions WHERE id=?',(item_id,))
    return redirect(url_for('dashboard'))
@APP.route('/moderators',methods=['GET','POST'])
@owner_required
def moderators():
    if request.method=='POST':
        username=request.form.get('username','').strip(); password=request.form.get('password','')
        if len(username)<3 or len(password)<8: flash('اسم المستخدم 3 أحرف على الأقل وكلمة المرور 8 أحرف على الأقل.')
        else:
            try:
                with db() as c: c.execute("INSERT INTO users(username,password_hash,role,active) VALUES(?,?, 'moderator',1)",(username,generate_password_hash(password)))
                flash('تمت إضافة المشرف.')
            except sqlite3.IntegrityError: flash('اسم المستخدم موجود مسبقاً.')
    with db() as c: mods=c.execute("SELECT * FROM users WHERE role='moderator' ORDER BY id DESC").fetchall()
    return render_template_string(MODS,mods=mods)
@APP.post('/moderators/<int:uid>/toggle')
@owner_required
def toggle(uid):
    with db() as c: c.execute("UPDATE users SET active=CASE active WHEN 1 THEN 0 ELSE 1 END WHERE id=? AND role='moderator'",(uid,))
    return redirect(url_for('moderators'))
@APP.post('/moderators/<int:uid>/delete')
@owner_required
def delmod(uid):
    with db() as c: c.execute("DELETE FROM users WHERE id=? AND role='moderator'",(uid,))
    return redirect(url_for('moderators'))

SERVICE_ADMIN = r"""<!doctype html><html lang="ar" dir="rtl"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">"""+STYLE+r"""<body><div class="top">إدارة الدليل الخدمي — سلمية نحنا معك</div><div class="wrap"><div class="nav"><a href="/dashboard">لوحة التحكم</a><a href="/services">عرض الدليل العام</a></div>{% for m in get_flashed_messages() %}<p>{{m}}</p>{% endfor %}<div class="card"><h3>{{'تعديل بيانات' if edit else 'إضافة بيانات جديدة'}}</h3><form method="post"><p><select name="category" required style="padding:10px;width:95%">{% for c in cats %}<option value="{{c}}" {% if edit and edit.category==c %}selected{% endif %}>{{c}}</option>{% endfor %}</select></p><input name="name" placeholder="الاسم / اسم الجهة" required style="width:95%" value="{{edit.name if edit else ''}}"><input name="phone" placeholder="رقم الهاتف" style="width:95%" value="{{edit.phone if edit else ''}}"><input name="area" placeholder="المنطقة / القرية" style="width:95%" value="{{edit.area if edit else ''}}"><input name="address" placeholder="العنوان بالتفصيل" style="width:95%" value="{{edit.address if edit else ''}}"><input name="hours" placeholder="أوقات الدوام / المناوبة" style="width:95%" value="{{edit.hours if edit else ''}}"><input name="notes" placeholder="ملاحظات إضافية" style="width:95%" value="{{edit.notes if edit else ''}}"><button>حفظ البيانات</button></form></div><h3>البيانات المنشورة والمخفية</h3>{% for x in rows %}<div class="card"><b>{{x.name}}</b> — {{x.category}}<p>☎ {{x.phone or 'لا يوجد'}} | {{x.area or x.address or 'العنوان غير محدد'}}</p><p class="muted">{{'منشور' if x.active else 'مخفي'}} — آخر تحديث: {{x.updated_at}}</p><div class="row"><a href="/directory/{{x.id}}/edit">تعديل</a><form method="post" action="/directory/{{x.id}}/toggle"><button>{{'إخفاء' if x.active else 'نشر'}}</button></form>{% if u.role=='owner' %}<form method="post" action="/directory/{{x.id}}/delete" onsubmit="return confirm('حذف البيانات نهائياً؟')"><button class="danger">حذف</button></form>{% endif %}</div></div>{% else %}<p>لا توجد بيانات بعد. أضف أول جهة من النموذج أعلاه.</p>{% endfor %}</div></body></html>"""
SERVICE_PUBLIC = r"""<!doctype html><html lang="ar" dir="rtl"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">"""+STYLE+r"""<body><div class="top">الدليل الخدمي — سلمية نحنا معك</div><div class="wrap"><div class="nav"><a href="/">الرئيسية</a>{% for c in cats %}<a href="/services?category={{c}}">{{c}}</a>{% endfor %}</div><h2>{{selected or 'الدليل الخدمي'}}</h2>{% for x in rows %}<div class="card"><b>{{x.name}}</b><p>☎️ {% if x.phone %}<a href="tel:{{x.phone}}">{{x.phone}}</a>{% else %}غير متوفر{% endif %}</p><p>📍 {{x.area or ''}} {{x.address or ''}}</p><p>🕒 {{x.hours or 'غير محدد'}}</p><p>{{x.notes or ''}}</p><small class="muted">آخر تحديث: {{x.updated_at}}</small></div>{% else %}<p>لا توجد معلومات منشورة في هذا القسم حالياً. نعمل على التحقق من البيانات وإضافتها.</p>{% endfor %}<p class="muted">يرجى التأكد هاتفياً من أوقات المناوبة قبل التوجه.</p></div></body></html>"""

@APP.route('/directory', methods=['GET','POST'])
@login_required
def directory():
    if request.method=='POST':
        f=request.form
        with db() as c: c.execute('INSERT INTO service_entries(category,name,phone,address,area,hours,notes) VALUES(?,?,?,?,?,?,?)',(f.get('category'),f.get('name','').strip(),f.get('phone','').strip(),f.get('address','').strip(),f.get('area','').strip(),f.get('hours','').strip(),f.get('notes','').strip()))
        flash('تمت إضافة البيانات إلى الدليل.')
    with db() as c: rows=c.execute('SELECT * FROM service_entries ORDER BY id DESC').fetchall()
    return render_template_string(SERVICE_ADMIN,rows=rows,cats=SERVICE_CATEGORIES,edit=None,u=me())

@APP.route('/directory/<int:item_id>/edit', methods=['GET','POST'])
@login_required
def directory_edit(item_id):
    with db() as c: item=c.execute('SELECT * FROM service_entries WHERE id=?',(item_id,)).fetchone()
    if not item: abort(404)
    if request.method=='POST':
        f=request.form
        with db() as c: c.execute('UPDATE service_entries SET category=?,name=?,phone=?,address=?,area=?,hours=?,notes=?,updated_at=CURRENT_TIMESTAMP WHERE id=?',(f.get('category'),f.get('name','').strip(),f.get('phone','').strip(),f.get('address','').strip(),f.get('area','').strip(),f.get('hours','').strip(),f.get('notes','').strip(),item_id))
        flash('تم تحديث البيانات.'); return redirect(url_for('directory'))
    with db() as c: rows=c.execute('SELECT * FROM service_entries ORDER BY id DESC').fetchall()
    return render_template_string(SERVICE_ADMIN,rows=rows,cats=SERVICE_CATEGORIES,edit=item,u=me())

@APP.post('/directory/<int:item_id>/toggle')
@login_required
def directory_toggle(item_id):
    with db() as c: c.execute('UPDATE service_entries SET active=CASE active WHEN 1 THEN 0 ELSE 1 END, updated_at=CURRENT_TIMESTAMP WHERE id=?',(item_id,))
    return redirect(url_for('directory'))

@APP.post('/directory/<int:item_id>/delete')
@owner_required
def directory_delete(item_id):
    with db() as c: c.execute('DELETE FROM service_entries WHERE id=?',(item_id,))
    return redirect(url_for('directory'))

@APP.route('/services')
def services():
    selected=request.args.get('category','').strip()
    with db() as c:
        if selected in SERVICE_CATEGORIES: rows=c.execute('SELECT * FROM service_entries WHERE active=1 AND category=? ORDER BY name',(selected,)).fetchall()
        else: selected=''; rows=c.execute('SELECT * FROM service_entries WHERE active=1 ORDER BY category,name').fetchall()
    return render_template_string(SERVICE_PUBLIC,rows=rows,cats=SERVICE_CATEGORIES,selected=selected)

@APP.route('/logout')
def logout(): session.clear(); return redirect(url_for('login'))

init_db()
if BOT_TOKEN and os.getenv('RUN_BOT_POLLING','true').lower()=='true':
    threading.Thread(target=bot_loop,daemon=True).start()
if __name__=='__main__': APP.run(host='0.0.0.0',port=int(os.getenv('PORT',5000)))
