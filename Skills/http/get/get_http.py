"""Fetch a URL and return its body as text."""
import json

import requests


def handler(context, event):
    try:
        body = _body(event)
        url, timeout = _input(body, "url", str), _input(body, "timeoutSeconds", (int, float), 10)
        try:
            response = requests.get(url, timeout=timeout)
        except requests.RequestException as e:
            return _reply(context, {"error": f"{type(e).__name__}: {e}"}, 502)
        result = {"text": response.text, "status": response.status_code}
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
