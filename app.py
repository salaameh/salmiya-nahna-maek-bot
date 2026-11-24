import os, time, threading, sqlite3, requests
from functools import wraps
from flask import Flask, request, redirect, url_for, session, render_template_string, flash

APP = Flask(__name__)
APP.secret_key = os.getenv("SECRET_KEY", "change-this-secret-key")
DB_PATH = os.getenv("DB_PATH", "data.sqlite3")
BOT_TOKEN = os.getenv("BOT_TOKEN", "")
ADMIN_USER = os.getenv("ADMIN_USER", "admin")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "")

CATEGORIES = [
    "دليل المحلات", "فرص العمل", "الإعلانات", "عقارات للبيع والإيجار",
    "مناسبات وأخبار", "خدمات المدينة"
]

def db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    with db() as con:
        con.execute("""CREATE TABLE IF NOT EXISTS submissions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            telegram_id TEXT, name TEXT, category TEXT, details TEXT,
            contact TEXT, status TEXT DEFAULT 'pending',
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )""")

def tg(method, **data):
    if not BOT_TOKEN:
        return {}
    try:
        r = requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/{method}", json=data, timeout=35)
        return r.json()
    except Exception:
        return {}

def send_message(chat_id, text, keyboard=None):
    payload = {"chat_id": chat_id, "text": text}
    if keyboard:
        payload["reply_markup"] = {"keyboard": keyboard, "resize_keyboard": True}
    tg("sendMessage", **payload)

def save_submission(user_id, name, category, details, contact):
    with db() as con:
        con.execute(
            "INSERT INTO submissions (telegram_id,name,category,details,contact) VALUES (?,?,?,?,?)",
            (str(user_id), name, category, details, contact)
        )

def bot_loop():
    offset = None
    while True:
        if not BOT_TOKEN:
            time.sleep(10)
            continue
        try:
            params = {"timeout": 25}
            if offset is not None:
                params["offset"] = offset
            result = requests.get(f"https://api.telegram.org/bot{BOT_TOKEN}/getUpdates", params=params, timeout=35).json()
            for update in result.get("result", []):
                offset = update["update_id"] + 1
                msg = update.get("message", {})
                chat = msg.get("chat", {})
                chat_id = chat.get("id")
                user_id = msg.get("from", {}).get("id", "")
                text = (msg.get("text") or "").strip()
                if not chat_id:
                    continue
                if text in ("/start", "القائمة الرئيسية"):
                    keys = [[{"text": c}] for c in CATEGORIES]
                    send_message(chat_id, "أهلاً بك في بوت سلمية نحنا معك 🌿\nاختر القسم الذي تريد إرسال طلب إليه:", keys)
                elif text in CATEGORIES:
                    with db() as con:
                        con.execute("INSERT INTO submissions (telegram_id,name,category,details,status) VALUES (?,?,?,?,?)",
                                    (str(user_id), str(msg.get("from", {}).get("first_name", "مستخدم")), text,
                                     "طلب جديد بانتظار التفاصيل والتواصل", "pending"))
                    send_message(chat_id, f"تم تسجيل طلبك ضمن قسم: {text}\nلإكمال التفاصيل، أرسل رسالة تتضمن الاسم والتفاصيل ورقم التواصل.")
                elif text and not text.startswith("/"):
                    save_submission(user_id, str(msg.get("from", {}).get("first_name", "مستخدم")),
                                    "رسالة / تفاصيل إضافية", text, "")
                    send_message(chat_id, "وصلتنا رسالتك بنجاح، وسيتم مراجعتها من الإدارة. شكراً لك 🌷")
        except Exception:
            time.sleep(3)

def login_required(fn):
    @wraps(fn)
    def wrapped(*args, **kwargs):
        if not session.get("logged_in"):
            return redirect(url_for("login"))
        return fn(*args, **kwargs)
    return wrapped

LOGIN_HTML = """
<!doctype html><html lang="ar" dir="rtl"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>دخول الإدارة</title><style>
body{font-family:Arial;background:#f2f5f1;margin:0;display:grid;place-items:center;min-height:100vh}
.card{background:white;padding:28px;border-radius:18px;width:min(88%,360px);box-shadow:0 8px 30px #0001}
h2{color:#176b45;margin-top:0}input,button{box-sizing:border-box;width:100%;padding:13px;margin:8px 0;border:1px solid #ddd;border-radius:10px;font-size:16px}
button{background:#176b45;color:white;border:0;font-weight:bold}
small{color:#666}.err{color:#b22}
</style><div class="card"><h2>سلمية نحنا معك</h2><p>تسجيل دخول لوحة التحكم</p>
{% with messages=get_flashed_messages() %}{% for m in messages %}<p class="err">{{m}}</p>{% endfor %}{% endwith %}
<form method="post"><input name="user" placeholder="اسم المستخدم" required><input name="password" type="password" placeholder="كلمة المرور" required><button>دخول</button></form>
<small>لوحة الإدارة الخاصة بالمشروع</small></div></html>
"""

