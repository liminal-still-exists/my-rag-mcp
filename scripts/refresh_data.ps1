$ErrorActionPreference = "Stop"
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$projectRoot = Split-Path -Parent $scriptDir
Set-Location $projectRoot

function Test-Admin {
    $currentIdentity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = New-Object Security.Principal.WindowsPrincipal($currentIdentity)
    return $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

function Wait-OnFailure {
    if ($Host.Name -eq "ConsoleHost") {
        Read-Host "엔터를 누르면 창이 닫힙니다"
    }
}

try {
    if (-not (Test-Admin)) {
        throw "이 스크립트는 관리자 PowerShell에서만 실행할 수 있습니다. 관리자 권한으로 다시 열어 주세요."
    }

    $serviceName = "MyRagMcpServer"
    $service = Get-Service -Name $serviceName -ErrorAction SilentlyContinue

    if ($service) {
        if ($service.Status -eq "Running") {
            Stop-Service -Name $serviceName -ErrorAction Stop
            Write-Host "$serviceName 서비스를 중지했습니다."
        }
        else {
            Write-Host "$serviceName 서비스가 이미 중지 상태입니다."
        }
    }
    else {
        Write-Host "$serviceName 서비스가 없어 중지/재시작을 건너뜁니다."
    }

    & "$projectRoot\.venv\Scripts\python.exe" "$projectRoot\embed.py"
    if ($LASTEXITCODE -ne 0) {
        throw "embed.py 실행에 실패했습니다. 위 오류 메시지를 확인해 주세요."
    }

    if (-not $service) {
        exit 0
    }

    try {
        Start-Service -Name $serviceName -ErrorAction Stop
        Write-Host "$serviceName 서비스를 시작했습니다."
    }
    catch {
        throw "임베딩은 완료됐지만 $serviceName 서비스 시작에 실패했습니다. 관리자 PowerShell에서 다시 실행해 주세요. 원인: $($_.Exception.Message)"
    }
}
catch {
    Write-Host ""
    Write-Host "오류: $($_.Exception.Message)" -ForegroundColor Red
    Write-Host ""
    Wait-OnFailure
    exit 1
}
