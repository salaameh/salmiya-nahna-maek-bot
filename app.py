import os, time, threading, sqlite3, requests, re, uuid
from contextlib import contextmanager
from functools import wraps
from flask import Flask, request, redirect, url_for, session, render_template_string, flash, abort
from werkzeug.security import generate_password_hash, check_password_hash

APP = Flask(__name__)
APP.secret_key = os.getenv("SECRET_KEY", "change-this-secret-key-before-production")
DB_PATH = os.getenv("DB_PATH", "data.sqlite3")
BOT_TOKEN = os.getenv("BOT_TOKEN", "")
ADMIN_USER = os.getenv("ADMIN_USER", "admin")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "")
RUN_BOT = os.getenv("RUN_BOT_POLLING", "true").lower() == "true"

SERVICE_CATEGORIES = [
    "أرقام ضرورية", "صيدليات المناوبة", "المشافي والمراكز الطبية",
    "الخدمات العامة", "خدمات الطوارئ"
]

@contextmanager
def db():
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

def tg(method, **data):
    if not BOT_TOKEN:
        return {"ok": False, "description": "BOT_TOKEN غير مضبوط"}
    try:
        r = requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/{method}", json=data, timeout=35)
        return r.json()
    except Exception as exc:
        return {"ok": False, "description": str(exc)}

def send_message(chat_id, text, keyboard=None):
    payload = {"chat_id": chat_id, "text": text, "disable_web_page_preview": True}
    if keyboard:
        payload["reply_markup"] = {"keyboard": keyboard, "resize_keyboard": True}
    return tg("sendMessage", **payload)

def send_content(chat_id, item):
    title = item["title"] or "منشور"
    body = item["body"] or ""
    parts = [f"📌 {title}"]
    if body: parts.append(body)
    if item["contact"]: parts.append(f"☎️ للتواصل: {item['contact']}")
    if item["address"]: parts.append(f"📍 العنوان: {item['address']}")
    text = "\n\n".join(parts)
    if item["image_url"]:
        result = tg("sendPhoto", chat_id=chat_id, photo=item["image_url"], caption=text[:1000])
        if result.get("ok"): return result
    return send_message(chat_id, text)

def ensure_column(conn, table, column, definition):
    cols = [x["name"] for x in conn.execute(f"PRAGMA table_info({table})").fetchall()]
    if column not in cols:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")

def init_db():
    with db() as c:
        c.execute("""CREATE TABLE IF NOT EXISTS users(
            id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL, role TEXT NOT NULL DEFAULT 'moderator',
            active INTEGER NOT NULL DEFAULT 1, created_at TEXT DEFAULT CURRENT_TIMESTAMP)""")
        c.execute("""CREATE TABLE IF NOT EXISTS submissions(
            id INTEGER PRIMARY KEY AUTOINCREMENT, telegram_id TEXT, name TEXT, category TEXT,
            details TEXT, contact TEXT, status TEXT DEFAULT 'pending',
            created_at TEXT DEFAULT CURRENT_TIMESTAMP)""")
        c.execute("""CREATE TABLE IF NOT EXISTS service_entries(
            id INTEGER PRIMARY KEY AUTOINCREMENT, category TEXT NOT NULL, name TEXT NOT NULL,
            phone TEXT, address TEXT, area TEXT, hours TEXT, notes TEXT,
            active INTEGER NOT NULL DEFAULT 1, updated_at TEXT DEFAULT CURRENT_TIMESTAMP)""")
        c.execute("""CREATE TABLE IF NOT EXISTS dynamic_sections(
            id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL UNIQUE, icon TEXT DEFAULT '📂',
            section_type TEXT NOT NULL DEFAULT 'submissions', sort_order INTEGER NOT NULL DEFAULT 0,
            active INTEGER NOT NULL DEFAULT 1, created_at TEXT DEFAULT CURRENT_TIMESTAMP)""")
        c.execute("""CREATE TABLE IF NOT EXISTS content_entries(
            id INTEGER PRIMARY KEY AUTOINCREMENT, section_id INTEGER NOT NULL,
            title TEXT NOT NULL, body TEXT, contact TEXT, address TEXT, image_url TEXT,
            active INTEGER NOT NULL DEFAULT 1, created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(section_id) REFERENCES dynamic_sections(id))""")
        c.execute("""CREATE TABLE IF NOT EXISTS bot_users(
            id INTEGER PRIMARY KEY AUTOINCREMENT, chat_id TEXT UNIQUE NOT NULL,
            telegram_id TEXT, first_name TEXT, username TEXT, active INTEGER NOT NULL DEFAULT 1,
            started_at TEXT DEFAULT CURRENT_TIMESTAMP, last_seen TEXT DEFAULT CURRENT_TIMESTAMP)""")
        c.execute("""CREATE TABLE IF NOT EXISTS notification_logs(
            id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT NOT NULL, message TEXT NOT NULL,
            audience TEXT DEFAULT 'all', sent_count INTEGER DEFAULT 0, failed_count INTEGER DEFAULT 0,
            created_by TEXT, created_at TEXT DEFAULT CURRENT_TIMESTAMP)""")

        # Migrations are additive: old users, submissions and service entries are preserved.
        ensure_column(c, "users", "can_notify", "INTEGER NOT NULL DEFAULT 0")

        defaults = [
            ("دليل المحلات", "🏪", "content", 1),
            ("فرص العمل", "💼", "submissions", 2),
            ("الإعلانات", "📢", "content", 3),
            ("عقارات للبيع والإيجار", "🏠", "submissions", 4),
            ("مناسبات وأخبار", "🎉", "submissions", 5),
            ("آخر الأخبار", "📰", "content", 6),
            ("خدمات المدينة", "🧭", "services", 7),
        ]
        for name, icon, typ, order in defaults:
            c.execute("""INSERT OR IGNORE INTO dynamic_sections(name,icon,section_type,sort_order,active)
                         VALUES(?,?,?,?,1)""", (name, icon, typ, order))
        if ADMIN_PASSWORD:
            exists = c.execute("SELECT id FROM users WHERE username=?", (ADMIN_USER,)).fetchone()
            if not exists:
                c.execute("INSERT INTO users(username,password_hash,role,active,can_notify) VALUES(?,?, 'owner',1,1)",
                          (ADMIN_USER, generate_password_hash(ADMIN_PASSWORD)))
            else:
                c.execute("UPDATE users SET role='owner',can_notify=1 WHERE username=?", (ADMIN_USER,))

