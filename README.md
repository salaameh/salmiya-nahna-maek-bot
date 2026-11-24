# سليمة نحنا معك — Telegram bot + admin panel

## إعداد Render
1. أنشئ Web Service واربط هذا المستودع.
2. Build command: `pip install -r requirements.txt`
3. Start command: `gunicorn app:APP --workers 1 --threads 4 --timeout 120`
4. أضف Environment Variables:
   - `BOT_TOKEN`: رمز البوت من BotFather (سري).
   - `ADMIN_USER`: اسم دخول الإدارة (مثلاً admin).
   - `ADMIN_PASSWORD`: كلمة مرور قوية.
   - `SECRET_KEY`: قيمة عشوائية طويلة.
   - `RUN_BOT_POLLING`: true
5. بعد النشر افتح رابط الخدمة ثم `/login` للدخول للوحة التحكم.

## ملاحظات مهمة
- لا تضع رمز البوت داخل الملفات أو GitHub.
- النسخة الأولية تستخدم SQLite، وقد تُفقد البيانات عند إعادة نشر الخدمة على استضافة بلا قرص دائم. قبل الاستخدام الفعلي يجب ربط PostgreSQL دائم.
- هذه نسخة MVP أولية وليست منصة مكتملة؛ اختبرها قبل الإعلان عنها.
