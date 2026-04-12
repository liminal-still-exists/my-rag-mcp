import json
import os
import secrets
import time
import base64
import hashlib
from uuid import uuid4
from urllib.parse import urlsplit, urlunsplit

import mcp.server.auth.handlers.register as register_module
import mcp.server.auth.handlers.token as token_module
import mcp.server.auth.routes as auth_routes_module
from mcp.server.auth.settings import AuthSettings, ClientRegistrationOptions
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from mcp.shared.auth import OAuthClientInformationFull, OAuthClientMetadata, OAuthMetadata
from pydantic import AnyHttpUrl, ValidationError
from starlette.routing import Route
from starlette.requests import Request
from starlette.responses import JSONResponse
from mcp.server.auth.errors import stringify_pydantic_error
from mcp.server.auth.json_response import PydanticJSONResponse

from notion_store import format_record, get_store
from oauth_provider import DEFAULT_SCOPE, LocalOAuthProvider

HOST = os.environ.get("MCP_HOST", "127.0.0.1")
PORT = int(os.environ.get("MCP_PORT", "18444"))
TRANSPORT = os.environ.get("MCP_TRANSPORT", "streamable-http")
BASE_URL = f"http://{HOST}:{PORT}"
APPROVAL_SECRET = os.environ.get("MCP_OAUTH_APPROVAL_SECRET", "change-me")
MCP_PATH = "/myrag"
BASE_HOST = BASE_URL.removeprefix("https://").removeprefix("http://").rstrip("/")


def is_local_url(url: str) -> bool:
    return "127.0.0.1" in url or "localhost" in url


def validate_runtime_config() -> None:
    if TRANSPORT != "streamable-http":
        return
    if not is_local_url(BASE_URL) and APPROVAL_SECRET == "change-me":
        raise RuntimeError(
            "Set MCP_OAUTH_APPROVAL_SECRET before running streamable-http on a non-local URL."
        )