def current_user():
    if not session.get("uid"): return None
    with db() as c:
        return c.execute("SELECT * FROM users WHERE id=? AND active=1", (session["uid"],)).fetchone()

def login_required(f):
    @wraps(f)
    def wrapped(*args, **kwargs):
        if not current_user():
            session.clear()
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return wrapped

def owner_required(f):
    @wraps(f)
    @login_required
    def wrapped(*args, **kwargs):
        if current_user()["role"] != "owner": abort(403)
        return f(*args, **kwargs)
    return wrapped

def notify_required(f):
    @wraps(f)
    @login_required
    def wrapped(*args, **kwargs):
        u = current_user()
        if u["role"] != "owner" and not u["can_notify"]: abort(403)
        return f(*args, **kwargs)
    return wrapped

def get_sections(active_only=True):
    with db() as c:
        sql = "SELECT * FROM dynamic_sections"
        if active_only: sql += " WHERE active=1"
        return c.execute(sql + " ORDER BY sort_order,id").fetchall()

def main_keyboard():
    sections = get_sections()
    rows = []
    for section in sections:
        rows.append([{"text": f"{section['icon'] or '📂'} {section['name']}"}])
    rows.append([{"text": "🏠 القائمة الرئيسية"}])
    return rows

def remember_bot_user(message):
    chat = message.get("chat", {})
    user = message.get("from", {})
    chat_id = chat.get("id")
    if not chat_id: return
    with db() as c:
        c.execute("""INSERT INTO bot_users(chat_id,telegram_id,first_name,username,active,last_seen)
                     VALUES(?,?,?,?,1,CURRENT_TIMESTAMP)
                     ON CONFLICT(chat_id) DO UPDATE SET
                     telegram_id=excluded.telegram_id, first_name=excluded.first_name,
                     username=excluded.username, active=1,last_seen=CURRENT_TIMESTAMP""",
                  (str(chat_id), str(user.get("id", "")), user.get("first_name", "مستخدم"), user.get("username", "")))

def handle_service_category(chat_id, category):
    with db() as c:
        items = c.execute("SELECT * FROM service_entries WHERE category=? AND active=1 ORDER BY name", (category,)).fetchall()
    if not items:
        send_message(chat_id, f"لا توجد بيانات منشورة حالياً في قسم {category}. نعمل على تحديث الدليل باستمرار.", main_keyboard())
        return
    lines = []
    for x in items:
        lines.append(f"📌 {x['name']}\n📍 {x['area'] or x['address'] or 'العنوان غير محدد'}\n☎️ {x['phone'] or 'لا يوجد رقم'}\n🕒 {x['hours'] or 'الدوام غير محدد'}\n{x['notes'] or ''}")
    send_message(chat_id, f"{category} — سلمية والقرى المحيطة\n\n" + "\n\n".join(lines), main_keyboard())

def handle_section(chat_id, section, message):
    typ = section["section_type"]
    if typ == "services":
        keys = [[{"text": x}] for x in SERVICE_CATEGORIES]
        keys.append([{"text": "🏠 القائمة الرئيسية"}])
        send_message(chat_id, "اختر من أقسام الدليل الخدمي:", keys)
    elif typ == "content":
        with db() as c:
            items = c.execute("SELECT * FROM content_entries WHERE section_id=? AND active=1 ORDER BY id DESC LIMIT 20",
                              (section["id"],)).fetchall()
        if not items:
            send_message(chat_id, f"لا توجد منشورات حالياً في قسم {section['name']}.", main_keyboard())
        else:
            send_message(chat_id, f"{section['icon']} {section['name']}", main_keyboard())
            for item in items:
                send_content(chat_id, item)
    else:
        user = message.get("from", {})
        with db() as c:
            c.execute("""INSERT INTO submissions(telegram_id,name,category,details,status)
                         VALUES(?,?,?,?, 'pending')""",
                      (str(user.get("id", "")), user.get("first_name", "مستخدم"), section["name"],
                       "طلب جديد من البوت — بانتظار التفاصيل"))
        send_message(chat_id, f"تم تسجيل طلبك ضمن قسم {section['name']}.\nأرسل التفاصيل ورقم التواصل برسالة لاحقة.", main_keyboard())

