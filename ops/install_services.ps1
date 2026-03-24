$ErrorActionPreference = "Stop"

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$projectRoot = Split-Path -Parent $scriptDir
$serverScript = Join-Path $projectRoot "run_server.ps1"
$caddyScript = Join-Path $projectRoot "run_caddy.ps1"
$taskName = "\my_rag_mcp"

function Ensure-Admin {
    $currentIdentity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = New-Object Security.Principal.WindowsPrincipal($currentIdentity)
    if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
        throw "관리자 권한 PowerShell에서 실행해야 합니다."
    }
}

function Ensure-Service {
    param(
        [string]$Name,
        [string]$DisplayName,
        [string]$CommandLine
    )

    $existing = Get-Service -Name $Name -ErrorAction SilentlyContinue
    if ($existing) {
        sc.exe stop $Name | Out-Null
        sc.exe config $Name start= auto binPath= $CommandLine DisplayName= $DisplayName | Out-Null
    }
    else {
        sc.exe create $Name binPath= $CommandLine start= auto DisplayName= $DisplayName | Out-Null
    }
}

Ensure-Admin

schtasks /End /TN $taskName 2>$null | Out-Null
schtasks /Change /TN $taskName /DISABLE 2>$null | Out-Null

$serverBinPath = "powershell.exe -NoProfile -ExecutionPolicy Bypass -File `"$serverScript`""
$caddyBinPath = "powershell.exe -NoProfile -ExecutionPolicy Bypass -File `"$caddyScript`""

Ensure-Service -Name "MyRagMcpServer" -DisplayName "My RAG MCP Server" -CommandLine $serverBinPath
Ensure-Service -Name "MyRagCaddy" -DisplayName "My RAG Caddy" -CommandLine $caddyBinPath

sc.exe start MyRagMcpServer | Out-Null
Start-Sleep -Seconds 2
sc.exe start MyRagCaddy | Out-Null

Write-Host "서비스 설치 및 시작 완료"
