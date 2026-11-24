import os, sqlite3, threading, time, requests
from functools import wraps
from flask import Flask, request, redirect, url_for, session, render_template_string, flash

APP = Flask(__name__)
APP.secret_key = os.getenv("SECRET_KEY", "change-this-secret-before-deploy")
DB_PATH = os.getenv("DB_PATH", "salmiya.db")
BOT_TOKEN = os.getenv("BOT_TOKEN", "")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "change-me-now")
ADMIN_USER = os.getenv("ADMIN_USER", "admin")
PUBLIC_URL = os.getenv("PUBLIC_URL", "").rstrip("/")
API = f"https://api.telegram.org/bot{BOT_TOKEN}" if BOT_TOKEN else ""

def db():
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    return con

def init_db():
    with db() as con:
        con.execute("""CREATE TABLE IF NOT EXISTS requests (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_id TEXT, name TEXT, shop TEXT, category TEXT,
            phone TEXT, details TEXT, status TEXT DEFAULT 'pending',
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )""")
        con.execute("""CREATE TABLE IF NOT EXISTS subscribers (
            chat_id TEXT PRIMARY KEY, first_name TEXT, username TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )""")

def send_message(chat_id, text, keyboard=None):
    if not API: return
    payload = {"chat_id": chat_id, "text": text}
    if keyboard: payload["reply_markup"] = keyboard
    try: requests.post(API + "/sendMessage", json=payload, timeout=15)
    except requests.RequestException: pass

def main_keyboard():
    return {"keyboard":[
        [{"text":"🏪 تسجيل محل"},{"text":"📍 دليل المحلات"}],
        [{"text":"📰 آخر الأخبار"},{"text":"📣 إعلان أو خدمة"}],
        [{"text":"ℹ️ حول المنصة"},{"text":"☎️ تواصل معنا"}]
    ],"resize_keyboard":True}

def handle_update(update):
    msg = update.get("message") or update.get("edited_message")
    if not msg or not msg.get("chat"): return
    chat_id = str(msg["chat"]["id"])
    user = msg.get("from", {})
    text = (msg.get("text") or "").strip()
    with db() as con:
        con.execute("INSERT OR IGNORE INTO subscribers(chat_id,first_name,username) VALUES(?,?,?)",
                    (chat_id, user.get("first_name",""), user.get("username","")))
    if text in ("/start", "رجوع", "القائمة الرئيسية"):
        send_message(chat_id, "أهلاً بك في منصة سليمة نحنا معك 💚\nمنصتك لخدمات وأخبار ودليل مدينة سلمية.", main_keyboard())
    elif text == "🏪 تسجيل محل":
        send_message(chat_id, "لتسجيل محلك، أرسل المعلومات برسالة واحدة بهذا الترتيب:\nاسم المحل:\nالتصنيف:\nرقم التواصل:\nالعنوان:\nوصف مختصر:\n\nسيصل طلبك للإدارة للمراجعة قبل النشر.")
        with db() as con:
            con.execute("UPDATE subscribers SET first_name=COALESCE(first_name,'') WHERE chat_id=?", (chat_id,))
    elif text == "📍 دليل المحلات":
        send_message(chat_id, "نعمل على تجهيز دليل المحلات. ستظهر هنا المحلات التي وافقت الإدارة على نشرها.")
    elif text == "📰 آخر الأخبار":
        send_message(chat_id, "قسم الأخبار قيد التجهيز. تابع المنصة ليصلك كل جديد.")
    elif text == "📣 إعلان أو خدمة":
        send_message(chat_id, "لإرسال إعلان أو خدمة، اكتب التفاصيل ورقم التواصل برسالة واحدة.")
    elif text == "ℹ️ حول المنصة":
        send_message(chat_id, "سليمة نحنا معك — منصة خدمية وترفيهية وإعلانية لأهالي سلمية وريفها.")
    elif text == "☎️ تواصل معنا":
        send_message(chat_id, "يمكنك التواصل مع إدارة المنصة عبر مراسلة هذا البوت.")
    else:
        # Treat multi-line shop/application messages as pending submissions.
        if any(k in text for k in ["اسم المحل:", "اسم المحل :", "التصنيف:"]):
            lines = [x.strip() for x in text.splitlines() if x.strip()]
            def value(prefix):
                for line in lines:
                    if line.startswith(prefix): return line.split(":",1)[-1].strip()
                return ""
            with db() as con:
                con.execute("""INSERT INTO requests(chat_id,name,shop,category,phone,details)
                               VALUES(?,?,?,?,?,?)""",
                            (chat_id, user.get("first_name",""), value("اسم المحل"), value("اسم المحل"),
                             value("رقم التواصل"), text))
            send_message(chat_id, "وصل طلب تسجيل محلك بنجاح ✅\nسيتم مراجعته من الإدارة قبل إضافته إلى الدليل.")
        else:
            send_message(chat_id, "وصلتنا رسالتك 💚\nاستخدم أزرار القائمة أو أرسل طلب تسجيل المحل بالتنسيق الموضح.")