def bot_loop():
    offset = None
    while True:
        try:
            params = {"timeout": 25}
            if offset is not None: params["offset"] = offset
            result = requests.get(f"https://api.telegram.org/bot{BOT_TOKEN}/getUpdates", params=params, timeout=35).json()
            for update in result.get("result", []):
                offset = update["update_id"] + 1
                msg = update.get("message", {})
                chat_id = msg.get("chat", {}).get("id")
                text = (msg.get("text") or "").strip()
                if not chat_id: continue
                remember_bot_user(msg)
                if text in ("/start", "/menu", "القائمة الرئيسية", "🏠 القائمة الرئيسية"):
                    send_message(chat_id, "أهلاً بك في بوت سلمية نحنا معك 🌿\nاختر القسم الذي تريد تصفحه:", main_keyboard())
                    continue
                if text in SERVICE_CATEGORIES:
                    handle_service_category(chat_id, text)
                    continue
                # Telegram keyboard labels include the section icon; match by exact stored name or label.
                section = None
                for s in get_sections():
                    if text in (s["name"], f"{s['icon'] or '📂'} {s['name']}"):
                        section = s
                        break
                if section:
                    handle_section(chat_id, section, msg)
                    continue
                if text and not text.startswith("/"):
                    user = msg.get("from", {})
                    with db() as c:
                        c.execute("""INSERT INTO submissions(telegram_id,name,category,details)
                                     VALUES(?,?,?,?)""",
                                  (str(user.get("id", "")), user.get("first_name", "مستخدم"),
                                   "رسالة / تفاصيل إضافية", text))
                    send_message(chat_id, "وصلتنا رسالتك بنجاح، وسيتم مراجعتها من الإدارة. شكراً لك 🌷", main_keyboard())
        except Exception:
            time.sleep(3)

STYLE = """
<style>
*{box-sizing:border-box}body{font-family:Arial,Tahoma;background:#f2f6f3;margin:0;color:#193629}
.top{background:#176b45;color:#fff;padding:18px 16px}.top a{float:left}
.wrap{max-width:1050px;margin:18px auto;padding:0 12px}.card,.stat{background:#fff;padding:16px;border-radius:14px;margin:12px 0;box-shadow:0 2px 10px #0001}
.stats{display:flex;gap:10px;flex-wrap:wrap}.stat{flex:1;min-width:130px}.num{font-size:25px;color:#176b45;font-weight:bold}
a,button{display:inline-block;background:#176b45;color:white;padding:10px 13px;border:0;border-radius:8px;text-decoration:none;cursor:pointer;font-size:14px}
button.danger,a.danger{background:#9b3333}.secondary{background:#53665c}.nav,.row{display:flex;gap:8px;flex-wrap:wrap;margin:12px 0}
input,select,textarea{display:block;width:100%;padding:12px;border:1px solid #d8e1da;border-radius:8px;margin:8px 0;font:inherit;background:#fff}
textarea{min-height:110px}.muted{color:#68786f;font-size:13px}.tag{background:#e8f3ec;color:#176b45;padding:5px 9px;border-radius:20px;font-size:12px}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:10px}.inline{display:inline}
</style>
"""
LOGIN = """<!doctype html><html lang="ar" dir="rtl"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>دخول الإدارة</title>""" + STYLE + """
<body><div class="wrap"><div class="card" style="max-width:420px;margin:12vh auto"><h2>🌿 سلمية نحنا معك</h2><p>تسجيل دخول لوحة التحكم</p>
{% for m in get_flashed_messages() %}<p style="color:#b22">{{m}}</p>{% endfor %}
<form method="post"><input name="user" placeholder="اسم المستخدم" required><input name="password" type="password" placeholder="كلمة المرور" required><button style="width:100%">دخول</button></form></div></div></body></html>"""

BASE_TOP = """<div class="top"><b>🌿 سلمية نحنا معك</b><a href="/logout">خروج</a><div class="muted" style="color:#e8eee9">{{u.username}} — {{'مدير' if u.role=='owner' else 'مشرف'}}</div></div>"""
NAV = """<div class="nav"><a href="/dashboard">الرئيسية والطلبات</a><a href="/sections">مدير القوائم</a><a href="/directory">الدليل الخدمي</a><a href="/notifications">الإشعارات</a>{% if u.role=='owner' %}<a href="/moderators">المشرفون</a>{% endif %}<a href="/services">معاينة الدليل</a></div>"""

