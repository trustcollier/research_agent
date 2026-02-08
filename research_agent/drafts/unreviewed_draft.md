# Draft (unreviewed)

Checklist (minimal UI, FastAPI-served)
- Add `<!DOCTYPE html>` to avoid quirks mode.
- Declare charset early (`<meta charset="UTF-8">` within first 1024 bytes or a Content-Type charset).
- Include a viewport meta tag with `width=device-width, initial-scale=1`.
- Serve everything over HTTPS and redirect HTTP → HTTPS.
- Set a strict CSP that includes `script-src`, `object-src`, and `base-uri`, and use nonces or hashes.
- Mitigate clickjacking with CSP `frame-ancestors` and/or `X-Frame-Options`.
- Add baseline security headers: HSTS, X-Content-Type-Options, COOP, CORP (plus Trusted Types via CSP if you can enforce it).

Exact headers + example values
- Content-Security-Policy: `script-src 'nonce-{RANDOM}' 'strict-dynamic' https: 'unsafe-inline'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; require-trusted-types-for 'script'`
- Strict-Transport-Security: `max-age=63072000; includeSubDomains; preload`
- X-Content-Type-Options: `nosniff`
- X-Frame-Options: `DENY`
- Cross-Origin-Resource-Policy: `same-origin`
- Cross-Origin-Opener-Policy: `same-origin`

FastAPI middleware snippet
```python
import secrets
from fastapi import FastAPI, Request

app = FastAPI()

@app.middleware("http")
async def security_headers(request: Request, call_next):
    nonce = secrets.token_urlsafe(16)
    response = await call_next(request)

    csp = (
        "script-src 'nonce-{nonce}' 'strict-dynamic' https: 'unsafe-inline'; "
        "object-src 'none'; base-uri 'none'; frame-ancestors 'none'; "
        "require-trusted-types-for 'script'"
    ).format(nonce=nonce)

    response.headers["Content-Security-Policy"] = csp
    response.headers["Strict-Transport-Security"] = "max-age=63072000; includeSubDomains; preload"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Cross-Origin-Resource-Policy"] = "same-origin"
    response.headers["Cross-Origin-Opener-Policy"] = "same-origin"

    request.state.csp_nonce = nonce
    return response
```
