@echo off
REM ============================================================
REM  PHILO-TECH v2 - تشغيل الخادم بنقرة مزدوجة على ويندوز
REM ============================================================
chcp 65001 >nul
cd /d "%~dp0"

echo.
echo   == PHILO-TECH v2 - نظام التقويم والتتبّع البيداغوجي ==
echo.

REM --- البيئة الافتراضية ---
if not exist ".venv\Scripts\activate.bat" goto make_venv
call ".venv\Scripts\activate.bat"
goto have_venv

:make_venv
echo [1/3] انشاء البيئة الافتراضية...
python -m venv .venv
call ".venv\Scripts\activate.bat"
echo [2/3] تثبيت المكتبات...
python -m pip install --upgrade pip >nul
python -m pip install -r requirements.txt

:have_venv
REM --- ملفّ الإعداد أوّل مرّة ---
if not exist "config.ini" copy config.ini.example config.ini >nul

REM --- تشغيل Ollama في الخلفية إن كان مثبّتاً (بلا إلزام) ---
where ollama >nul 2>nul && start "Ollama" /min ollama serve

REM --- محاولة فتح المنفذ 8000 في جدار الحماية (تنجح فقط إن شُغّل كمسؤول) ---
netsh advfirewall firewall add rule name="PHILO-TECH 8000" dir=in action=allow protocol=TCP localport=8000 >nul 2>nul

echo [3/3] تشغيل الخادم...
echo.
echo   لوحة الأستاذ على هذا الحاسوب:  http://localhost:8000/admin
echo   عنوان دخول التلاميذ على الهواتف يظهر تلقائياً أسفله عند الإقلاع.
echo.
echo   (اترك هذه النافذة مفتوحة طوال الحصّة. للايقاف: Ctrl+C)
echo.

python run.py

echo.
echo ============================================================
echo   توقّف الخادم. إن ظهر خطأ أعلاه فانسخه وأرسله.
echo ============================================================
pause