DASH = """<!doctype html><html lang="ar" dir="rtl"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">""" + STYLE + """<body>""" + BASE_TOP + """<div class="wrap">""" + NAV + """
<h2>لوحة التحكم</h2><div class="stats">
<div class="stat">كل الطلبات<div class="num">{{total}}</div></div><div class="stat">بانتظار المراجعة<div class="num">{{pending}}</div></div>
<div class="stat">المقبولة<div class="num">{{approved}}</div></div><div class="stat">مستخدمو البوت<div class="num">{{users_count}}</div></div>
<div class="stat">الأقسام الفعالة<div class="num">{{sections_count}}</div></div></div>
<div class="grid"><div class="card"><h3>📂 القوائم والأقسام</h3><p>إضافة الأقسام وتحديد طريقة عرضها.</p><a href="/sections">إدارة القوائم</a></div>
<div class="card"><h3>🏪 المحلات والإعلانات والأخبار</h3><p>أضف محتوى يظهر مباشرة في البوت.</p><a href="/sections">إدارة المحتوى</a></div>
<div class="card"><h3>🔔 الإشعارات</h3><p>إرسال الأخبار العاجلة ومتابعة سجل الإرسال.</p><a href="/notifications">إدارة الإشعارات</a></div></div>
<h3>الطلبات الواردة</h3>
{% for x in rows %}<div class="card"><b>#{{x.id}} — {{x.category}}</b> <span class="tag">{{'بانتظار المراجعة' if x.status=='pending' else 'مقبول' if x.status=='approved' else 'مرفوض'}}</span>
<p>{{x.details}}</p><div class="muted">{{x.name or 'غير محدد'}} | {{x.contact or 'لا يوجد تواصل'}} | {{x.created_at}}</div>
<div class="row"><form method="post" action="/status/{{x.id}}/approved"><button>قبول</button></form><form method="post" action="/status/{{x.id}}/rejected"><button class="danger">رفض</button></form>
{% if u.role=='owner' %}<form method="post" action="/delete/{{x.id}}"><button class="secondary">حذف</button></form>{% endif %}</div></div>
{% else %}<p>لا توجد طلبات.</p>{% endfor %}</div></body></html>"""

SECTIONS_PAGE = """<!doctype html><html lang="ar" dir="rtl"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">""" + STYLE + """<body>""" + BASE_TOP + """<div class="wrap">""" + NAV + """
<h2>مدير القوائم الديناميكية</h2><p class="muted">أنشئ الأقسام وعدّل أسماءها وأيقوناتها وترتيبها. الأقسام الفعالة تظهر تلقائياً في قائمة البوت.</p>
<div class="card"><h3>{{'تعديل قسم' if edit else 'إضافة قسم جديد'}}</h3><form method="post">
<label>اسم القسم</label><input name="name" required value="{{edit.name if edit else ''}}" placeholder="مثلاً: عروض اليوم">
<label>الأيقونة</label><input name="icon" value="{{edit.icon if edit else '📂'}}" placeholder="مثلاً 🏪">
<label>نوع القسم</label><select name="section_type" required>
<option value="content" {% if edit and edit.section_type=='content' %}selected{% endif %}>محتوى تنشره الإدارة (محلات / إعلانات / أخبار)</option>
<option value="submissions" {% if edit and edit.section_type=='submissions' %}selected{% endif %}>استقبال طلبات المستخدمين</option>
<option value="services" {% if edit and edit.section_type=='services' %}selected{% endif %}>الدليل الخدمي</option></select>
<label>ترتيب الظهور</label><input type="number" name="sort_order" value="{{edit.sort_order if edit else next_order}}">
<button>حفظ القسم</button>{% if edit %}<a class="secondary" href="/sections">إلغاء</a>{% endif %}</form></div>
<h3>الأقسام الحالية</h3>{% for s in rows %}<div class="card"><b>{{s.icon}} {{s.name}}</b> <span class="tag">{{'محتوى الإدارة' if s.section_type=='content' else 'طلبات المستخدمين' if s.section_type=='submissions' else 'دليل خدمي'}}</span>
<p class="muted">الترتيب: {{s.sort_order}} — {{'ظاهر في البوت' if s.active else 'مخفي'}}</p><div class="row">
<a href="/sections/{{s.id}}/edit">تعديل</a>{% if s.section_type=='content' %}<a href="/content/{{s.id}}">إدارة المحتوى</a>{% endif %}
<form method="post" action="/sections/{{s.id}}/toggle"><button class="secondary">{{'إخفاء' if s.active else 'إظهار'}}</button></form>
{% if u.role=='owner' %}<form method="post" action="/sections/{{s.id}}/delete" onsubmit="return confirm('حذف القسم؟ سيتم حذف منشوراته أيضاً.')"><button class="danger">حذف</button></form>{% endif %}</div></div>{% endfor %}
</div></body></html>"""

CONTENT_PAGE = """<!doctype html><html lang="ar" dir="rtl"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">""" + STYLE + """<body>""" + BASE_TOP + """<div class="wrap">""" + NAV + """
<h2>{{section.icon}} إدارة قسم {{section.name}}</h2><div class="card"><h3>{{'تعديل المنشور' if edit else 'إضافة منشور جديد'}}</h3>
<form method="post"><label>العنوان / اسم المحل</label><input name="title" required value="{{edit.title if edit else ''}}" placeholder="العنوان">
<label>التفاصيل</label><textarea name="body" placeholder="اكتب تفاصيل المنشور">{{edit.body if edit else ''}}</textarea>
<label>رقم التواصل (اختياري)</label><input name="contact" value="{{edit.contact if edit else ''}}" placeholder="رقم الهاتف أو واتساب">
<label>العنوان (اختياري)</label><input name="address" value="{{edit.address if edit else ''}}" placeholder="المنطقة والعنوان">
<label>رابط صورة مباشر (اختياري)</label><input name="image_url" value="{{edit.image_url if edit else ''}}" placeholder="https://...">
<button>حفظ ونشر</button>{% if edit %}<a class="secondary" href="/content/{{section.id}}">إلغاء</a>{% endif %}</form></div>
<h3>المنشورات</h3>{% for x in rows %}<div class="card"><b>{{x.title}}</b><p>{{x.body}}</p><div class="muted">{{x.contact or ''}} {{x.address or ''}} — {{'منشور' if x.active else 'مخفي'}}</div>
<div class="row"><a href="/content/{{section.id}}/{{x.id}}/edit">تعديل</a><form method="post" action="/content/{{section.id}}/{{x.id}}/toggle"><button class="secondary">{{'إخفاء' if x.active else 'نشر'}}</button></form>
{% if u.role=='owner' %}<form method="post" action="/content/{{section.id}}/{{x.id}}/delete" onsubmit="return confirm('حذف المنشور؟')"><button class="danger">حذف</button></form>{% endif %}</div></div>{% else %}<p>لا يوجد محتوى بعد.</p>{% endfor %}
</div></body></html>"""

