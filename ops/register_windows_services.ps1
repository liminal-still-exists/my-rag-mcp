$ErrorActionPreference = "Stop"

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$projectRoot = Split-Path -Parent $scriptDir
$serverScript = Join-Path $projectRoot "run_server.ps1"
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
    $localNssm = Join-Path $projectRoot "bin\nssm.exe"
    if (Test-Path -LiteralPath $localNssm) {
        return $localNssm
    }

    throw "bin\nssm.exe를 찾을 수 없습니다. 프로젝트 로컬 NSSM 바이너리를 확인해 주세요."
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

    sc.exe config $Name start= delayed-auto | Out-Null
    if ($LASTEXITCODE -ne 0) {
        throw "$Name 서비스의 시작 유형을 자동(지연된 시작)으로 설정하지 못했습니다."
    }
}

Ensure-Admin

New-Item -ItemType Directory -Force -Path $logsDir | Out-Null
$nssm = Find-NssmPath

cmd /c "schtasks /Query /TN $taskName >nul 2>&1"
if ($LASTEXITCODE -eq 0) {
    schtasks /End /TN $taskName | Out-Null
    schtasks /Change /TN $taskName /DISABLE | Out-Null
}

Remove-ServiceIfExists -Name "MyRagMcpServer"

Install-NssmService -Name "MyRagMcpServer" -DisplayName "My RAG MCP Server" -ScriptPath $serverScript

& $nssm start MyRagMcpServer | Out-Null

Get-Service MyRagMcpServer | Select-Object Name,Status,StartType
