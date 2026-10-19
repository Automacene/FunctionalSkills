"""Call one HTTP endpoint once per item and return the replies in item order."""
import json
import time

import requests


def handler(context, event):
    try:
        body = _body(event)
        url, items = _input(body, "url", str), _input(body, "items", list)
        method = _input(body, "method", str, "POST").upper()
        headers, shared = _input(body, "headers", dict, {}), _input(body, "params", dict, {})
        delay, timeout = _input(body, "delayMs", (int, float), 0) / 1000, _input(body, "timeoutSeconds", (int, float), 30)
        results = []
        for index, item in enumerate(items):
            params = {**shared, **(item if isinstance(item, dict) else {"item": item})}
            try:
                if method == "GET":
                    response = requests.get(url, params=params, headers=headers, timeout=timeout)
                else:
                    response = requests.request(method, url, json=params, headers=headers, timeout=timeout)
            except requests.RequestException as e:
                return _reply(context, {"error": f"item {index}: {type(e).__name__}: {e}", "results": results}, 502)
            if not response.ok:
                return _reply(context, {"error": f"item {index}: HTTP {response.status_code}", "results": results}, 502)
            results.append(_json(response))
            if delay and index < len(items) - 1:
                time.sleep(delay)
        result = {"results": results}
    except (BadRequest, ValueError, TypeError) as e:
        return _reply(context, {"error": str(e)}, 400)
    return _reply(context, result)


def _json(response):
    if not response.content:
        return {}
    try:
        return response.json()
    except ValueError:
        return {"text": response.text}


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
