"""Call a nuclio function once per item and return its replies in order (null where a call failed)."""
import json
import os

import requests

# Where a nuclio function answers on the DAEGON network
FUNCTION_URL = os.getenv("NUCLIO_FUNCTION_URL", "http://nuclio-nuclio-{name}:8080")


def handler(context, event):
    try:
        body = _body(event)
        function, items = _input(body, "function", str), _input(body, "items", list)
        field, params = _input(body, "field", str, None), _input(body, "params", dict, {})
        timeout = _input(body, "timeoutSeconds", (int, float), 30)
        url = FUNCTION_URL.format(name=function)
        results, errors = [], []
        for index, item in enumerate(items):
            request = {**params, field: item} if field else {**params, **item} if isinstance(item, dict) else {**params, "item": item}
            try:
                response = requests.post(url, json=request, timeout=timeout,
                                         headers={k: v for k, v in event.headers.items() if k.lower().startswith("x-daegon-")})
                response.raise_for_status()
                results.append(response.json())
            except Exception as e:
                results.append(None)
                errors.append({"index": index, "error": f"{type(e).__name__}: {e}"})
        result = {"results": results, "errors": errors}
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
