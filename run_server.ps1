$ErrorActionPreference = "Stop"
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $scriptDir
. "$scriptDir\load_local_env.ps1"
Import-ProjectEnv
if (-not $env:MCP_PORT) {
    $env:MCP_PORT = "18000"
}
if (-not $env:MCP_PUBLIC_BASE_URL) {
    throw ".env에 MCP_PUBLIC_BASE_URL을 설정해 주세요."
}
if (-not $env:MCP_OAUTH_APPROVAL_SECRET) {
    throw ".env에 MCP_OAUTH_APPROVAL_SECRET를 설정해 주세요."
}
& "$scriptDir\.venv\Scripts\python.exe" "$scriptDir\mcp_server.py"
