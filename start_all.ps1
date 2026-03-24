$ErrorActionPreference = "Stop"

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$envLoader = Join-Path $scriptDir "load_local_env.ps1"
. $envLoader
Import-ProjectEnv
$runtimeDir = Join-Path $scriptDir "runtime"
$logsDir = Join-Path $scriptDir "logs"
$serverPidFile = Join-Path $runtimeDir "mcp_server.pid"
$caddyPidFile = Join-Path $runtimeDir "caddy.pid"
$serverLogFile = Join-Path $logsDir "mcp_server_start.log"
$caddyLogFile = Join-Path $logsDir "caddy_start.log"

New-Item -ItemType Directory -Force -Path $runtimeDir | Out-Null
New-Item -ItemType Directory -Force -Path $logsDir | Out-Null

function Get-ListeningProcessId {
    param(
        [int[]]$Ports
    )

    $connections = Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue |
        Where-Object { $_.LocalPort -in $Ports } |
        Select-Object -ExpandProperty OwningProcess -Unique

    return @($connections)
}

function Open-MonitorWindow {
    param(
        [string]$Title,
        [string]$Message,
        [string]$LogFile
    )

    $monitorCommand = @"
`$Host.UI.RawUI.WindowTitle = '$Title'
Write-Host '$Message'
Write-Host ''
if (Test-Path '$LogFile') {
    Get-Content -Path '$LogFile' -Wait
} else {
    Write-Host '로그 파일이 아직 없습니다. Ctrl+C로 창을 닫을 수 있습니다.'
}
"@

    Start-Process -FilePath "powershell.exe" -ArgumentList "-NoExit", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", $monitorCommand | Out-Null
}

function Stop-RecordedProcess {
    param(
        [string]$PidFile
    )

    if (-not (Test-Path $PidFile)) {
        return
    }

    $rawPid = (Get-Content $PidFile -ErrorAction SilentlyContinue | Select-Object -First 1).Trim()
    if (-not $rawPid) {
        Remove-Item $PidFile -Force -ErrorAction SilentlyContinue
        return
    }

    $process = Get-Process -Id ([int]$rawPid) -ErrorAction SilentlyContinue
    if ($process) {
        Stop-Process -Id $process.Id -Force -ErrorAction SilentlyContinue
        Start-Sleep -Seconds 1
    }

    Remove-Item $PidFile -Force -ErrorAction SilentlyContinue
}

$serverPortsInUse = Get-ListeningProcessId -Ports @(18000)
$caddyPortsInUse = Get-ListeningProcessId -Ports @(2019, 443)

if ($serverPortsInUse.Count -gt 0 -or $caddyPortsInUse.Count -gt 0) {
    if ($serverPortsInUse.Count -gt 0) {
        Open-MonitorWindow -Title "mcp_server" -Message "mcp_server가 이미 실행 중입니다. 새로 띄우지 않고 로그만 표시합니다." -LogFile $serverLogFile
    }
    if ($caddyPortsInUse.Count -gt 0) {
        Open-MonitorWindow -Title "caddy" -Message "caddy가 이미 실행 중입니다. 새로 띄우지 않고 로그만 표시합니다." -LogFile $caddyLogFile
    }
    return
}

Stop-RecordedProcess -PidFile $serverPidFile
Stop-RecordedProcess -PidFile $caddyPidFile

Remove-Item $serverLogFile,$caddyLogFile -Force -ErrorAction SilentlyContinue

$serverCommand = @"
Set-Location '$scriptDir'
`$Host.UI.RawUI.WindowTitle = 'mcp_server'
. '$envLoader'
Import-ProjectEnv
if (-not `$env:MCP_PORT) { `$env:MCP_PORT='18000' }
if (-not `$env:MCP_PUBLIC_BASE_URL) { throw '.env에 MCP_PUBLIC_BASE_URL을 설정해 주세요.' }
if (-not `$env:MCP_OAUTH_APPROVAL_SECRET) { throw '.env에 MCP_OAUTH_APPROVAL_SECRET를 설정해 주세요.' }
& '.\.venv\Scripts\python.exe' '.\mcp_server.py'
"@

$caddyCommand = @"
Set-Location '$scriptDir'
`$Host.UI.RawUI.WindowTitle = 'caddy'
. '$envLoader'
Import-ProjectEnv
`$caddyHome = if (`$env:MCP_CADDY_HOME) { `$env:MCP_CADDY_HOME } else { `$env:USERPROFILE }
`$caddyXdgHome = if (`$env:MCP_CADDY_XDG_HOME) { `$env:MCP_CADDY_XDG_HOME } else { `$env:APPDATA }
if (-not `$env:MCP_PUBLIC_HOST) {
    if (-not `$env:MCP_PUBLIC_BASE_URL) { throw '.env에 MCP_PUBLIC_HOST 또는 MCP_PUBLIC_BASE_URL을 설정해 주세요.' }
    `$env:MCP_PUBLIC_HOST = ([Uri]`$env:MCP_PUBLIC_BASE_URL).Host
}
`$env:HOME = `$caddyHome
`$env:USERPROFILE = `$caddyHome
`$env:XDG_DATA_HOME = `$caddyXdgHome
`$env:XDG_CONFIG_HOME = `$caddyXdgHome
& '.\bin\caddy.exe' 'run'
"@

$serverProcess = Start-Process -FilePath "powershell.exe" -ArgumentList "-NoExit", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", $serverCommand -PassThru
$serverProcess.Id | Set-Content $serverPidFile

Start-Sleep -Seconds 2

$caddyProcess = Start-Process -FilePath "powershell.exe" -ArgumentList "-NoExit", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", $caddyCommand -PassThru
$caddyProcess.Id | Set-Content $caddyPidFile