SERVICE_ADMIN = """<!doctype html><html lang="ar" dir="rtl"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">""" + STYLE + """<body>""" + BASE_TOP + """<div class="wrap">""" + NAV + """
<h2>إدارة الدليل الخدمي</h2><div class="card"><h3>{{'تعديل بيانات' if edit else 'إضافة بيانات جديدة'}}</h3><form method="post">
<label>القسم</label><select name="category">{% for cat in cats %}<option value="{{cat}}" {% if edit and edit.category==cat %}selected{% endif %}>{{cat}}</option>{% endfor %}</select>
<input name="name" required placeholder="اسم الجهة" value="{{edit.name if edit else ''}}"><input name="phone" placeholder="رقم الهاتف" value="{{edit.phone if edit else ''}}">
<input name="area" placeholder="المنطقة / القرية" value="{{edit.area if edit else ''}}"><input name="address" placeholder="العنوان بالتفصيل" value="{{edit.address if edit else ''}}">
<input name="hours" placeholder="أوقات الدوام / المناوبة" value="{{edit.hours if edit else ''}}"><input name="notes" placeholder="ملاحظات إضافية" value="{{edit.notes if edit else ''}}">
<button>حفظ البيانات</button></form></div>
{% for x in rows %}<div class="card"><b>{{x.name}}</b> — {{x.category}}<p>☎ {{x.phone or 'لا يوجد'}} | {{x.area or x.address or 'العنوان غير محدد'}}</p>
<p class="muted">{{'منشور' if x.active else 'مخفي'}} — {{x.updated_at}}</p><div class="row"><a href="/directory/{{x.id}}/edit">تعديل</a>
<form method="post" action="/directory/{{x.id}}/toggle"><button class="secondary">{{'إخفاء' if x.active else 'نشر'}}</button></form>
{% if u.role=='owner' %}<form method="post" action="/directory/{{x.id}}/delete"><button class="danger">حذف</button></form>{% endif %}</div></div>{% endfor %}
</div></body></html>"""

SERVICE_PUBLIC = """<!doctype html><html lang="ar" dir="rtl"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">""" + STYLE + """
<body><div class="top">🌿 الدليل الخدمي — سلمية نحنا معك</div><div class="wrap"><div class="nav"><a href="/">الرئيسية</a>{% for cat in cats %}<a href="/services?category={{cat}}">{{cat}}</a>{% endfor %}</div>
<h2>{{selected or 'الدليل الخدمي'}}</h2>{% for x in rows %}<div class="card"><b>{{x.name}}</b><p>☎️ {% if x.phone %}<a href="tel:{{x.phone}}">{{x.phone}}</a>{% else %}غير متوفر{% endif %}</p>
<p>📍 {{x.area or ''}} {{x.address or ''}}</p><p>🕒 {{x.hours or 'غير محدد'}}</p><p>{{x.notes or ''}}</p></div>{% else %}<p>لا توجد معلومات منشورة حالياً.</p>{% endfor %}
<p class="muted">يرجى التأكد هاتفياً من أوقات المناوبة قبل التوجه.</p></div></body></html>"""

MODS = """<!doctype html><html lang="ar" dir="rtl"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">""" + STYLE + """<body>""" + BASE_TOP + """<div class="wrap">""" + NAV + """
<h2>إدارة المشرفين</h2><div class="card"><h3>إضافة مشرف</h3><form method="post"><input name="username" placeholder="اسم المستخدم" required>
<input name="password" type="password" placeholder="كلمة المرور (8 أحرف على الأقل)" minlength="8" required>
<label><input type="checkbox" name="can_notify" value="1" style="display:inline;width:auto"> السماح لهذا المشرف بإرسال الإشعارات</label><button>إضافة مشرف</button></form></div>
{% for x in mods %}<div class="card"><b>{{x.username}}</b> — {{'مفعّل' if x.active else 'موقوف'}} — {{'يمكنه إرسال الإشعارات' if x.can_notify else 'لا يمكنه إرسال الإشعارات'}}
<div class="row"><form method="post" action="/moderators/{{x.id}}/toggle"><button class="secondary">{{'إيقاف' if x.active else 'تفعيل'}}</button></form>
<form method="post" action="/moderators/{{x.id}}/notify"><button>{{'إلغاء صلاحية الإشعارات' if x.can_notify else 'منح صلاحية الإشعارات'}}</button></form>
<form method="post" action="/moderators/{{x.id}}/delete"><button class="danger">حذف</button></form></div></div>{% endfor %}
</div></body></html>"""

