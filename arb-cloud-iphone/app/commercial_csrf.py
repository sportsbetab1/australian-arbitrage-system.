"""Customer API CSRF protection for cookie sessions.

Double-submit token is not sufficient alone: require server-bound token derived
from the current session's random secret and a server-side HMAC key.
"""
import hashlib
import hmac
import os
import secrets
from fastapi import HTTPException

# Tokens are derived from the existing high-entropy session cookie.
# An independent secret is mandatory in deployment; do not fall back to a
# predictable application constant.
def csrf_secret():
    value = os.environ.get("COMMERCIAL_CSRF_SECRET", "")
    if len(value.encode("utf-8")) < 32:
        raise RuntimeError("COMMERCIAL_CSRF_SECRET must be at least 32 bytes")
    return value.encode("utf-8")

def csrf_token(session_token):
    if not session_token:
        raise HTTPException(401,"Authentication required")
    return hmac.new(csrf_secret(), session_token.encode("utf-8"),hashlib.sha256).hexdigest()

def check_csrf(request, cookie_name):
    session_token=request.cookies.get(cookie_name)
    supplied=request.headers.get("x-csrf-token","")
    if not session_token or not supplied or not secrets.compare_digest(csrf_token(session_token),supplied):
        raise HTTPException(403,"Invalid CSRF token")
