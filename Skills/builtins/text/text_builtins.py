"""DAEGON's text built-ins: strings, encodings, templates and JSON text, in one function. An expression of nested
calls written as {"$name": [args...]} (expr, fields), or a named operation (op): format, parse_json. The shape of JSON
values (lists, objects) is builtins/data."""
import base64
import json
import re
import string


def handler(context, event):
    try:
        body = _body(event)
        if "op" in body:
            op = _input(body, "op", str)
            if op not in OPS:
                raise BadRequest(f"unknown op {op!r}; one of {', '.join(sorted(OPS))}")
            return _reply(context, OPS[op](body))
        if body.get("expr") is None and body.get("fields") is None:
            raise BadRequest("give 'op' (a named operation), 'expr' (one expression) or 'fields' (named expressions)")
        result = {}
        if "expr" in body:
            result["value"] = evaluate(body["expr"])
        if body.get("fields") is not None:
            fields = _input(body, "fields", dict)
            result["fields"] = {name: evaluate(expr) for name, expr in fields.items()}
    except (BadRequest, ValueError, TypeError, KeyError, IndexError) as e:
        return _reply(context, {"error": str(e) if isinstance(e, BadRequest) else f"{type(e).__name__}: {e}"}, 400)
    return _reply(context, result)


def evaluate(node):
    """A call is an object with one key that starts with "$", whose value is the list of its arguments:
    {"$concat": ["a", "b"]}. Its arguments are evaluated first. Anything else is data: lists and objects are evaluated
    inside, everything else is itself."""
    if isinstance(node, dict):
        if len(node) == 1:
            (key, args), = node.items()
            if isinstance(key, str) and key.startswith("$"):
                name = key[1:]
                if name not in BUILTINS:
                    raise BadRequest(f"unknown built-in {name!r}; one of {', '.join(sorted(BUILTINS))}")
                if not isinstance(args, list):  # a lone value would be ambiguous: a list there is one argument or many
                    raise BadRequest(f"the arguments of ${name} must be a list, such as {{\"${name}\": [...]}}")
                return BUILTINS[name](*[evaluate(arg) for arg in args])
        return {key: evaluate(value) for key, value in node.items()}
    if isinstance(node, list):
        return [evaluate(item) for item in node]
    return node


def _op_on(name, items, *args):
    """Apply a built-in to each item of a list (each failure gives null), or to a single value."""
    fn = _builtin(name)
    if not isinstance(items, list):
        return fn(items, *args)
    results = []
    for item in items:
        try:
            results.append(fn(item, *args))
        except Exception:
            results.append(None)
    return results


def _builtin(name):
    if name not in BUILTINS:
        raise BadRequest(f"unknown built-in {name!r}")
    return BUILTINS[name]


def _text(value):
    return value if isinstance(value, str) else json.dumps(value) if isinstance(value, (dict, list)) else str(value)


def _decode(val):
    """base64 or base64url (Gmail's), padded or not."""
    return base64.b64decode(val.replace("-", "+").replace("_", "/") + "=" * (-len(val) % 4)).decode("utf-8")


BUILTINS = {
    "encode": lambda val: base64.b64encode(val.encode("utf-8") if isinstance(val, str) else bytes(val)).decode(),
    "decode": _decode,
    "str": lambda val: "" if val is None else _text(val),
    "concat": lambda *args: "".join(_text(a) for a in args if a is not None),
    "nl": lambda *args: "\n",
    "replace": lambda val, old, new: str(val).replace(old, new) if val is not None else "",
    "split": lambda delimiter, s: s.split(delimiter) if isinstance(s, str) else [],
    "sanitize": lambda s, pattern="[^a-zA-Z0-9_ -]", replacement="": re.sub(pattern, replacement, str(s)) if s else "",
    "slug": lambda s: re.sub(r"[^a-z0-9]+", "-", str(s).lower()).strip("-") if s else "",
    "to_jsons": lambda obj: json.dumps(obj),
    "from_jsons": lambda s: json.loads(s) if s else None,
    "extract_emails": lambda html: re.findall(r"[\w.-]+@[\w.-]+\.\w+", html or ""),
    "extract_phones": lambda html: re.findall(r"\+?1?\s?\(?\d{3}\)?[\s.-]?\d{3}[\s.-]?\d{4}", html or ""),
    "join": lambda values, separator="": separator.join(_text(v) for v in values if v is not None),
    "op_on": _op_on,
}


# ----------------------------------------------------------------------------------------------------------------
# Named operations: {"op": <name>, ...its inputs}
# ----------------------------------------------------------------------------------------------------------------
def _at(value, path):
    """What a dotted path names inside value: keys of objects, and positions (0, 1, -1) of lists. None when absent."""
    for part in str(path).split(".") if path not in (None, "") else []:
        if isinstance(value, dict):
            value = value.get(part)
        elif isinstance(value, list) and re.fullmatch(r"-?\d+", part) and -len(value) <= int(part) < len(value):
            value = value[int(part)]
        else:
            return None
    return value


def _empty(value):
    return value is None or (isinstance(value, dict) and all(v in (None, "", [], {}) for v in value.values()))


FENCE = re.compile(r"^```[a-zA-Z]*\s*|\s*```$")


def op_parse_json(body):
    """Each item's text (at path) parsed as JSON, null where it is not; a ```json fence around it is allowed."""
    path, field = _input(body, "path", str, ""), _input(body, "field", str, None)
    items = []
    for item in _input(body, "items", list):
        text = _at(item, path)
        try:
            items.append(json.loads(FENCE.sub("", text.strip())) if isinstance(text, str) else None)
        except ValueError:
            items.append(None)
    if _input(body, "dropEmpty", bool, False):
        items = [item for item in items if not _empty(item)]
    result = {"items": items}
    if field is not None:
        result["values"] = [_at(item, field) for item in items]
    return result


class _Formatter(string.Formatter):
    """str.format over data: {name}, {name[0]}, {name[key]}; no attribute access."""
    def get_field(self, field_name, args, kwargs):
        if "." in field_name:
            raise BadRequest(f"{{{field_name}}}: use [key] or [0], not attributes")
        return super().get_field(field_name, args, kwargs)

    def get_value(self, key, args, kwargs):
        if key not in kwargs:
            raise BadRequest(f"{{{key}}} is not in values")
        return kwargs[key]


def op_format(body):
    """Fill a template from values: {name}, {list[0]}, {object[key]}; optionally base64 encoded too."""
    try:
        text = _Formatter().format(_input(body, "template", str), **_input(body, "values", dict))
    except (IndexError, KeyError) as e:
        raise BadRequest(f"the template reads what the values do not have: {e}")
    result = {"text": text}
    if _input(body, "encode", bool, False):
        result["encoded"] = base64.b64encode(text.encode("utf-8")).decode()
    return result


OPS = {"format": op_format, "parse_json": op_parse_json}


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