NOTIFICATIONS = """<!doctype html><html lang="ar" dir="rtl"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">""" + STYLE + """<body>""" + BASE_TOP + """<div class="wrap">""" + NAV + """
<h2>🔔 الأخبار العاجلة والإشعارات</h2><div class="stats"><div class="stat">مستخدمو البوت<div class="num">{{users_count}}</div></div><div class="stat">إشعارات مرسلة<div class="num">{{logs|length}}</div></div></div>
<div class="card"><h3>إرسال إشعار جديد</h3><p class="muted">سيصل الإشعار إلى المستخدمين الذين بدأوا البوت ولم يحظروه. تأكد من صحة النص قبل الإرسال.</p>
<form method="post" onsubmit="return confirm('تأكيد إرسال الإشعار الآن لجميع مستخدمي البوت؟')">
<label>عنوان الإشعار</label><input name="title" required placeholder="🚨 خبر عاجل | سلمية">
<label>نص الخبر</label><textarea name="message" required placeholder="اكتب نص الخبر أو التنبيه هنا"></textarea>
<label>الجمهور</label><select name="audience"><option value="all">جميع مستخدمي البوت</option></select>
<button style="background:#a52b2b">تأكيد وإرسال الإشعار</button></form></div>
<h3>سجل الإشعارات</h3>{% for x in logs %}<div class="card"><b>{{x.title}}</b><p>{{x.message}}</p><span class="tag">نجح: {{x.sent_count}}</span> <span class="tag">فشل: {{x.failed_count}}</span><p class="muted">{{x.created_by}} — {{x.created_at}}</p></div>{% else %}<p>لا توجد إشعارات مرسلة بعد.</p>{% endfor %}
</div></body></html>"""

@APP.route("/")
def home():
    return """<div dir="rtl" style="font-family:Arial;text-align:center;margin:12vh auto"><h1>🌿 سلمية نحنا معك</h1><p>منصة الدليل الخدمي والإعلاني والأخباري لسلمية والقرى المحيطة</p><p><a href="/services">تصفح الدليل الخدمي</a></p><a href="/login">دخول لوحة الإدارة</a></div>"""

@APP.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        with db() as c:
            u = c.execute("SELECT * FROM users WHERE username=? AND active=1", (request.form.get("user", "").strip(),)).fetchone()
        if u and check_password_hash(u["password_hash"], request.form.get("password", "")):
            session.clear()
            session["uid"] = u["id"]
            return redirect(url_for("dashboard"))
        flash("اسم المستخدم أو كلمة المرور غير صحيحة.")
    return render_template_string(LOGIN)

@APP.route("/dashboard")
@login_required
def dashboard():
    with db() as c:
        rows = c.execute("SELECT * FROM submissions ORDER BY id DESC LIMIT 100").fetchall()
        total = c.execute("SELECT COUNT(*) FROM submissions").fetchone()[0]
        pending = c.execute("SELECT COUNT(*) FROM submissions WHERE status='pending'").fetchone()[0]
        approved = c.execute("SELECT COUNT(*) FROM submissions WHERE status='approved'").fetchone()[0]
        users_count = c.execute("SELECT COUNT(*) FROM bot_users WHERE active=1").fetchone()[0]
        sections_count = c.execute("SELECT COUNT(*) FROM dynamic_sections WHERE active=1").fetchone()[0]
    return render_template_string(DASH, rows=rows, total=total, pending=pending, approved=approved,
                                  users_count=users_count, sections_count=sections_count, u=current_user())

@APP.post("/status/<int:item_id>/<status>")
@login_required
def status(item_id, status):
    if status in ("approved", "rejected"):
        with db() as c: c.execute("UPDATE submissions SET status=? WHERE id=?", (status, item_id))
    return redirect(url_for("dashboard"))

@APP.post("/delete/<int:item_id>")
@owner_required
def delete_submission(item_id):
    with db() as c: c.execute("DELETE FROM submissions WHERE id=?", (item_id,))
    return redirect(url_for("dashboard"))

@APP.route("/sections", methods=["GET", "POST"])
@login_required
def sections():
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        icon = request.form.get("icon", "📂").strip() or "📂"
        typ = request.form.get("section_type", "submissions")
        order = request.form.get("sort_order", "0")
        if not name or typ not in ("content", "submissions", "services"):
            flash("تحقق من اسم القسم ونوعه.")
        else:
            try:
                with db() as c:
                    c.execute("INSERT INTO dynamic_sections(name,icon,section_type,sort_order,active) VALUES(?,?,?,?,1)",
                              (name, icon, typ, int(order or 0)))
                flash("تمت إضافة القسم. سيظهر في البوت تلقائياً.")
            except sqlite3.IntegrityError:
                flash("اسم القسم موجود مسبقاً.")
        return redirect(url_for("sections"))
    rows = get_sections(active_only=False)
    return render_template_string(SECTIONS_PAGE, rows=rows, edit=None, next_order=len(rows)+1, u=current_user())

@APP.route("/sections/<int:section_id>/edit", methods=["GET", "POST"])
@login_required
def section_edit(section_id):
    with db() as c: item = c.execute("SELECT * FROM dynamic_sections WHERE id=?", (section_id,)).fetchone()
    if not item: abort(404)
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        typ = request.form.get("section_type", "submissions")
        try:
            with db() as c:
                c.execute("UPDATE dynamic_sections SET name=?,icon=?,section_type=?,sort_order=? WHERE id=?",
                          (name, request.form.get("icon", "📂").strip() or "📂", typ,
                           int(request.form.get("sort_order", 0)), section_id))
            flash("تم تعديل القسم.")
            return redirect(url_for("sections"))
        except sqlite3.IntegrityError:
            flash("يوجد قسم آخر بالاسم نفسه.")
    return render_template_string(SECTIONS_PAGE, rows=get_sections(False), edit=item, next_order=0, u=current_user())

