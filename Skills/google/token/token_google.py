"""A fresh Google OAuth access token, refreshed from a refresh token when the cached one is close to expiring."""
import json
import os
import time

import requests

TOKEN_URL = "https://oauth2.googleapis.com/token"


def handler(context, event):
    try:
        body = _body(event)
        data = context.user_data
        if not getattr(data, "token", None) or data.expires_at < time.time() + 300:
            missing = [name for name in ("GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET", "GOOGLE_REFRESH_TOKEN") if not os.getenv(name)]
            if missing:
                return _reply(context, {"error": f"not configured: set {', '.join(missing)} in the function's env"}, 500)
            try:
                response = requests.post(TOKEN_URL, timeout=30, data={
                    "client_id": os.environ["GOOGLE_CLIENT_ID"], "client_secret": os.environ["GOOGLE_CLIENT_SECRET"],
                    "refresh_token": os.environ["GOOGLE_REFRESH_TOKEN"], "grant_type": "refresh_token"})
                response.raise_for_status()
            except requests.RequestException as e:
                return _reply(context, {"error": f"refreshing the token failed: {type(e).__name__}: {e}"}, 502)
            reply = response.json()
            data.token, data.expires_at = reply["access_token"], int(time.time()) + int(reply.get("expires_in", 3600))
        result = {"token": data.token, "expiresAt": data.expires_at}
    except (BadRequest, ValueError, TypeError) as e:
        return _reply(context, {"error": str(e)}, 400)
    return _reply(context, result)


class BadRequest(Exception):
    pass


_REQUIRED = object()


def _input(body, name, kind=None, default=_REQUIRED):
    """One input from the body: required unless a default is given, and of `kind` when one is given."""
    if body.get(name) is None:
        if default is _REQUIRED:
            raise BadRequest(f"'{name}' is required")
        return default
    value = body[name]
    if kind is not None and not isinstance(value, kind):
        raise BadRequest(f"'{name}' must be {_KINDS.get(kind, kind)}, not {type(value).__name__}")
    return value


_KINDS = {str: "a string", list: "a list", dict: "an object", int: "an integer", bool: "a boolean",
          (int, float): "a number"}


def _body(event):
    """The request's JSON body, which must be an object."""
    body = event.body
    if not isinstance(body, dict):
        try:
            body = json.loads(body or b"")
        except ValueError as e:
            raise BadRequest(f"expected a JSON body: {e}")
    if not isinstance(body, dict):
        raise BadRequest("expected a JSON object")
    return body


def _reply(context, body, status=200):
    return context.Response(body=json.dumps(body), content_type="application/json", status_code=status)