def patch_mcp_oauth_compat() -> None:
    original_build_metadata = auth_routes_module.build_metadata
    original_create_protected_resource_routes = auth_routes_module.create_protected_resource_routes
    original_token_handle = token_module.TokenHandler.handle

    def normalize_redirect_uri(url: str | None) -> str | None:
        if not url:
            return None
        parsed = urlsplit(url)
        path = parsed.path.rstrip("/") or "/"
        return urlunsplit((parsed.scheme, parsed.netloc, path, parsed.query, parsed.fragment))

    def build_metadata_with_public_client_support(
        issuer_url: AnyHttpUrl,
        service_documentation_url: AnyHttpUrl | None,
        client_registration_options: ClientRegistrationOptions,
        revocation_options,
    ) -> OAuthMetadata:
        metadata = original_build_metadata(
            issuer_url,
            service_documentation_url,
            client_registration_options,
            revocation_options,
        )
        methods = list(metadata.token_endpoint_auth_methods_supported or [])
        if "none" not in methods:
            methods.insert(0, "none")
        metadata.token_endpoint_auth_methods_supported = methods

        if metadata.revocation_endpoint_auth_methods_supported is not None:
            revocation_methods = list(metadata.revocation_endpoint_auth_methods_supported)
            if "none" not in revocation_methods:
                revocation_methods.insert(0, "none")
            metadata.revocation_endpoint_auth_methods_supported = revocation_methods

        return metadata

    async def register_handle_with_public_client_default(
        self,
        request: Request,
    ):
        try:
            body = await request.json()
            client_metadata = OAuthClientMetadata.model_validate(body)
        except ValidationError as validation_error:
            return PydanticJSONResponse(
                content=register_module.RegistrationErrorResponse(
                    error="invalid_client_metadata",
                    error_description=stringify_pydantic_error(validation_error),
                ),
                status_code=400,
            )

        client_id = str(uuid4())

        if client_metadata.token_endpoint_auth_method is None:
            client_metadata.token_endpoint_auth_method = "none"

        client_secret = None
        if client_metadata.token_endpoint_auth_method != "none":
            client_secret = secrets.token_hex(32)

        if client_metadata.scope is None and self.options.default_scopes is not None:
            client_metadata.scope = " ".join(self.options.default_scopes)
        elif client_metadata.scope is not None and self.options.valid_scopes is not None:
            requested_scopes = set(client_metadata.scope.split())
            valid_scopes = set(self.options.valid_scopes)
            if not requested_scopes.issubset(valid_scopes):
                return PydanticJSONResponse(
                    content=register_module.RegistrationErrorResponse(
                        error="invalid_client_metadata",
                        error_description="Requested scopes are not valid: "
                        f"{', '.join(requested_scopes - valid_scopes)}",
                    ),
                    status_code=400,
                )

        if not {"authorization_code", "refresh_token"}.issubset(set(client_metadata.grant_types)):
            return PydanticJSONResponse(
                content=register_module.RegistrationErrorResponse(
                    error="invalid_client_metadata",
                    error_description="grant_types must be authorization_code and refresh_token",
                ),
                status_code=400,
            )

        if "code" not in client_metadata.response_types:
            return PydanticJSONResponse(
                content=register_module.RegistrationErrorResponse(
                    error="invalid_client_metadata",
                    error_description="response_types must include 'code' for authorization_code grant",
                ),
                status_code=400,
            )

        client_id_issued_at = int(time.time())
        client_secret_expires_at = (
            client_id_issued_at + self.options.client_secret_expiry_seconds
            if self.options.client_secret_expiry_seconds is not None
            else None
        )

        client_info = OAuthClientInformationFull(
            client_id=client_id,
            client_id_issued_at=client_id_issued_at,
            client_secret=client_secret,
            client_secret_expires_at=client_secret_expires_at,
            redirect_uris=client_metadata.redirect_uris,
            token_endpoint_auth_method=client_metadata.token_endpoint_auth_method,
            grant_types=client_metadata.grant_types,
            response_types=client_metadata.response_types,
            client_name=client_metadata.client_name,
            client_uri=client_metadata.client_uri,
            logo_uri=client_metadata.logo_uri,
            scope=client_metadata.scope,
            contacts=client_metadata.contacts,
            tos_uri=client_metadata.tos_uri,
            policy_uri=client_metadata.policy_uri,
            jwks_uri=client_metadata.jwks_uri,
            jwks=client_metadata.jwks,
            software_id=client_metadata.software_id,
            software_version=client_metadata.software_version,
        )
        try:
            await self.provider.register_client(client_info)
            return PydanticJSONResponse(content=client_info, status_code=201)
        except register_module.RegistrationError as e:
            return PydanticJSONResponse(
                content=register_module.RegistrationErrorResponse(
                    error=e.error,
                    error_description=e.error_description,
                ),
                status_code=400,
            )

    def create_protected_resource_routes_with_root_fallback(
        resource_url: AnyHttpUrl,
        authorization_servers,
        scopes_supported=None,
        resource_name=None,
        resource_documentation=None,
    ):
        routes = list(
            original_create_protected_resource_routes(
                resource_url,
                authorization_servers,
                scopes_supported=scopes_supported,
                resource_name=resource_name,
                resource_documentation=resource_documentation,
            )
        )

        from mcp.server.auth.handlers.metadata import ProtectedResourceMetadataHandler
        from mcp.server.auth.routes import cors_middleware
        from mcp.shared.auth import ProtectedResourceMetadata

        metadata = ProtectedResourceMetadata(
            resource=resource_url,
            authorization_servers=authorization_servers,
            scopes_supported=scopes_supported,
            resource_name=resource_name,
            resource_documentation=resource_documentation,
        )
        handler = ProtectedResourceMetadataHandler(metadata)

        routes.append(
            Route(
                "/.well-known/oauth-protected-resource",
                endpoint=cors_middleware(handler.handle, ["GET", "OPTIONS"]),
                methods=["GET", "OPTIONS"],
            )
        )

        return routes

    async def token_handle_with_relaxed_redirect_uri(self, request: Request):
        try:
            client_info = await self.client_authenticator.authenticate_request(request)
        except token_module.AuthenticationError as e:
            return PydanticJSONResponse(
                content=token_module.TokenErrorResponse(
                    error="unauthorized_client",
                    error_description=e.message,
                ),
                status_code=401,
                headers={"Cache-Control": "no-store", "Pragma": "no-cache"},
            )

        try:
            form_data = await request.form()
            token_request = token_module.TokenRequest.model_validate(dict(form_data)).root
        except ValidationError as validation_error:
            return self.response(
                token_module.TokenErrorResponse(
                    error="invalid_request",
                    error_description=stringify_pydantic_error(validation_error),
                )
            )

        if token_request.grant_type not in client_info.grant_types:
            return self.response(
                token_module.TokenErrorResponse(
                    error="unsupported_grant_type",
                    error_description=f"Unsupported grant type (supported grant types are {client_info.grant_types})",
                )
            )

        match token_request:
            case token_module.AuthorizationCodeRequest():
                auth_code = await self.provider.load_authorization_code(client_info, token_request.code)
                if auth_code is None or auth_code.client_id != token_request.client_id:
                    return self.response(
                        token_module.TokenErrorResponse(
                            error="invalid_grant",
                            error_description="authorization code does not exist",
                        )
                    )

                if auth_code.expires_at < time.time():
                    return self.response(
                        token_module.TokenErrorResponse(
                            error="invalid_grant",
                            error_description="authorization code has expired",
                        )
                    )

                if auth_code.redirect_uri_provided_explicitly:
                    authorize_request_redirect_uri = auth_code.redirect_uri
                else:
                    authorize_request_redirect_uri = None

                token_redirect_str = normalize_redirect_uri(
                    str(token_request.redirect_uri) if token_request.redirect_uri is not None else None
                )
                auth_redirect_str = normalize_redirect_uri(
                    str(authorize_request_redirect_uri) if authorize_request_redirect_uri is not None else None
                )

                if token_redirect_str is not None and auth_redirect_str is not None and token_redirect_str != auth_redirect_str:
                    print(
                        "Token redirect_uri mismatch tolerated:",
                        {"token": token_redirect_str, "auth": auth_redirect_str},
                    )

                sha256 = hashlib.sha256(token_request.code_verifier.encode()).digest()
                hashed_code_verifier = base64.urlsafe_b64encode(sha256).decode().rstrip("=")

                if hashed_code_verifier != auth_code.code_challenge:
                    return self.response(
                        token_module.TokenErrorResponse(
                            error="invalid_grant",
                            error_description="incorrect code_verifier",
                        )
                    )

                try:
                    tokens = await self.provider.exchange_authorization_code(client_info, auth_code)
                except token_module.TokenError as e:
                    return self.response(
                        token_module.TokenErrorResponse(
                            error=e.error,
                            error_description=e.error_description,
                        )
                    )

                return self.response(token_module.TokenSuccessResponse(root=tokens))

            case _:
                return await original_token_handle(self, request)

    async def send_auth_error_with_realm(self, send, status_code: int, error: str, description: str) -> None:
        www_auth_parts = ['realm="mcp"', f'error="{error}"', f'error_description="{description}"']
        if self.resource_metadata_url:
            www_auth_parts.append(f'resource_metadata="{self.resource_metadata_url}"')

        www_authenticate = f"Bearer {', '.join(www_auth_parts)}"
        body = {"error": error, "error_description": description}
        body_bytes = json.dumps(body).encode()

        await send(
            {
                "type": "http.response.start",
                "status": status_code,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"content-length", str(len(body_bytes)).encode()),
                    (b"www-authenticate", www_authenticate.encode()),
                ],
            }
        )

        await send(
            {
                "type": "http.response.body",
                "body": body_bytes,
            }
        )

    auth_routes_module.build_metadata = build_metadata_with_public_client_support
    auth_routes_module.create_protected_resource_routes = create_protected_resource_routes_with_root_fallback
    register_module.RegistrationHandler.handle = register_handle_with_public_client_default
    token_module.TokenHandler.handle = token_handle_with_relaxed_redirect_uri
    from mcp.server.auth.middleware.bearer_auth import RequireAuthMiddleware

    RequireAuthMiddleware._send_auth_error = send_auth_error_with_realm


