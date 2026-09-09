@echo off
REM ============================================================
REM  PHILO-TECH v2 - تشغيل الخادم بنقرة مزدوجة على ويندوز
REM ============================================================
chcp 65001 >nul
cd /d "%~dp0"

echo.
echo   == PHILO-TECH v2 - نظام التقويم والتتبّع البيداغوجي ==
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

REM تشغيل محرّك الذكاء الاصطناعي المحلّي (Ollama) تلقائياً إن كان مثبّتاً وغير مشغّل
where ollama >nul 2>nul
if %errorlevel%==0 (
  tasklist /fi "imagename eq ollama.exe" 2>nul | find /i "ollama.exe" >nul
  if errorlevel 1 (
    echo تشغيل محرّك الذكاء الاصطناعي (Ollama) في الخلفية...
    start "Ollama" /min ollama serve
  )
)

REM محاولة فتح المنفذ 8000 في جدار الحماية (تنجح فقط إن شُغّل الملفّ كمسؤول)
netsh advfirewall firewall show rule name="PHILO-TECH 8000" >nul 2>nul
if errorlevel 1 (
  netsh advfirewall firewall add rule name="PHILO-TECH 8000" dir=in action=allow protocol=TCP localport=8000 >nul 2>nul
  if errorlevel 1 (
    echo [تنبيه] لم أستطع فتح المنفذ 8000 في جدار الحماية ^(يلزم تشغيل كمسؤول^).
    echo         إن لم تصل الهواتف، شغّل setup.ps1 مرّة واحدة كمسؤول.
  )
)

echo [3/3] تشغيل الخادم...
echo.
echo   لوحة الأستاذ على هذا الحاسوب:  http://localhost:8000/admin
echo   عنوان دخول التلاميذ على الهواتف يظهر تلقائياً أسفله عند الإقلاع.
echo.
echo   (اترك هذه النافذة مفتوحة طوال الحصّة. للايقاف: Ctrl+C)
echo.

python run.py
pause
