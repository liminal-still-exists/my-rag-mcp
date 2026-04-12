$ErrorActionPreference = "Stop"

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$projectRoot = Split-Path -Parent $scriptDir
$logsDir = Join-Path $projectRoot "logs"
$legacyRoot = $projectRoot
$keepPerPattern = 10

$patternGroups = @(
    "MyRagMcpServer.err-*.log",
    "MyRagMcpServer.out-*.log"
)

function Remove-OldLogs {
    param(
        [string]$BasePath
    )

    if (-not (Test-Path $BasePath)) {
        return
    }

    foreach ($pattern in $patternGroups) {
        $files = Get-ChildItem -Path $BasePath -File -Filter $pattern -ErrorAction SilentlyContinue |
            Sort-Object LastWriteTime -Descending

        if ($files.Count -le $keepPerPattern) {
            continue
        }

        $files | Select-Object -Skip $keepPerPattern | Remove-Item -Force -ErrorAction SilentlyContinue
    }
}

Remove-OldLogs -BasePath $logsDir
Remove-OldLogs -BasePath $legacyRoot
