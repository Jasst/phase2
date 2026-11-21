# stop_server.ps1 — останавливает всё, что относится к pulsenet.
# Вызывается из run_server.bat. Убивает:
#   1) python-процессы, запущенные из pulsenet (по командной строке);
#   2) процессы, которые слушают порты приложения: python (чат, MCP) и llama-server
#      (модель чата и эмбеддер, которые запускает чат). Без этого llama-server
#      остаётся жить и держит порт 8090/8091.
# Порты берутся из .env (если заданы), иначе — значения по умолчанию.
$ErrorActionPreference = 'SilentlyContinue'

$ports = @(8000, 8010, 8090, 8091)
$envFile = Join-Path $PSScriptRoot '.env'
if (Test-Path $envFile) {
    Get-Content $envFile -Encoding UTF8 | ForEach-Object {
        if ($_ -match '^\s*(PULSENET_PORT|PULSENET_UNIFIED_PORT|LLAMACPP_PORT|TRIPLEX_EMBED_PORT)\s*=\s*(\d+)') {
            $ports += [int]$Matches[2]
        }
    }
}
$ports = $ports | Sort-Object -Unique

$stopped = @{}
function Stop-One($proc, $why) {
    if (-not $proc -or $stopped.ContainsKey($proc.ProcessId)) { return }
    $stopped[$proc.ProcessId] = $true
    try {
        Stop-Process -Id $proc.ProcessId -Force -ErrorAction Stop
        Write-Host "[stop] PID $($proc.ProcessId) $($proc.Name) — $why"
    } catch {
        Write-Host "[stop] PID $($proc.ProcessId) $($proc.Name) — не удалось остановить: $($_.Exception.Message)"
    }
}

# 1) python-процессы из pulsenet
Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
    Where-Object { $_.CommandLine -match 'pulsenet' } |
    ForEach-Object { Stop-One $_ 'процесс pulsenet' }

# 2) всё, что держит наши порты (только python и llama-server)
foreach ($port in $ports) {
    Get-NetTCPConnection -State Listen -LocalPort $port -ErrorAction SilentlyContinue |
        ForEach-Object {
            $p = Get-CimInstance Win32_Process -Filter "ProcessId=$($_.OwningProcess)"
            if ($p -and $p.Name -match '^(python|llama-server)') { Stop-One $p "слушает порт $port" }
        }
}

# 3) ждём, пока порты освободятся (до 15 секунд)
foreach ($port in $ports) {
    $busy = $true
    for ($i = 0; $i -lt 30 -and $busy; $i++) {
        $busy = [bool](Get-NetTCPConnection -State Listen -LocalPort $port -ErrorAction SilentlyContinue)
        if ($busy) { Start-Sleep -Milliseconds 500 }
    }
    if ($busy) { Write-Host "[stop] ВНИМАНИЕ: порт $port всё ещё занят" }
    else { Write-Host "[stop] порт $port свободен" }
}
exit 0
