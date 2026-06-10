# osu-api-proxy-python

Reverse proxy for the osu! API. Forwards `/api/*` and `/oauth/token` to `osu.ppy.sh` with rate limiting and optional authentication.

## Requirements

- Python 3.10+
- pip

## Quick start

```bash
pip install -r requirements.txt
cp .env.example .env
# edit .env as needed
python proxy.py
```

The server listens on `0.0.0.0:8727` by default.


## Authentication

When `PROXY_SECRET` is set, clients must send the secret via:

- **Header:** `X-Proxy-Secret: YOUR-SECRET`
- **Query param:** `?proxy_secret=YOUR-SECRET`

Both forms are stripped before forwarding to osu.ppy.sh. If `PROXY_SECRET` is empty, the proxy is open.

## Usage

Exactly the same as the osu! API, just replace `osu.ppy.sh` with your proxy address.