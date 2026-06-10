import asyncio
import logging
import os
import time
from urllib.parse import urlencode

from aiohttp import web, ClientSession, ClientTimeout
from dotenv import load_dotenv

load_dotenv()

UPSTREAM = os.environ.get("UPSTREAM", "https://osu.ppy.sh")
RATE_LIMIT = int(os.environ.get("RATE_LIMIT", "3"))
BURST = int(os.environ.get("BURST", "10"))
CONNECT_TIMEOUT = int(os.environ.get("CONNECT_TIMEOUT", "10"))
READ_TIMEOUT = int(os.environ.get("READ_TIMEOUT", "90"))
HOST = os.environ.get("HOST", "0.0.0.0")
PORT = int(os.environ.get("PORT", "8727"))
LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO")
PROXY_SECRET = os.environ.get("PROXY_SECRET", "")

UPSTREAM_HOST = UPSTREAM.replace("https://", "").replace("http://", "").split("/")[0]

STRIP_HEADERS = frozenset({
    "x-proxy-secret",
    "cf-connecting-ip",
    "cf-ray",
    "cf-visitor",
    "cf-ipcountry",
    "cf-worker",
    "accept-encoding",
})

HOP_BY_HOP = frozenset({
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailers",
    "transfer-encoding",
    "upgrade",
    "content-encoding",
    "content-length",
})


class TokenBucket:
    def __init__(self, rate, burst):
        self.rate = rate
        self.burst = burst
        self.tokens = float(burst)
        self.last = time.monotonic()
        self._lock = asyncio.Lock()

    def _refill(self):
        now = time.monotonic()
        self.tokens = min(self.burst, self.tokens + (now - self.last) * self.rate)
        self.last = now

    def has_capacity(self):
        return self.tokens >= 1.0

    async def acquire(self):
        async with self._lock:
            self._refill()
            while self.tokens < 1.0:
                wait = (1.0 - self.tokens) / self.rate
                await asyncio.sleep(wait)
                self._refill()
            self.tokens -= 1.0


limiter = TokenBucket(RATE_LIMIT, BURST)
logger = logging.getLogger("osu-api-proxy")


def clean_request_headers(headers):
    out = {}
    for k, v in headers.items():
        lk = k.lower()
        if lk in STRIP_HEADERS or lk in HOP_BY_HOP:
            continue
        out[k] = v
    return out


async def health(request):
    return web.json_response({"status": "ok", "proxy": "osu-api-proxy-python"})


async def proxy_handler(request):
    if PROXY_SECRET:
        hdr_secret = request.headers.get("X-Proxy-Secret", "")
        arg_secret = request.query.get("proxy_secret", "")
        if hdr_secret != PROXY_SECRET and arg_secret != PROXY_SECRET:
            return web.json_response(
                {"error": "Invalid or missing X-Proxy-Secret."},
                status=401,
            )

    if not limiter.has_capacity():
        return web.json_response(
            {"error": "Rate limit exceeded. Try again later."},
            status=429,
        )

    await limiter.acquire()
    session: ClientSession = request.app["client_session"]
    path = request.path
    method = request.method
    headers = clean_request_headers(request.headers)
    headers["Host"] = UPSTREAM_HOST

    query = dict(request.query)
    query.pop("proxy_secret", None)

    upstream_url = f"{UPSTREAM}{path}{'?' + urlencode(query, doseq=True) if query else ''}"
    logger.debug(">>> %s %s", method, upstream_url)
    logger.debug(">>> headers: %s", dict(headers))
    logger.debug(">>> query: %s", query)

    try:
        body = await request.read()
        upstream_timeout = ClientTimeout(
            sock_connect=CONNECT_TIMEOUT,
            sock_read=READ_TIMEOUT,
        )
        async with session.request(
            method,
            upstream_url,
            headers=headers,
            data=body if body else None,
            timeout=upstream_timeout,
            allow_redirects=False,
        ) as upstream:
            logger.debug("<<< %s %s -> %s", method, path, upstream.status)
            logger.debug("<<< headers: %s", dict(upstream.headers))
            resp_headers = {}
            for k, v in upstream.headers.items():
                lk = k.lower()
                if lk in HOP_BY_HOP:
                    continue
                resp_headers[k] = v

            resp_body = await upstream.read()
            return web.Response(
                status=upstream.status,
                headers=resp_headers,
                body=resp_body,
            )
    except asyncio.TimeoutError:
        return web.json_response(
            {"error": "Upstream timeout."},
            status=504,
        )
    except Exception:
        logger.exception("Upstream request failed")
        return web.json_response(
            {"error": "Upstream error."},
            status=502,
        )


async def not_found(request):
    return web.json_response(
        {"error": "This proxy only forwards /api/* and /oauth/token."},
        status=404,
    )


async def on_startup(app):
    app["client_session"] = ClientSession()


async def on_cleanup(app):
    await app["client_session"].close()


def create_app():
    app = web.Application()

    app.router.add_get("/", health)
    app.router.add_route("*", "/api/{path:.*}", proxy_handler)
    app.router.add_route("*", "/api/", proxy_handler)
    app.router.add_route("*", "/oauth/token", proxy_handler)
    app.router.add_route("*", "/{path:.*}", not_found)

    app.on_startup.append(on_startup)
    app.on_cleanup.append(on_cleanup)

    return app


if __name__ == "__main__":
    logging.basicConfig(
        level=getattr(logging, LOG_LEVEL.upper(), logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    app = create_app()
    web.run_app(app, host=HOST, port=PORT)