patch_mcp_oauth_compat()

oauth_provider = LocalOAuthProvider(
    issuer_url=BASE_URL,
    approval_secret=APPROVAL_SECRET,
)

mcp = FastMCP(
    "notion-rag",
    host=HOST,
    port=PORT,
    streamable_http_path=MCP_PATH,
    transport_security=TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=[
            BASE_HOST,
            "127.0.0.1:*",
            "localhost:*",
            "[::1]:*",
        ],
        allowed_origins=[
            f"http://{BASE_HOST}",
            "http://127.0.0.1:*",
            "http://localhost:*",
            "http://[::1]:*",
        ],
    ),
    auth=AuthSettings(
        issuer_url=BASE_URL,
        resource_server_url=f"{BASE_URL.rstrip('/')}{MCP_PATH}",
        client_registration_options=ClientRegistrationOptions(
            enabled=True,
            default_scopes=[DEFAULT_SCOPE],
            valid_scopes=[DEFAULT_SCOPE],
        ),
        required_scopes=[DEFAULT_SCOPE],
    ),
    auth_server_provider=oauth_provider,
)


def build_oauth_metadata() -> dict:
    base = BASE_URL.rstrip("/")
    return {
        "issuer": f"{base}/",
        "authorization_endpoint": f"{base}/authorize",
        "token_endpoint": f"{base}/token",
        "registration_endpoint": f"{base}/register",
        "scopes_supported": [DEFAULT_SCOPE],
        "response_types_supported": ["code"],
        "grant_types_supported": ["authorization_code", "refresh_token"],
        "token_endpoint_auth_methods_supported": ["none", "client_secret_post", "client_secret_basic"],
        "code_challenge_methods_supported": ["S256"],
    }


