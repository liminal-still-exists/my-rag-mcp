$ErrorActionPreference = "Stop"

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$projectRoot = Split-Path -Parent $scriptDir
$serverScript = Join-Path $projectRoot "run_server.ps1"
$caddyScript = Join-Path $projectRoot "run_caddy.ps1"
$logsDir = Join-Path $projectRoot "logs"
$taskName = "\my_rag_mcp"

function Ensure-Admin {
    $currentIdentity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = New-Object Security.Principal.WindowsPrincipal($currentIdentity)
    if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
        throw "관리자 권한 PowerShell에서 실행해야 합니다."
    }
}

function Find-NssmPath {
    $command = Get-Command nssm.exe -ErrorAction SilentlyContinue
    if ($command) {
        return $command.Source
    }

    $candidate = Get-ChildItem "C:\Users\*\AppData\Local\Microsoft\WinGet\Packages" -Filter nssm.exe -Recurse -ErrorAction SilentlyContinue |
        Select-Object -ExpandProperty FullName -First 1

    if ($candidate) {
        return $candidate
    }

    throw "nssm.exe를 찾을 수 없습니다. WinGet 또는 PATH를 확인해 주세요."
}

function Remove-ServiceIfExists {
    param([string]$Name)

    $existing = Get-Service -Name $Name -ErrorAction SilentlyContinue
    if ($existing) {
        & $nssm stop $Name confirm | Out-Null
        & $nssm remove $Name confirm | Out-Null
    }
}

function Install-NssmService {
    param(
        [string]$Name,
        [string]$DisplayName,
        [string]$ScriptPath
    )

    & $nssm install $Name "powershell.exe" "-NoProfile -ExecutionPolicy Bypass -File `"$ScriptPath`"" | Out-Null
    & $nssm set $Name DisplayName $DisplayName | Out-Null
    & $nssm set $Name AppDirectory $projectRoot | Out-Null
    & $nssm set $Name Start SERVICE_AUTO_START | Out-Null
    & $nssm set $Name AppStdout (Join-Path $logsDir "$Name.out.log") | Out-Null
    & $nssm set $Name AppStderr (Join-Path $logsDir "$Name.err.log") | Out-Null
    & $nssm set $Name AppRotateFiles 1 | Out-Null
    & $nssm set $Name AppRotateOnline 1 | Out-Null
}

Ensure-Admin

New-Item -ItemType Directory -Force -Path $logsDir | Out-Null
$nssm = Find-NssmPath

schtasks /End /TN $taskName 2>$null | Out-Null
schtasks /Change /TN $taskName /DISABLE 2>$null | Out-Null

Remove-ServiceIfExists -Name "MyRagMcpServer"
Remove-ServiceIfExists -Name "MyRagCaddy"

Install-NssmService -Name "MyRagMcpServer" -DisplayName "My RAG MCP Server" -ScriptPath $serverScript
Install-NssmService -Name "MyRagCaddy" -DisplayName "My RAG Caddy" -ScriptPath $caddyScript

& $nssm start MyRagMcpServer | Out-Null
Start-Sleep -Seconds 2
& $nssm start MyRagCaddy | Out-Null

Get-Service MyRagMcpServer,MyRagCaddy | Select-Object Name,Status,StartType
