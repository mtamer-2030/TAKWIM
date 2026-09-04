@echo off
REM ============================================================
REM  مِحَكّ (mihakk) - تشغيل الخادم بنقرة مزدوجة على ويندوز
REM ============================================================
chcp 65001 >nul
cd /d "%~dp0"

echo.
echo   == محَكّ - نظام التقويم الصفّي ==
echo.

REM إنشاء البيئة الافتراضية أول مرة
if not exist ".venv\" (
  echo [1/3] انشاء البيئة الافتراضية...
  python -m venv .venv
  call ".venv\Scripts\activate.bat"
  echo [2/3] تثبيت المكتبات...
  python -m pip install --upgrade pip >nul
  python -m pip install -r requirements.txt
) else (
  call ".venv\Scripts\activate.bat"
)

REM نسخ ملفّ الإعداد أول مرة
if not exist "config.ini" (
  echo [!] لم يُعثر على config.ini - نسخ من المثال. عدّل كلمة السرّ والاعدادات.
  copy config.ini.example config.ini >nul
)

echo [3/3] تشغيل الخادم على http://0.0.0.0:8000
echo.
echo   افتح لوحة الأستاذ:   http://192.168.1.50:8000/teacher
echo   رمز دخول التلاميذ:    http://192.168.1.50:8000
echo.
echo   (اترك هذه النافذة مفتوحة طوال الحصّة. للايقاف: Ctrl+C)
echo.

python run.py
pause
