"""Bound inline product payloads before FastAPI parses their JSON bodies."""

from fastapi import HTTPException, Request
from fastapi.routing import APIRoute

# JSON escaping can expand a valid 256 KiB UTF-8 value. Keep room for the
# composition and technical envelope while bounding pre-validation allocation.
MAX_PRODUCT_BODY_BYTES = 2 * 1024 * 1024


class ProductBodyLimitRoute(APIRoute):
    def get_route_handler(self):
        handler = super().get_route_handler()

        async def bounded(request: Request):
            if request.method in {"POST", "PUT", "PATCH"}:
                length = request.headers.get("content-length")
                if length is not None:
                    try:
                        size = int(length)
                    except ValueError:
                        raise HTTPException(400, "Invalid Content-Length") from None
                    if size < 0:
                        raise HTTPException(400, "Invalid Content-Length")
                    if size > MAX_PRODUCT_BODY_BYTES:
                        raise HTTPException(413, "Product request too large")
                body = bytearray()
                async for chunk in request.stream():
                    if len(body) + len(chunk) > MAX_PRODUCT_BODY_BYTES:
                        raise HTTPException(413, "Product request too large")
                    body.extend(chunk)
                # Starlette's body()/json() path caches _body. The
                # bounded read must remain available to FastAPI's schema parser.
                request._body = bytes(body)
            return await handler(request)

        return bounded
