from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse

from agentclaw.community.di import Injected
from agentclaw.community.plugin_api.token_exchange import TokenExchangePlugin

router = APIRouter()


@router.options("/api/v1/token/exchange")
async def token_exchange_options(request: Request):
    origin = request.headers.get("origin", "*")
    return Response(
        status_code=200,
        headers={
            "Access-Control-Allow-Origin": origin,
            "Access-Control-Allow-Methods": "POST, OPTIONS",
            "Access-Control-Allow-Headers": "Content-Type, Authorization, Cookie",
            "Access-Control-Allow-Credentials": "true",
        },
    )


@router.post("/api/v1/token/exchange")
async def token_exchange(
    request: Request,
    plugin: TokenExchangePlugin = Injected(TokenExchangePlugin),
) -> Response:
    origin = request.headers.get("origin", "*")
    headers = {
        "Access-Control-Allow-Origin": origin,
        "Access-Control-Allow-Credentials": "true",
    }
    # Plugin owns the per-runtime policy: Local returns a mock; Prod
    # reads IAM_TOKEN and calls Buservice. Missing cookie / upstream
    # failures raise DomainError subclasses mapped to 400/500 by the
    # global handler in api/app.py.
    content = await plugin.exchange_from_request(request)
    return JSONResponse(content=content, headers=headers)


@router.options("/api/v1/token/iam")
async def get_iam_token_options(request: Request):
    origin = request.headers.get("origin", "*")
    return Response(
        status_code=200,
        headers={
            "Access-Control-Allow-Origin": origin,
            "Access-Control-Allow-Methods": "GET, OPTIONS",
            "Access-Control-Allow-Headers": "Content-Type, Authorization, Cookie",
            "Access-Control-Allow-Credentials": "true",
        },
    )


@router.get("/api/v1/token/iam")
async def get_iam_token(request: Request) -> Response:
    origin = request.headers.get("origin", "*")
    headers = {
        "Access-Control-Allow-Origin": origin,
        "Access-Control-Allow-Credentials": "true",
    }
    iam_token = request.cookies.get("IAM_TOKEN") or ""
    if not iam_token:
        return JSONResponse(
            content={"success": False, "error": "IAM_TOKEN cookie not found"},
            status_code=400,
            headers=headers,
        )
    return JSONResponse(
        content={"success": True, "iam_token": iam_token},
        headers=headers,
    )
