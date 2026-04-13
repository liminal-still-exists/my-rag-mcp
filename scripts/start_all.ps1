$ErrorActionPreference = "Stop"

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$projectRoot = Split-Path -Parent $scriptDir
$envLoader = Join-Path $scriptDir "load_local_env.ps1"
. $envLoader
Import-ProjectEnv
$runtimeDir = Join-Path $projectRoot "runtime"
$logsDir = Join-Path $projectRoot "logs"
$serverPidFile = Join-Path $runtimeDir "mcp_server.pid"
$serverLogFile = Join-Path $logsDir "mcp_server_start.log"

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

$serverPort = if ($env:MCP_PORT) { [int]$env:MCP_PORT } else { 18444 }
$serverPortsInUse = Get-ListeningProcessId -Ports @($serverPort)

if ($serverPortsInUse.Count -gt 0) {
    Open-MonitorWindow -Title "mcp_server" -Message "mcp_server가 이미 실행 중입니다. 새로 띄우지 않고 로그만 표시합니다." -LogFile $serverLogFile
    return
}

Stop-RecordedProcess -PidFile $serverPidFile

Remove-Item $serverLogFile -Force -ErrorAction SilentlyContinue

$serverCommand = @"
Set-Location '$scriptDir'
`$Host.UI.RawUI.WindowTitle = 'mcp_server'
. '$envLoader'
Import-ProjectEnv
if (-not `$env:MCP_PORT) { `$env:MCP_PORT='18444' }
if (-not `$env:MCP_OAUTH_APPROVAL_SECRET) { throw '.env에 MCP_OAUTH_APPROVAL_SECRET를 설정해 주세요.' }
Set-Location '$projectRoot'
& '.\.venv\Scripts\python.exe' '.\mcp_server.py'
"@

$serverProcess = Start-Process -FilePath "powershell.exe" -ArgumentList "-NoExit", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", $serverCommand -PassThru
$serverProcess.Id | Set-Content $serverPidFile