DASH_HTML = """
<!doctype html><html lang="ar" dir="rtl"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>لوحة التحكم</title><style>
body{font-family:Arial;background:#f4f6f4;margin:0;color:#183328}.top{background:#176b45;color:white;padding:18px}
.wrap{max-width:1000px;margin:20px auto;padding:0 12px}.stats{display:flex;gap:10px;flex-wrap:wrap}
.stat,.item{background:white;border-radius:14px;padding:15px;margin:10px 0;box-shadow:0 2px 10px #0000000a}
.stat{flex:1;min-width:110px}.num{font-size:25px;font-weight:bold;color:#176b45}
a,button{background:#176b45;color:white;padding:9px 13px;border-radius:8px;text-decoration:none;border:0;cursor:pointer}
.muted{color:#65756c;font-size:13px}.tag{color:#8a5a00}.actions{display:flex;gap:8px;flex-wrap:wrap}
</style><div class="top"><b>سلمية نحنا معك</b> <span style="float:left"><a href="/logout">تسجيل الخروج</a></span></div>
<div class="wrap"><h2>لوحة التحكم</h2><div class="stats">
<div class="stat">كل الطلبات<div class="num">{{total}}</div></div>
<div class="stat">بانتظار المراجعة<div class="num">{{pending}}</div></div>
<div class="stat">المقبولة<div class="num">{{approved}}</div></div></div>
<h3>الطلبات والإعلانات</h3>
{% for x in rows %}<div class="item"><b>#{{x.id}} — {{x.category}}</b> <span class="tag">({{x.status}})</span>
<p>{{x.details}}</p><div class="muted">الاسم: {{x.name or 'غير محدد'}} | التواصل: {{x.contact or 'غير محدد'}} | {{x.created_at}}</div>
<div class="actions"><form method="post" action="/status/{{x.id}}/approved"><button>قبول</button></form>
<form method="post" action="/status/{{x.id}}/rejected"><button style="background:#9b3333">رفض</button></form>
<form method="post" action="/delete/{{x.id}}" onsubmit="return confirm('حذف الطلب؟')"><button style="background:#555">حذف</button></form></div></div>{% else %}<p>لا توجد طلبات حتى الآن.</p>{% endfor %}
</div></html>
"""

@APP.route("/")
def home():
    return """<div dir='rtl' style='font-family:Arial;text-align:center;margin:12vh auto;max-width:500px'>
    <h1>سلمية نحنا معك</h1><p>منصة الدليل الخدمي والإعلاني لمدينة سلمية</p>
    <p><a href='/login'>دخول لوحة الإدارة</a></p></div>"""

@APP.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        if not ADMIN_PASSWORD:
            flash("لم يتم ضبط كلمة مرور الإدارة بعد. أضف ADMIN_PASSWORD في Environment.")
        elif request.form.get("user") == ADMIN_USER and request.form.get("password") == ADMIN_PASSWORD:
            session["logged_in"] = True
            return redirect(url_for("dashboard"))
        else:
            flash("اسم المستخدم أو كلمة المرور غير صحيحة.")
    return render_template_string(LOGIN_HTML)

@APP.route("/dashboard")
@login_required
def dashboard():
    with db() as con:
        rows = con.execute("SELECT * FROM submissions ORDER BY id DESC").fetchall()
        total = con.execute("SELECT COUNT(*) FROM submissions").fetchone()[0]
        pending = con.execute("SELECT COUNT(*) FROM submissions WHERE status='pending'").fetchone()[0]
        approved = con.execute("SELECT COUNT(*) FROM submissions WHERE status='approved'").fetchone()[0]
    return render_template_string(DASH_HTML, rows=rows, total=total, pending=pending, approved=approved)

@APP.post("/status/<int:item_id>/<status>")
@login_required
def status(item_id, status):
    if status in ("approved", "rejected"):
        with db() as con:
            con.execute("UPDATE submissions SET status=? WHERE id=?", (status, item_id))
    return redirect(url_for("dashboard"))

@APP.post("/delete/<int:item_id>")
@login_required
def delete(item_id):
    with db() as con:
        con.execute("DELETE FROM submissions WHERE id=?", (item_id,))
    return redirect(url_for("dashboard"))

@APP.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))

init_db()
if BOT_TOKEN and os.getenv("RUN_BOT_POLLING", "true").lower() == "true":
    threading.Thread(target=bot_loop, daemon=True).start()

if __name__ == "__main__":
    APP.run(host="0.0.0.0", port=int(os.getenv("PORT", 5000)))