@APP.post("/sections/<int:section_id>/toggle")
@login_required
def section_toggle(section_id):
    with db() as c:
        c.execute("UPDATE dynamic_sections SET active=CASE active WHEN 1 THEN 0 ELSE 1 END WHERE id=?", (section_id,))
    return redirect(url_for("sections"))

@APP.post("/sections/<int:section_id>/delete")
@owner_required
def section_delete(section_id):
    with db() as c:
        c.execute("DELETE FROM content_entries WHERE section_id=?", (section_id,))
        c.execute("DELETE FROM dynamic_sections WHERE id=?", (section_id,))
    return redirect(url_for("sections"))

@APP.route("/content/<int:section_id>", methods=["GET", "POST"])
@login_required
def content(section_id):
    with db() as c:
        section = c.execute("SELECT * FROM dynamic_sections WHERE id=? AND section_type='content'", (section_id,)).fetchone()
    if not section: abort(404)
    if request.method == "POST":
        f = request.form
        with db() as c:
            c.execute("""INSERT INTO content_entries(section_id,title,body,contact,address,image_url,active)
                         VALUES(?,?,?,?,?,?,1)""",
                      (section_id, f.get("title", "").strip(), f.get("body", "").strip(),
                       f.get("contact", "").strip(), f.get("address", "").strip(), f.get("image_url", "").strip()))
        flash("تم حفظ المنشور ونشره.")
        return redirect(url_for("content", section_id=section_id))
    with db() as c:
        rows = c.execute("SELECT * FROM content_entries WHERE section_id=? ORDER BY id DESC", (section_id,)).fetchall()
    return render_template_string(CONTENT_PAGE, section=section, rows=rows, edit=None, u=current_user())

@APP.route("/content/<int:section_id>/<int:item_id>/edit", methods=["GET", "POST"])
@login_required
def content_edit(section_id, item_id):
    with db() as c:
        section = c.execute("SELECT * FROM dynamic_sections WHERE id=? AND section_type='content'", (section_id,)).fetchone()
        item = c.execute("SELECT * FROM content_entries WHERE id=? AND section_id=?", (item_id, section_id)).fetchone()
    if not section or not item: abort(404)
    if request.method == "POST":
        f = request.form
        with db() as c:
            c.execute("""UPDATE content_entries SET title=?,body=?,contact=?,address=?,image_url=?,updated_at=CURRENT_TIMESTAMP
                         WHERE id=? AND section_id=?""",
                      (f.get("title", "").strip(), f.get("body", "").strip(), f.get("contact", "").strip(),
                       f.get("address", "").strip(), f.get("image_url", "").strip(), item_id, section_id))
        flash("تم تحديث المنشور.")
        return redirect(url_for("content", section_id=section_id))
    with db() as c:
        rows = c.execute("SELECT * FROM content_entries WHERE section_id=? ORDER BY id DESC", (section_id,)).fetchall()
    return render_template_string(CONTENT_PAGE, section=section, rows=rows, edit=item, u=current_user())

@APP.post("/content/<int:section_id>/<int:item_id>/toggle")
@login_required
def content_toggle(section_id, item_id):
    with db() as c:
        c.execute("""UPDATE content_entries SET active=CASE active WHEN 1 THEN 0 ELSE 1 END,
                     updated_at=CURRENT_TIMESTAMP WHERE id=? AND section_id=?""", (item_id, section_id))
    return redirect(url_for("content", section_id=section_id))

@APP.post("/content/<int:section_id>/<int:item_id>/delete")
@owner_required
def content_delete(section_id, item_id):
    with db() as c: c.execute("DELETE FROM content_entries WHERE id=? AND section_id=?", (item_id, section_id))
    return redirect(url_for("content", section_id=section_id))

@APP.route("/directory", methods=["GET", "POST"])
@login_required
def directory():
    if request.method == "POST":
        f = request.form
        with db() as c:
            c.execute("""INSERT INTO service_entries(category,name,phone,address,area,hours,notes)
                         VALUES(?,?,?,?,?,?,?)""",
                      (f.get("category"), f.get("name", "").strip(), f.get("phone", "").strip(),
                       f.get("address", "").strip(), f.get("area", "").strip(), f.get("hours", "").strip(),
                       f.get("notes", "").strip()))
        flash("تمت إضافة البيانات إلى الدليل.")
    with db() as c: rows = c.execute("SELECT * FROM service_entries ORDER BY id DESC").fetchall()
    return render_template_string(SERVICE_ADMIN, rows=rows, cats=SERVICE_CATEGORIES, edit=None, u=current_user())

@APP.route("/directory/<int:item_id>/edit", methods=["GET", "POST"])
@login_required
def directory_edit(item_id):
    with db() as c: item = c.execute("SELECT * FROM service_entries WHERE id=?", (item_id,)).fetchone()
    if not item: abort(404)
    if request.method == "POST":
        f = request.form
        with db() as c:
            c.execute("""UPDATE service_entries SET category=?,name=?,phone=?,address=?,area=?,hours=?,notes=?,
                         updated_at=CURRENT_TIMESTAMP WHERE id=?""",
                      (f.get("category"), f.get("name", "").strip(), f.get("phone", "").strip(),
                       f.get("address", "").strip(), f.get("area", "").strip(), f.get("hours", "").strip(),
                       f.get("notes", "").strip(), item_id))
        flash("تم تحديث البيانات.")
        return redirect(url_for("directory"))
    with db() as c: rows = c.execute("SELECT * FROM service_entries ORDER BY id DESC").fetchall()
    return render_template_string(SERVICE_ADMIN, rows=rows, cats=SERVICE_CATEGORIES, edit=item, u=current_user())

