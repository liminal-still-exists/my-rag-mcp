import html
import json
import secrets
import time
from pathlib import Path
from dataclasses import dataclass
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from pydantic import AnyHttpUrl
from starlette.requests import Request
from starlette.responses import HTMLResponse, RedirectResponse, Response

from mcp.server.auth.provider import AccessToken, AuthorizationCode, AuthorizationParams, RefreshToken
from mcp.server.auth.provider import OAuthAuthorizationServerProvider
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken

DEFAULT_SCOPE = "mcp"
AUTH_CODE_TTL_SECONDS = 300
ACCESS_TOKEN_TTL_SECONDS = 3600
REFRESH_TOKEN_TTL_SECONDS = 60 * 60 * 24 * 30
PENDING_AUTHORIZATION_TTL_SECONDS = 60 * 10


@dataclass
class PendingAuthorization:
    client_id: str
    state: str | None
    scopes: list[str]
    code_challenge: str
    redirect_uri: AnyHttpUrl
    redirect_uri_provided_explicitly: bool
    resource: str | None
    created_at: float


class LocalOAuthProvider(
    OAuthAuthorizationServerProvider[AuthorizationCode, RefreshToken, AccessToken]
):
    def __init__(self, issuer_url: str, approval_secret: str, state_file: str | Path | None = None):
        self.issuer_url = issuer_url.rstrip("/")
        self.approval_secret = approval_secret
        self.state_file = Path(state_file or "runtime/oauth_state.json")
        self.clients: dict[str, OAuthClientInformationFull] = {}
        self.pending_authorizations: dict[str, PendingAuthorization] = {}
        self.authorization_codes: dict[str, AuthorizationCode] = {}
        self.refresh_tokens: dict[str, RefreshToken] = {}
        self.access_tokens: dict[str, AccessToken] = {}
        self._load_state()

    def _serialize_mapping(self, values: dict[str, Any]) -> dict[str, dict[str, Any]]:
        return {key: value.model_dump(mode="json") for key, value in values.items()}

    def _persist_state(self) -> None:
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "clients": self._serialize_mapping(self.clients),
            "authorization_codes": self._serialize_mapping(self.authorization_codes),
            "refresh_tokens": self._serialize_mapping(self.refresh_tokens),
            "access_tokens": self._serialize_mapping(self.access_tokens),
        }
        temp_file = self.state_file.with_suffix(f"{self.state_file.suffix}.tmp")
        temp_file.write_text(
            json.dumps(payload, ensure_ascii=True, indent=2),
            encoding="utf-8",
        )
        temp_file.replace(self.state_file)

    def _load_state(self) -> None:
        if not self.state_file.exists():
            return

        try:
            payload = json.loads(self.state_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            print(f"Failed to load OAuth state from {self.state_file}: {exc}")
            return

        try:
            self.clients = {
                client_id: OAuthClientInformationFull.model_validate(client_info)
                for client_id, client_info in payload.get("clients", {}).items()
            }
            self.authorization_codes = {
                code: AuthorizationCode.model_validate(code_data)
                for code, code_data in payload.get("authorization_codes", {}).items()
            }
            self.refresh_tokens = {
                token: RefreshToken.model_validate(token_data)
                for token, token_data in payload.get("refresh_tokens", {}).items()
            }
            self.access_tokens = {
                token: AccessToken.model_validate(token_data)
                for token, token_data in payload.get("access_tokens", {}).items()
            }
        except Exception as exc:
            print(f"Failed to restore OAuth state from {self.state_file}: {exc}")
            self.clients = {}
            self.authorization_codes = {}
            self.refresh_tokens = {}
            self.access_tokens = {}

    async def get_client(self, client_id: str) -> OAuthClientInformationFull | None:
        return self.clients.get(client_id)

    async def register_client(self, client_info: OAuthClientInformationFull) -> None:
        if not client_info.client_id:
            raise ValueError("client_id is required")
        self.clients[client_info.client_id] = client_info
        self._persist_state()

    def _cleanup_stale_pending_authorizations(self) -> None:
        now = time.time()
        stale_ids = [
            request_id
            for request_id, pending in self.pending_authorizations.items()
            if now - pending.created_at > PENDING_AUTHORIZATION_TTL_SECONDS
        ]
        for request_id in stale_ids:
            self.pending_authorizations.pop(request_id, None)

    async def authorize(self, client: OAuthClientInformationFull, params: AuthorizationParams) -> str:
        request_id = secrets.token_urlsafe(24)
        self.pending_authorizations[request_id] = PendingAuthorization(
            client_id=client.client_id or "",
            state=params.state,
            scopes=params.scopes or [DEFAULT_SCOPE],
            code_challenge=params.code_challenge,
            redirect_uri=params.redirect_uri,
            redirect_uri_provided_explicitly=params.redirect_uri_provided_explicitly,
            resource=params.resource,
            created_at=time.time(),
        )
        return f"{self.issuer_url}/oauth/approve?request_id={request_id}"

    async def load_authorization_code(
        self,
        client: OAuthClientInformationFull,
        authorization_code: str,
    ) -> AuthorizationCode | None:
        code = self.authorization_codes.get(authorization_code)
        if code and code.client_id == client.client_id:
            return code
        return None

    async def exchange_authorization_code(
        self,
        client: OAuthClientInformationFull,
        authorization_code: AuthorizationCode,
    ) -> OAuthToken:
        self.authorization_codes.pop(authorization_code.code, None)

        access_token_value = secrets.token_urlsafe(32)
        refresh_token_value = secrets.token_urlsafe(32)
        access_token = AccessToken(
            token=access_token_value,
            client_id=client.client_id or "",
            scopes=authorization_code.scopes,
            expires_at=int(time.time()) + ACCESS_TOKEN_TTL_SECONDS,
            resource=authorization_code.resource,
        )
        refresh_token = RefreshToken(
            token=refresh_token_value,
            client_id=client.client_id or "",
            scopes=authorization_code.scopes,
            expires_at=int(time.time()) + REFRESH_TOKEN_TTL_SECONDS,
        )
        self.access_tokens[access_token_value] = access_token
        self.refresh_tokens[refresh_token_value] = refresh_token
        self._persist_state()

        return OAuthToken(
            access_token=access_token_value,
            token_type="Bearer",
            expires_in=ACCESS_TOKEN_TTL_SECONDS,
            refresh_token=refresh_token_value,
            scope=" ".join(authorization_code.scopes),
        )

    async def load_refresh_token(
        self,
        client: OAuthClientInformationFull,
        refresh_token: str,
    ) -> RefreshToken | None:
        token = self.refresh_tokens.get(refresh_token)
        if token is None:
            return None
        if token.expires_at and token.expires_at < int(time.time()):
            self.refresh_tokens.pop(refresh_token, None)
            self._persist_state()
            return None
        if token.client_id == client.client_id:
            return token
        return None

    async def exchange_refresh_token(
        self,
        client: OAuthClientInformationFull,
        refresh_token: RefreshToken,
        scopes: list[str],
    ) -> OAuthToken:
        self.refresh_tokens.pop(refresh_token.token, None)

        access_token_value = secrets.token_urlsafe(32)
        refresh_token_value = secrets.token_urlsafe(32)
        access_token = AccessToken(
            token=access_token_value,
            client_id=client.client_id or "",
            scopes=scopes,
            expires_at=int(time.time()) + ACCESS_TOKEN_TTL_SECONDS,
        )
        new_refresh_token = RefreshToken(
            token=refresh_token_value,
            client_id=client.client_id or "",
            scopes=scopes,
            expires_at=int(time.time()) + REFRESH_TOKEN_TTL_SECONDS,
        )
        self.access_tokens[access_token_value] = access_token
        self.refresh_tokens[refresh_token_value] = new_refresh_token
        self._persist_state()

        return OAuthToken(
            access_token=access_token_value,
            token_type="Bearer",
            expires_in=ACCESS_TOKEN_TTL_SECONDS,
            refresh_token=refresh_token_value,
            scope=" ".join(scopes),
        )

    async def load_access_token(self, token: str) -> AccessToken | None:
        access_token = self.access_tokens.get(token)
        if access_token is None:
            return None
        if access_token.expires_at and access_token.expires_at < int(time.time()):
            self.access_tokens.pop(token, None)
            self._persist_state()
            return None
        return access_token

    async def revoke_token(self, token: AccessToken | RefreshToken) -> None:
        if isinstance(token, AccessToken):
            self.access_tokens.pop(token.token, None)
        if isinstance(token, RefreshToken):
            self.refresh_tokens.pop(token.token, None)
        self._persist_state()

    def render_approval_page(self, request_id: str, error: str = "", action_path: str = "/oauth/approve") -> HTMLResponse:
        escaped_error = html.escape(error)
        error_block = (
            f'<p style="color:#b00020;margin:0 0 16px 0;">{escaped_error}</p>'
            if escaped_error
            else ""
        )
        markup = f"""<!doctype html>
<html lang="ko">
<head>
  <meta charset="utf-8">
  <title>MCP OAuth Approval</title>
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <style>
    body {{
      font-family: system-ui, sans-serif;
      background: #f6f2e8;
      color: #1f1a17;
      max-width: 560px;
      margin: 48px auto;
      padding: 24px;
    }}
    .card {{
      background: #fffdf8;
      border: 1px solid #d9cfbf;
      border-radius: 16px;
      padding: 24px;
      box-shadow: 0 12px 30px rgba(0,0,0,0.06);
    }}
    h1 {{ margin-top: 0; font-size: 24px; }}
    p {{ line-height: 1.6; }}
    input {{
      width: 100%;
      padding: 12px 14px;
      border: 1px solid #c7bba9;
      border-radius: 10px;
      box-sizing: border-box;
      margin: 10px 0 16px 0;
      font-size: 16px;
    }}
    button {{
      width: 100%;
      padding: 12px 14px;
      border: 0;
      border-radius: 10px;
      background: #2f6fed;
      color: white;
      font-size: 16px;
      cursor: pointer;
    }}
  </style>
</head>
<body>
  <div class="card">
    <h1>OAuth 승인</h1>
    <p>Claude 웹 커넥터가 이 MCP 서버에 접근하려고 합니다. 주인님이 정한 승인 비밀번호를 입력하면 연결이 완료됩니다.</p>
    {error_block}
    <form method="post" action="{html.escape(action_path)}">
      <input type="hidden" name="request_id" value="{html.escape(request_id)}">
      <label for="approval_secret">승인 비밀번호</label>
      <input id="approval_secret" name="approval_secret" type="password" autocomplete="current-password" required>
      <button type="submit">승인</button>
    </form>
  </div>
</body>
</html>"""
        return HTMLResponse(markup)

    async def handle_approval(self, request: Request) -> Response:
        self._cleanup_stale_pending_authorizations()

        if request.method == "GET":
            request_id = request.query_params.get("request_id", "")
            if request_id not in self.pending_authorizations:
                return HTMLResponse("Invalid or expired authorization request.", status_code=400)
            return self.render_approval_page(request_id, action_path=request.url.path)

        form = await request.form()
        request_id = str(form.get("request_id", ""))
        approval_secret = str(form.get("approval_secret", ""))
        pending = self.pending_authorizations.get(request_id)
        if pending is None:
            return HTMLResponse("Invalid or expired authorization request.", status_code=400)

        if approval_secret != self.approval_secret:
            return self.render_approval_page(request_id, error="비밀번호가 일치하지 않습니다.", action_path=request.url.path)

        self.pending_authorizations.pop(request_id, None)
        code_value = secrets.token_urlsafe(32)
        authorization_code = AuthorizationCode(
            code=code_value,
            scopes=pending.scopes,
            expires_at=time.time() + AUTH_CODE_TTL_SECONDS,
            client_id=pending.client_id,
            code_challenge=pending.code_challenge,
            redirect_uri=pending.redirect_uri,
            redirect_uri_provided_explicitly=pending.redirect_uri_provided_explicitly,
            resource=pending.resource,
        )
        self.authorization_codes[code_value] = authorization_code
        self._persist_state()

        redirect_target = str(pending.redirect_uri)
        parsed_redirect = urlsplit(redirect_target)
        query_params = parse_qsl(parsed_redirect.query, keep_blank_values=True)
        query_params.append(("code", code_value))
        if pending.state:
            query_params.append(("state", pending.state))
        encoded_query = urlencode(query_params)
        safe_redirect = urlunsplit(
            (
                parsed_redirect.scheme,
                parsed_redirect.netloc,
                parsed_redirect.path,
                encoded_query,
                parsed_redirect.fragment,
            )
        )
        return RedirectResponse(
            url=safe_redirect,
            status_code=302,
            headers={"Cache-Control": "no-store"},
        )
