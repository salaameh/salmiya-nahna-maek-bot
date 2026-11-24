# سلمية نحنا معك — نسخة جديدة

## متغيرات Render المطلوبة
- `ADMIN_USER`: admin
- `ADMIN_PASSWORD`: كلمة مرور تختارها أنت داخل Render، ولا ترسلها لأحد.
- `BOT_TOKEN`: رمز البوت الحالي من BotFather.
- `SECRET_KEY`: قيمة سرية عشوائية (يمكن توليدها من Render).
- `RUN_BOT_POLLING`: true

## النشر
Build: `pip install -r requirements.txt`
Start: `gunicorn app:APP --workers 1 --threads 4 --timeout 120`

## ملاحظات مهمة
هذه نسخة أولية. قاعدة SQLite تحفظ البيانات داخل مساحة الخدمة؛ على الاستضافة المجانية قد تضيع عند إعادة النشر أو إعادة التشغيل. قبل الاستخدام الفعلي يجب ربط قاعدة بيانات دائمة (مثل PostgreSQL) أو مساحة تخزين دائمة مدعومة. شغّل نسخة واحدة فقط من الخدمة لأن البوت يستخدم polling.