@mcp.custom_route("/oauth/approve", methods=["GET", "POST"], include_in_schema=False)
async def oauth_approve(request):
    return await oauth_provider.handle_approval(request)


@mcp.custom_route("/.well-known/oauth-authorization-server/myrag", methods=["GET", "OPTIONS"], include_in_schema=False)
async def oauth_authorization_server_path_metadata(request):
    if request.method == "OPTIONS":
        return JSONResponse(
            {},
            headers={
                "Access-Control-Allow-Origin": "*",
                "Access-Control-Allow-Methods": "GET, OPTIONS",
                "Access-Control-Allow-Headers": "*",
            },
        )
    return JSONResponse(
        build_oauth_metadata(),
        headers={"Access-Control-Allow-Origin": "*"},
    )


@mcp.custom_route("/.well-known/openid-configuration/myrag", methods=["GET", "OPTIONS"], include_in_schema=False)
async def openid_configuration_path_metadata(request):
    if request.method == "OPTIONS":
        return JSONResponse(
            {},
            headers={
                "Access-Control-Allow-Origin": "*",
                "Access-Control-Allow-Methods": "GET, OPTIONS",
                "Access-Control-Allow-Headers": "*",
            },
        )
    return JSONResponse(
        build_oauth_metadata(),
        headers={"Access-Control-Allow-Origin": "*"},
    )


@mcp.custom_route("/myrag/.well-known/openid-configuration", methods=["GET", "OPTIONS"], include_in_schema=False)
async def nested_openid_configuration_metadata(request):
    if request.method == "OPTIONS":
        return JSONResponse(
            {},
            headers={
                "Access-Control-Allow-Origin": "*",
                "Access-Control-Allow-Methods": "GET, OPTIONS",
                "Access-Control-Allow-Headers": "*",
            },
        )
    return JSONResponse(
        build_oauth_metadata(),
        headers={"Access-Control-Allow-Origin": "*"},
    )


@mcp.tool()
def query_notion(
    text: str = "",
    filename: str = "",
    page_title: str = "",
    page_path: str = "",
    parent_path: str = "",
    heading: str = "",
    date: str = "",
    min_depth: int | None = None,
    max_depth: int | None = None,
    sort_by: str = "date",
    sort_order: str = "asc",
    limit: int = 20,
    distinct_field: str = "",
) -> str:
    """Metadata/filter/sort 기반의 범용 조회 도구입니다."""
    store = get_store()
    results = store.query_records(
        text=text,
        filename=filename,
        page_title=page_title,
        page_path=page_path,
        parent_path=parent_path,
        heading=heading,
        date=date,
        min_depth=min_depth,
        max_depth=max_depth,
        sort_by=sort_by,
        sort_order=sort_order,
        limit=limit,
        distinct_field=distinct_field,
    )

    if not results:
        return "No results."

    if distinct_field:
        return "\n".join(f"- {value}" for value in results)

    return "".join(format_record(record) for record in results)


@mcp.tool()
def search_notion(query: str, n: int = 5, strategy: str = "hybrid", rerank: bool = True) -> str:
    """검색 전략(vector, bm25, hybrid)과 reranker 사용 여부를 선택할 수 있는 문서 탐색 도구입니다."""
    store = get_store()
    results = store.search_records(query=query, limit=n, strategy=strategy, rerank=rerank)
    if not results:
        return "No results."
    return "".join(format_record(record) for record in results)


if __name__ == "__main__":
    validate_runtime_config()
    mcp.run(transport=TRANSPORT)