def poll_updates():
    offset = None
    while True:
        if not BOT_TOKEN:
            time.sleep(10); continue
        try:
            params = {"timeout": 25}
            if offset is not None: params["offset"] = offset
            r = requests.get(API + "/getUpdates", params=params, timeout=35)
            r.raise_for_status()
            for upd in r.json().get("result", []):
                offset = upd["update_id"] + 1
                handle_update(upd)
        except Exception:
            time.sleep(5)

def login_required(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if not session.get("admin"): return redirect(url_for("login"))
        return fn(*args, **kwargs)
    return wrapper

LOGIN = """<!doctype html><html lang=ar dir=rtl><meta name=viewport content='width=device-width,initial-scale=1'>
<title>دخول الإدارة</title><style>body{font-family:Arial;background:#f2f6f4;padding:25px}.box{max-width:400px;margin:12vh auto;background:white;padding:25px;border-radius:18px}input,button{width:100%;padding:13px;margin:8px 0;border-radius:9px;border:1px solid #ccc}button{background:#126b48;color:white;border:0}</style>
<div class=box><h2>سليمة نحنا معك</h2><p>تسجيل دخول الإدارة</p><form method=post><input name=user placeholder='اسم المستخدم' required><input name=password type=password placeholder='كلمة المرور' required><button>دخول</button></form>{{msg}}</div></html>"""

DASH = """<!doctype html><html lang=ar dir=rtl><meta name=viewport content='width=device-width,initial-scale=1'>
<title>لوحة الإدارة</title><style>body{font-family:Arial;background:#f3f6f4;margin:0;color:#20352b}.top{background:#126b48;color:white;padding:20px}.wrap{padding:16px;max-width:1000px;margin:auto}.card{background:white;border-radius:14px;padding:16px;margin:12px 0;box-shadow:0 2px 8px #0001}a,button{background:#126b48;color:white;padding:9px 13px;border:0;border-radius:8px;text-decoration:none;margin:3px;display:inline-block}.muted{color:#667}.pill{background:#e8f5ee;color:#126b48;padding:5px 9px;border-radius:20px}</style>
<div class=top><h2>سليمة نحنا معك</h2><div>لوحة تحكم الإدارة</div></div><div class=wrap>
<p><span class=pill>طلبات التسجيل: {{items|length}}</span> <a href='/logout'>تسجيل الخروج</a></p>
{% for x in items %}<div class=card><b>{{x['shop'] or 'طلب تسجيل'}}</b> — {{x['category']}}<p>{{x['details']}}</p><small class=muted>تواصل: {{x['phone']}} | الحالة: {{x['status']}}</small>
{% if x['status']=='pending' %}<p><a href='/approve/{{x["id"]}}'>قبول الطلب</a><a href='/reject/{{x["id"]}}'>رفض الطلب</a></p>{% endif %}</div>{% else %}<div class=card>لا توجد طلبات حالياً.</div>{% endfor %}
</div></html>"""

@APP.route("/")
@login_required
def dashboard():
    with db() as con: items = con.execute("SELECT * FROM requests ORDER BY id DESC").fetchall()
    return render_template_string(DASH, items=items)

@APP.route("/login", methods=["GET","POST"])
def login():
    msg = ""
    if request.method == "POST":
        if request.form.get("user") == ADMIN_USER and request.form.get("password") == ADMIN_PASSWORD:
            session["admin"] = True
            return redirect(url_for("dashboard"))
        msg = "بيانات الدخول غير صحيحة"
    return render_template_string(LOGIN, msg=msg)

@APP.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))

@APP.route("/approve/<int:item_id>")
@login_required
def approve(item_id):
    with db() as con: con.execute("UPDATE requests SET status='approved' WHERE id=?", (item_id,))
    flash("تم قبول الطلب")
    return redirect(url_for("dashboard"))

@APP.route("/reject/<int:item_id>")
@login_required
def reject(item_id):
    with db() as con: con.execute("UPDATE requests SET status='rejected' WHERE id=?", (item_id,))
    return redirect(url_for("dashboard"))

@APP.route("/health")
def health(): return {"status":"ok"}

@APP.route("/telegram-webhook", methods=["POST"])
def telegram_webhook():
    if request.headers.get("X-Telegram-Bot-Api-Secret-Token") != os.getenv("WEBHOOK_SECRET",""):
        return "forbidden", 403
    handle_update(request.get_json(silent=True) or {})
    return "ok"

if __name__ == "__main__":
    init_db()
    if BOT_TOKEN:
        threading.Thread(target=poll_updates, daemon=True).start()
    APP.run(host="0.0.0.0", port=int(os.getenv("PORT", "10000")))
else:
    init_db()
    if BOT_TOKEN and os.getenv("RUN_BOT_POLLING","true").lower() == "true":
        threading.Thread(target=poll_updates, daemon=True).start()