@APP.post("/directory/<int:item_id>/toggle")
@login_required
def directory_toggle(item_id):
    with db() as c:
        c.execute("UPDATE service_entries SET active=CASE active WHEN 1 THEN 0 ELSE 1 END,updated_at=CURRENT_TIMESTAMP WHERE id=?", (item_id,))
    return redirect(url_for("directory"))

@APP.post("/directory/<int:item_id>/delete")
@owner_required
def directory_delete(item_id):
    with db() as c: c.execute("DELETE FROM service_entries WHERE id=?", (item_id,))
    return redirect(url_for("directory"))

@APP.route("/services")
def services():
    selected = request.args.get("category", "").strip()
    with db() as c:
        if selected in SERVICE_CATEGORIES:
            rows = c.execute("SELECT * FROM service_entries WHERE active=1 AND category=? ORDER BY name", (selected,)).fetchall()
        else:
            selected = ""
            rows = c.execute("SELECT * FROM service_entries WHERE active=1 ORDER BY category,name").fetchall()
    return render_template_string(SERVICE_PUBLIC, rows=rows, cats=SERVICE_CATEGORIES, selected=selected)

@APP.route("/moderators", methods=["GET", "POST"])
@owner_required
def moderators():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        can_notify = 1 if request.form.get("can_notify") == "1" else 0
        if len(username) < 3 or len(password) < 8:
            flash("اسم المستخدم 3 أحرف على الأقل وكلمة المرور 8 أحرف على الأقل.")
        else:
            try:
                with db() as c:
                    c.execute("INSERT INTO users(username,password_hash,role,active,can_notify) VALUES(?,?,'moderator',1,?)",
                              (username, generate_password_hash(password), can_notify))
                flash("تمت إضافة المشرف.")
            except sqlite3.IntegrityError:
                flash("اسم المستخدم موجود مسبقاً.")
        return redirect(url_for("moderators"))
    with db() as c: mods = c.execute("SELECT * FROM users WHERE role='moderator' ORDER BY id DESC").fetchall()
    return render_template_string(MODS, mods=mods, u=current_user())

@APP.post("/moderators/<int:uid>/toggle")
@owner_required
def moderator_toggle(uid):
    with db() as c: c.execute("UPDATE users SET active=CASE active WHEN 1 THEN 0 ELSE 1 END WHERE id=? AND role='moderator'", (uid,))
    return redirect(url_for("moderators"))

@APP.post("/moderators/<int:uid>/notify")
@owner_required
def moderator_notify(uid):
    with db() as c: c.execute("UPDATE users SET can_notify=CASE can_notify WHEN 1 THEN 0 ELSE 1 END WHERE id=? AND role='moderator'", (uid,))
    return redirect(url_for("moderators"))

@APP.post("/moderators/<int:uid>/delete")
@owner_required
def moderator_delete(uid):
    with db() as c: c.execute("DELETE FROM users WHERE id=? AND role='moderator'", (uid,))
    return redirect(url_for("moderators"))

@APP.route("/notifications", methods=["GET", "POST"])
@notify_required
def notifications():
    if request.method == "POST":
        title = request.form.get("title", "").strip()
        message = request.form.get("message", "").strip()
        if not title or not message:
            flash("اكتب عنوان الإشعار ونصه.")
            return redirect(url_for("notifications"))
        full_text = f"{title}\n\n{message}\n\nسلمية نحنا معك 🌿"
        with db() as c:
            recipients = c.execute("SELECT chat_id FROM bot_users WHERE active=1").fetchall()
        sent = failed = 0
        for recipient in recipients:
            result = send_message(recipient["chat_id"], full_text)
            if result.get("ok"):
                sent += 1
            else:
                failed += 1
                desc = (result.get("description") or "").lower()
                if "blocked" in desc or "chat not found" in desc or "deactivated" in desc:
                    with db() as c:
                        c.execute("UPDATE bot_users SET active=0 WHERE chat_id=?", (recipient["chat_id"],))
            time.sleep(0.05)
        with db() as c:
            c.execute("""INSERT INTO notification_logs(title,message,audience,sent_count,failed_count,created_by)
                         VALUES(?,?,?,?,?,?)""",
                      (title, message, "all", sent, failed, current_user()["username"]))
        flash(f"انتهى الإرسال: نجح {sent} — تعذر {failed}.")
        return redirect(url_for("notifications"))
    with db() as c:
        users_count = c.execute("SELECT COUNT(*) FROM bot_users WHERE active=1").fetchone()[0]
        logs = c.execute("SELECT * FROM notification_logs ORDER BY id DESC LIMIT 30").fetchall()
    return render_template_string(NOTIFICATIONS, users_count=users_count, logs=logs, u=current_user())

@APP.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))

init_db()
if BOT_TOKEN and RUN_BOT:
    threading.Thread(target=bot_loop, daemon=True).start()

if __name__ == "__main__":
    APP.run(host="0.0.0.0", port=int(os.getenv("PORT", 5000)))
