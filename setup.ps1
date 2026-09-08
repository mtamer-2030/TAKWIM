# ============================================================
#  PHILO-TECH — إعداد الخادم المحلّي على ويندوز (المرحلة 1)
#  يُشغَّل مرّة واحدة كمسؤول. يضبط: الشبكة + جدار الحماية + نسخ احتياطي مجدول.
# ============================================================

#requires -version 5

# — رفع الصلاحيات تلقائياً إن لم يكن مسؤولاً —
$admin = ([Security.Principal.WindowsPrincipal] `
    [Security.Principal.WindowsIdentity]::GetCurrent()
    ).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $admin) {
    Write-Host "إعادة التشغيل بصلاحيات المسؤول..." -ForegroundColor Yellow
    Start-Process powershell "-ExecutionPolicy Bypass -File `"$PSCommandPath`"" -Verb RunAs
    exit
}

$ErrorActionPreference = "Stop"
$StaticIP  = "192.168.1.50"
$Prefix    = 24
$Gateway   = "192.168.1.1"
$Port      = 8000
$ProjectDir = Split-Path -Parent $MyInvocation.MyCommand.Definition
$DataDir    = Join-Path $ProjectDir "data"
$BackupDir  = Join-Path $ProjectDir "backups"

Write-Host "=== PHILO-TECH setup ===" -ForegroundColor Cyan

# — 1) اكتشاف محول الشبكة النشط (المتّصل فعلاً) —
$adapter = Get-NetAdapter -Physical |
    Where-Object { $_.Status -eq "Up" } |
    Sort-Object -Property @{Expression={$_.Name -like "*Ethernet*"}} -Descending |
    Select-Object -First 1
if (-not $adapter) {
    Write-Host "[!] لا محول شبكة نشط. صِل كابل الإيثرنت بالراوتر ثمّ أعِد التشغيل." -ForegroundColor Red
    Read-Host "اضغط Enter للخروج"; exit 1
}
Write-Host "[1/4] المحول المكتشَف: $($adapter.Name)" -ForegroundColor Green

# — 2) تعيين IP ثابت 192.168.1.50 —
try {
    # أزِل أي عنوان/بوابة سابقة على هذا المحول لتفادي التعارض
    Get-NetIPAddress -InterfaceIndex $adapter.ifIndex -AddressFamily IPv4 -ErrorAction SilentlyContinue |
        Where-Object { $_.IPAddress -ne $StaticIP } |
        Remove-NetIPAddress -Confirm:$false -ErrorAction SilentlyContinue
    Remove-NetRoute -InterfaceIndex $adapter.ifIndex -DestinationPrefix "0.0.0.0/0" -Confirm:$false -ErrorAction SilentlyContinue

    if (-not (Get-NetIPAddress -InterfaceIndex $adapter.ifIndex -IPAddress $StaticIP -ErrorAction SilentlyContinue)) {
        New-NetIPAddress -InterfaceIndex $adapter.ifIndex -IPAddress $StaticIP `
            -PrefixLength $Prefix -DefaultGateway $Gateway | Out-Null
    }
    Set-DnsClientServerAddress -InterfaceIndex $adapter.ifIndex -ServerAddresses $Gateway
    Write-Host "[2/4] عنوان ثابت: $StaticIP/$Prefix (بوابة $Gateway)" -ForegroundColor Green
} catch {
    Write-Host "[!] تعذّر ضبط العنوان الثابت: $_" -ForegroundColor Red
    Write-Host "    يمكنك بدل ذلك حجز العنوان في الراوتر (DHCP reservation)." -ForegroundColor Yellow
}

# — 3) فتح المنفذ 8000 للوارد في جدار الحماية —
if (-not (Get-NetFirewallRule -DisplayName "PHILO-TECH $Port" -ErrorAction SilentlyContinue)) {
    New-NetFirewallRule -DisplayName "PHILO-TECH $Port" -Direction Inbound `
        -Protocol TCP -LocalPort $Port -Action Allow -Profile Any | Out-Null
}
Write-Host "[3/4] المنفذ $Port مفتوح للوارد (TCP)" -ForegroundColor Green

# — 4) مهمة مجدولة: نسخ مجلّد data/ احتياطياً يومياً 20:00 —
New-Item -ItemType Directory -Force -Path $BackupDir | Out-Null
$backupCmd = "Copy-Item -Path '$DataDir\*' -Destination (Join-Path '$BackupDir' (Get-Date -Format 'yyyyMMdd-HHmmss')) -Recurse -Force"
$action  = New-ScheduledTaskAction -Execute "powershell.exe" `
    -Argument "-NoProfile -WindowStyle Hidden -Command `"$backupCmd`""
$trigger = New-ScheduledTaskTrigger -Daily -At 8:00PM
try {
    Register-ScheduledTask -TaskName "PHILO-TECH Backup" -Action $action -Trigger $trigger `
        -RunLevel Highest -Force | Out-Null
    Write-Host "[4/4] نسخ احتياطي يومي مجدول 20:00 إلى backups\" -ForegroundColor Green
} catch {
    Write-Host "[!] تعذّر جدولة النسخ الاحتياطي: $_" -ForegroundColor Yellow
}

# — تنبيه Tesseract OCR (لاستخراج نصوص صور الكتب — المرحلة 3) —
$tess = Get-Command tesseract -ErrorAction SilentlyContinue
if (-not $tess) {
    Write-Host ""
    Write-Host "تنبيه: لم يُعثر على Tesseract OCR." -ForegroundColor Yellow
    Write-Host "  لاستخراج النصّ من صور صفحات الكتب المدرسية، ثبّت:" -ForegroundColor Yellow
    Write-Host "  https://github.com/UB-Mannheim/tesseract/wiki (اختر حزمة اللغة العربية 'ara')." -ForegroundColor Yellow
    Write-Host "  بعد التثبيت أضِف مجلّده إلى PATH، ثمّ أعِد فتح PowerShell." -ForegroundColor Yellow
}

Write-Host ""
Write-Host "تمّ الإعداد. شغّل الخادم بـ:  python -m uvicorn main:app --host 0.0.0.0 --port $Port" -ForegroundColor Cyan
Read-Host "اضغط Enter للإغلاق"
