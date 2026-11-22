# start_easydiffusion.ps1 — поднимает локальный EasyDiffusion (A1111-совместимый API)
# для MCP-инструмента image_generate. Вызывается из run_server.bat при старте.
# Если сервис уже слушает порт — ничего не делает. PID запущенного процесса пишется в
# data_unified/easydiffusion.pid, чтобы stop_server.ps1 погасил именно его.
$ErrorActionPreference = 'SilentlyContinue'

$dir = 'E:\Easy-Diffusion'
$port = 9000
$autostart = '1'
$waitSeconds = 180
$envFile = Join-Path $PSScriptRoot '.env'

if (Test-Path $envFile) {
    Get-Content $envFile -Encoding UTF8 | ForEach-Object {
        if ($_ -match '^\s*(EASYDIFFUSION_DIR|EASYDIFFUSION_PORT|EASYDIFFUSION_AUTOSTART|EASYDIFFUSION_START_TIMEOUT)\s*=\s*(.*?)\s*$') {
            $key = $Matches[1]
            $val = $Matches[2]
            if ($key -eq 'EASYDIFFUSION_DIR') { $dir = $val }
            elseif ($key -eq 'EASYDIFFUSION_PORT') { $port = [int]$val }
            elseif ($key -eq 'EASYDIFFUSION_AUTOSTART') { $autostart = $val }
            elseif ($key -eq 'EASYDIFFUSION_START_TIMEOUT') { $waitSeconds = [int]$val }
        }
    }
}

if ($autostart -ne '1') {
    Write-Host '[image] EASYDIFFUSION_AUTOSTART != 1 — автозапуск отключён'
    exit 0
}
if (Get-NetTCPConnection -State Listen -LocalPort $port -ErrorAction SilentlyContinue) {
    Write-Host "[image] EasyDiffusion уже слушает :$port — пропускаем запуск"
    exit 0
}
if (-not (Test-Path (Join-Path $dir 'scripts\check_modules.py'))) {
    Write-Host "[image] Не найден $dir\scripts\check_modules.py — проверьте EASYDIFFUSION_DIR"
    exit 1
}

$envDir = Join-Path $dir 'installer_files\env'
if (-not (Test-Path (Join-Path $envDir 'python.exe'))) {
    $envDir = Join-Path $dir 'stable-diffusion\env'
}
$python = Join-Path $envDir 'python.exe'
if (-not (Test-Path $python)) {
    Write-Host "[image] Не найден Python EasyDiffusion: $python"
    exit 1
}

# Окружение такое же, как в «Start Stable Diffusion UI.cmd» → scripts/on_sd_start.bat.
$env:INSTALL_ENV_DIR = $envDir
$env:PYTHONPATH = Join-Path $envDir 'lib\site-packages'
$env:PYTHONNOUSERSITE = '1'
$env:HF_HUB_DISABLE_SYMLINKS_WARNING = 'true'
$env:SD_UI_PATH = Join-Path $dir 'ui'

$logDir = Join-Path $PSScriptRoot 'logs'
New-Item -ItemType Directory -Path $logDir -Force | Out-Null
$outLog = Join-Path $logDir 'easydiffusion.log'
$errLog = Join-Path $logDir 'easydiffusion.err.log'
$pidFile = Join-Path $PSScriptRoot 'data_unified\easydiffusion.pid'
New-Item -ItemType Directory -Path (Split-Path $pidFile) -Force | Out-Null

Write-Host "[image] Starting EasyDiffusion on :$port (лог: logs\easydiffusion.log)..."
$proc = Start-Process -FilePath $python `
    -ArgumentList @('scripts\check_modules.py', '--launch-uvicorn') `
    -WorkingDirectory $dir -WindowStyle Hidden -PassThru `
    -RedirectStandardOutput $outLog -RedirectStandardError $errLog
Set-Content -Path $pidFile -Value $proc.Id -Encoding ASCII

for ($i = 0; $i -lt $waitSeconds; $i++) {
    if (Get-NetTCPConnection -State Listen -LocalPort $port -ErrorAction SilentlyContinue) {
        Write-Host "[image] EasyDiffusion готов: http://127.0.0.1:$port (PID $($proc.Id))"
        exit 0
    }
    if ($proc.HasExited) {
        Write-Host "[image] EasyDiffusion завершился с кодом $($proc.ExitCode). См. $errLog"
        Remove-Item $pidFile -Force -ErrorAction SilentlyContinue
        exit 1
    }
    Start-Sleep -Seconds 1
}

Write-Host "[image] ВНИМАНИЕ: порт $port не поднялся за ${waitSeconds}с. См. $errLog"
exit 1
