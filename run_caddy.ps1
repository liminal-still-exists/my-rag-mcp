$ErrorActionPreference = "Stop"
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $scriptDir
. "$scriptDir\load_local_env.ps1"
Import-ProjectEnv

# LocalSystem 서비스로 실행돼도 기존 사용자 Caddy 저장소를 그대로 사용하게 맞춥니다.
$caddyHome = if ($env:MCP_CADDY_HOME) { $env:MCP_CADDY_HOME } else { $env:USERPROFILE }
$caddyXdgHome = if ($env:MCP_CADDY_XDG_HOME) { $env:MCP_CADDY_XDG_HOME } else { $env:APPDATA }
if (-not $env:MCP_PUBLIC_HOST) {
    if (-not $env:MCP_PUBLIC_BASE_URL) {
        throw ".env에 MCP_PUBLIC_HOST 또는 MCP_PUBLIC_BASE_URL을 설정해 주세요."
    }
    $env:MCP_PUBLIC_HOST = ([Uri]$env:MCP_PUBLIC_BASE_URL).Host
}

$env:HOME = $caddyHome
$env:USERPROFILE = $caddyHome
$env:XDG_DATA_HOME = $caddyXdgHome
$env:XDG_CONFIG_HOME = $caddyXdgHome

& "$scriptDir\bin\caddy.exe" run --config "$scriptDir\Caddyfile" --adapter caddyfile
