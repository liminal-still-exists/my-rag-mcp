param(
    [switch]$Elevated
)

$ErrorActionPreference = "Stop"
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$projectRoot = Split-Path -Parent $scriptDir
Set-Location $projectRoot

function Test-Admin {
    $currentIdentity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = New-Object Security.Principal.WindowsPrincipal($currentIdentity)
    return $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

function Wait-OnExit {
    Read-Host "엔터를 누르면 창이 닫힙니다"
}

if (-not (Test-Admin)) {
    $launcherPath = Join-Path $scriptDir "refresh_data_launcher.ps1"
    $argumentList = @(
        "-NoExit"
        "-ExecutionPolicy"
        "Bypass"
        "-File"
        "`"$launcherPath`""
        "-Elevated"
    )

    Start-Process -FilePath "powershell.exe" -Verb RunAs -ArgumentList $argumentList
    exit 0
}

try {
    & "$scriptDir\refresh_data.ps1"
}
catch {
    Write-Host ""
    Write-Host "오류: $($_.Exception.Message)" -ForegroundColor Red
    Write-Host ""
}
finally {
    Wait-OnExit
}
