"""Keep the items a predicate holds for."""
import json


def handler(context, event):
    try:
        body = _body(event)
        predicate, args = _predicate(body)
        result = {"items": [item for item in _input(body, "items", list) if predicate(item, *args)]}
    except (BadRequest, ValueError, TypeError) as e:
        return _reply(context, {"error": str(e)}, 400)
    return _reply(context, result)


# The predicates include, exclude and exclude_on_fields take by name: item first, then the request's args
PREDICATES = {
    "eq": lambda a, b: a == b,
    "ne": lambda a, b: a != b,
    "gt": lambda a, b: a > b,
    "gte": lambda a, b: a >= b,
    "lt": lambda a, b: a < b,
    "lte": lambda a, b: a <= b,
    "contains": lambda container, item: item in container if container else False,
    "is_empty": lambda value: not value if isinstance(value, (list, dict, str)) else value is None,
    "is_null": lambda value: value is None,
    "not": lambda value: not value,
}


def _predicate(body):
    name = _input(body, "predicate", str)
    if name not in PREDICATES:
        raise BadRequest(f"unknown predicate {name!r}; one of {', '.join(PREDICATES)}")
    return PREDICATES[name], _input(body, "args", list, [])


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
