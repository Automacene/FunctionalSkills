"""Does what its input asks, to check how a caller reads each kind of reply: echo, sleep, raise, text or status."""
import json
import time


def handler(context, event):
    try:
        body = _body(event)
        mode = _input(body, "mode", str, "echo")
        if mode == "sleep":
            seconds = _input(body, "seconds", (int, float), 1)
            time.sleep(seconds)
            return _reply(context, {"slept": seconds})
        if mode == "raise":
            raise RuntimeError("probe was asked to fail")
        if mode == "text":
            return context.Response(body="plain words", content_type="text/plain", status_code=200)
        if mode == "status":
            code = _input(body, "code", int, 500)
            return _reply(context, {"error": f"asked for {code}"}, code)
        result = {"echo": body}
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
