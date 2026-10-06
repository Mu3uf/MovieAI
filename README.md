# CineMind - AI Movie Recommendation Assistant (100% free stack)

Flask + LangChain tools + Groq (free LLM) + Supabase (Auth, Postgres, RLS, pgvector) + TMDB.

## تشغيل سريع
```bash
python -m venv venv
venv\Scripts\activate          # Windows   |   source venv/bin/activate  (Mac/Linux)
pip install -r requirements.txt
copy .env.example .env         # ثم عبّي المفاتيح  (Mac/Linux: cp .env.example .env)
```
1. شغّل ملف `supabase_schema.sql` داخل Supabase → SQL Editor (مرة وحدة).
2. عبّي المفاتيح في `.env` (SUPABASE_URL, SUPABASE_ANON_KEY, SUPABASE_SERVICE_ROLE_KEY, TMDB_API_KEY, LLM_API_KEY).
3. عبّي الداتا (مرة وحدة):
   ```bash
   python -m jobs.sync_movies --trending --new --movies 3
   ```
4. شغّل الموقع: `python app.py`  ثم افتح http://localhost:5000
5. فحص الإعدادات: http://localhost:5000/api/health

## الاختبارات
```bash
pytest -m "not live"      # بدون مفاتيح (منطق الترتيب والفلترة والذاكرة)
pytest                    # كامل (يحتاج المفاتيح)
```

## النشر المجاني
Render.com (Web Service مجاني): Build `pip install -r requirements.txt` ، Start `gunicorn app:app --workers 1 --threads 8 --timeout 60`، وأضف متغيرات `.env` في Environment.
المزامنة التلقائية: `.github/workflows/sync.yml` (GitHub Actions مجاني) — أضف الـ secrets في إعدادات الريبو.
