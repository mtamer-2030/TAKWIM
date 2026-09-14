# إصلاح مباشر لوصول هواتف التلاميذ: يزيل حظر python من جدار ويندوز، يضيف السماح
# الصحيح (للبرنامج وللمنفذ)، وينظّف القواعد المكرّرة، ويضبط شبكة الراوتر «خاصّة».
#
# السبب المعالَج: عند أوّل تشغيل ينشئ ويندوز قاعدة «حظر» صامتة لـpython.exe تتغلّب
# على سماح المنفذ، فتُحجَب الهواتف رغم أنّ الخادم يعمل والحاسوب يصل إليه.
#
# التشغيل (مرّة واحدة، PowerShell كمسؤول Run as administrator):
#     .\.venv\Scripts\python.exe  ← ليس هنا؛ هذا سكربت PowerShell:
#     powershell -ExecutionPolicy Bypass -File scripts\fix_firewall.ps1
#
#Requires -RunAsAdministrator

$ErrorActionPreference = "Stop"
$Port = 8000

# جذر المشروع = المجلّد الأب لمجلّد scripts، ومسار python داخل البيئة الافتراضيّة.
$root = Split-Path -Parent $PSScriptRoot
$py   = Join-Path $root ".venv\Scripts\python.exe"

Write-Host "==== إصلاح جدار الحماية — PHILO-TECH ====" -ForegroundColor Cyan
if (-not (Test-Path $py)) {
    Write-Host "⚠️  لم يُعثر على python البيئة الافتراضيّة في: $py" -ForegroundColor Yellow
    Write-Host "    سيُكتفى بقاعدة المنفذ. أنشئ البيئة أو صحّح المسار إن لزم." -ForegroundColor Yellow
    $py = $null
}

# (١) نظافة: احذف كلّ قواعد PHILO-TECH السابقة (المكرّرة ١٤ مرّة) لنعيد بناءها نظيفة.
Get-NetFirewallRule -DisplayName "PHILO-TECH*" -ErrorAction SilentlyContinue |
    Remove-NetFirewallRule -ErrorAction SilentlyContinue
Write-Host "✓ حُذفت قواعد PHILO-TECH القديمة (إن وُجدت)."

# (٢) الأهمّ: احذف أيّ قاعدة «حظر» واردة تخصّ python هذا المشروع (تتغلّب على السماح).
if ($py) {
    $removed = 0
    foreach ($rule in (Get-NetFirewallRule -Direction Inbound -Action Block -ErrorAction SilentlyContinue)) {
        $prog = ($rule | Get-NetFirewallApplicationFilter -ErrorAction SilentlyContinue).Program
        if ($prog -and ($prog -ieq $py -or $prog -like "*\takwim\.venv\Scripts\python*")) {
            $rule | Remove-NetFirewallRule -ErrorAction SilentlyContinue
            $removed++
        }
    }
    Write-Host "✓ حُذفت قواعد حظر python المتعارضة: $removed"
}

# (٣) السماح: قاعدة للمنفذ + قاعدة للبرنامج، على كلّ الأنماط (Any).
New-NetFirewallRule -DisplayName "PHILO-TECH $Port" -Direction Inbound -Action Allow `
    -Protocol TCP -LocalPort $Port -Profile Any | Out-Null
Write-Host "✓ سُمِح للمنفذ TCP $Port (كلّ الأنماط)."
if ($py) {
    New-NetFirewallRule -DisplayName "PHILO-TECH python" -Direction Inbound -Action Allow `
        -Program $py -Profile Any | Out-Null
    Write-Host "✓ سُمِح لبرنامج python: $py"
}

# (٤) اجعل شبكة الراوتر «خاصّة» (الشبكة العامّة تحجب الوارد افتراضاً).
$lan = Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue |
       Where-Object { $_.IPAddress -like "192.168.*" -or $_.IPAddress -like "10.*" } |
       Select-Object -First 1
if ($lan) {
    Set-NetConnectionProfile -InterfaceIndex $lan.InterfaceIndex -NetworkCategory Private -ErrorAction SilentlyContinue
    Write-Host "✓ ضُبطت شبكة $($lan.IPAddress) كـ«خاصّة» (Private)."
}

# (٥) تأكيد: هل المنفذ مفتوحٌ ومستمِع؟
Write-Host ""
if ($lan) {
    $ip = $lan.IPAddress
    Write-Host "عنوان دخول التلاميذ:  http://$ip`:$Port/student" -ForegroundColor Green
    $ok = Test-NetConnection -ComputerName $ip -Port $Port -WarningAction SilentlyContinue
    if ($ok.TcpTestSucceeded) {
        Write-Host "✅ المنفذ مفتوح والخادم يستمع. جرّب العنوان من هاتف على نفس شبكة الراوتر." -ForegroundColor Green
    } else {
        Write-Host "⚠️  المنفذ لا يستجيب بعد — شغّل النظام:  python run.py  ثمّ جرّب من الهاتف." -ForegroundColor Yellow
    }
} else {
    Write-Host "⚠️  لم يُكتشَف عنوان شبكة محلّية — تأكّد من وصل الحاسوب بالراوتر." -ForegroundColor Yellow
}
Write-Host "==== انتهى ====" -ForegroundColor Cyan
