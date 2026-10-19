"""Call one HTTP endpoint until a field of its reply has an expected value, and return that reply."""
import json
import time

import requests


def handler(context, event):
    try:
        body = _body(event)
        url, until = _input(body, "url", str), _input(body, "until", dict)
        if "field" not in until or "equals" not in until:
            raise BadRequest("'until' must hold field and equals")
        method = _input(body, "method", str, "GET").upper()
        headers, params = _input(body, "headers", dict, {}), _input(body, "params", dict, {})
        interval, attempts = _input(body, "intervalMs", (int, float), 2000) / 1000, _input(body, "maxAttempts", int, 100)
        field, expected = until["field"], until["equals"]
        for attempt in range(1, attempts + 1):
            try:
                if method == "GET":
                    response = requests.get(url, params=params, headers=headers, timeout=30)
                else:
                    response = requests.request(method, url, json=params, headers=headers, timeout=30)
                reply = response.json() if response.ok and response.content else None
            except (requests.RequestException, ValueError):
                reply = None
            if reply is not None and _get(reply, field) == expected:
                return _reply(context, {"result": reply, "attempts": attempt})
            if attempt < attempts:
                time.sleep(interval)
        return _reply(context, {"error": f"'{field}' was not {expected!r} after {attempts} attempts"}, 504)
    except (BadRequest, ValueError, TypeError) as e:
        return _reply(context, {"error": str(e)}, 400)
    return _reply(context, result)


def _get(value, path):
    """A dotted path into a reply: "a.b" is value["a"]["b"]; None where a step is missing."""
    for key in path.split("."):
        value = value.get(key) if isinstance(value, dict) else None
    return value


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
