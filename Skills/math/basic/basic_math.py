"""Apply an arithmetic operator to a list of numbers: add, subtract, multiply, divide, floor_divide, mod, power, min, max, mean, abs, negate, sqrt, floor, ceil or round."""
import json
import math


def handler(context, event):
    try:
        body = _body(event)
        op = _input(body, "op", str)
        if op not in OPERATORS:
            raise BadRequest(f"unknown op {op!r}; one of {', '.join(OPERATORS)}")
        try:
            result = {"value": OPERATORS[op](_numbers(body))}
        except ZeroDivisionError:
            raise BadRequest("division by zero")
        except OverflowError as e:
            raise BadRequest(f"the result is too large: {e}")
    except (BadRequest, ValueError, TypeError) as e:
        return _reply(context, {"error": str(e)}, 400)
    return _reply(context, result)


def _fold(fn):
    """Apply a two-number operator left to right: a op b op c ..., which needs at least two numbers."""
    def run(values):
        if len(values) < 2:
            raise BadRequest("this operator needs at least two values")
        result = values[0]
        for value in values[1:]:
            result = fn(result, value)
        return result
    return run


def _unary(fn):
    def run(values):
        if len(values) != 1:
            raise BadRequest("this operator takes exactly one value")
        return fn(values[0])
    return run


def _power(a, b):
    if abs(b) > 10000:
        raise BadRequest("an exponent is at most 10000")
    return a ** b


def _sqrt(a):
    if a < 0:
        raise BadRequest("sqrt of a negative number")
    return math.sqrt(a)


def _round(values):
    if len(values) not in (1, 2) or (len(values) == 2 and not float(values[1]).is_integer()):
        raise BadRequest("round takes a value and, optionally, a whole number of digits")
    return round(values[0], int(values[1])) if len(values) == 2 else round(values[0])


OPERATORS = {
    # any count: add of nothing is 0, multiply of nothing is 1, min/max/mean of nothing is null
    "add": lambda values: sum(values),
    "multiply": lambda values: math.prod(values),
    "min": lambda values: min(values) if values else None,
    "max": lambda values: max(values) if values else None,
    "mean": lambda values: sum(values) / len(values) if values else None,
    # left to right, two or more
    "subtract": _fold(lambda a, b: a - b),
    "divide": _fold(lambda a, b: a / b),
    "floor_divide": _fold(lambda a, b: a // b),
    "mod": _fold(lambda a, b: a % b),
    "power": _fold(_power),
    # one value
    "abs": _unary(abs),
    "negate": _unary(lambda a: -a),
    "sqrt": _unary(_sqrt),
    "floor": _unary(math.floor),
    "ceil": _unary(math.ceil),
    # a value and, optionally, digits
    "round": _round,
}


def _numbers(body):
    values = _input(body, "values", list)
    if not all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in values):
        raise BadRequest("'values' must hold only numbers")
    return values


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
