$ErrorActionPreference = "Stop"
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$projectRoot = Split-Path -Parent $scriptDir
Set-Location $projectRoot
. "$scriptDir\load_local_env.ps1"
Import-ProjectEnv
if (-not $env:MCP_PORT) {
    $env:MCP_PORT = "18444"
}
if (-not $env:MCP_OAUTH_APPROVAL_SECRET) {
    throw ".env에 MCP_OAUTH_APPROVAL_SECRET를 설정해 주세요."
}
& "$projectRoot\.venv\Scripts\python.exe" "$projectRoot\mcp_server.py"
