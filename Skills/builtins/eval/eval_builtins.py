"""Evaluate an expression of DAEGON's data built-ins in one call: nested calls written as {"$name": [args...]}."""
import base64
import json
import re


def handler(context, event):
    try:
        body = _body(event)
        if body.get("expr") is None and body.get("fields") is None:
            raise BadRequest("give 'expr' (one expression) or 'fields' (named expressions)")
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


def _as_list(value):
    return value if isinstance(value, list) else [value]


def _merge_on(items, keep_as_list):
    """Merge items with the same "name"; keys in keep_as_list collect every distinct value, the rest keep the last."""
    merged = {}
    for item in items or []:
        name = item.get("name") if isinstance(item, dict) else None
        if not name:
            continue
        target = merged.setdefault(name, {})
        for key, value in item.items():
            if key in keep_as_list:
                bucket = target.setdefault(key, [])
                for v in _as_list(value):
                    if v and v not in bucket:
                        bucket.append(v)
            else:
                target[key] = value
    return list(merged.values())


def _enrich_with_fields(objects, field_names, *value_lists):
    return [{**objects[i], **{field_names[j]: value_lists[j][i] if i < len(value_lists[j]) else None
                              for j in range(len(field_names))}}
            for i in range(len(objects))]


def _zip(keys, *lists):
    return [{keys[j] if j < len(keys) else f"field_{j}": lists[j][i] if i < len(lists[j]) else None
             for j in range(len(lists))}
            for i in range(len(lists[0]) if lists else 0)]


def _keyed_zip(outer_keys, inner_keys, *lists):
    return [{outer_keys[i]: {inner_keys[j] if j < len(inner_keys) else f"field_{j}":
                             lists[j][i] if i < len(lists[j]) else None for j in range(len(lists))}}
            for i in range(len(lists[0]) if lists else 0)]


BUILTINS = {
    # text and encoding
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
    # lists
    "list": lambda *args: list(args),
    "first": lambda lst: lst[0] if isinstance(lst, list) and lst else None,
    "last": lambda lst: lst[-1] if isinstance(lst, list) and lst else None,
    "nth": lambda lst, n: lst[n] if isinstance(lst, list) and -len(lst) <= n < len(lst) else None,
    "len": lambda val: len(val) if isinstance(val, (list, str, dict)) else 0,
    "range": lambda start, end: list(range(int(start), int(end))),
    "dupe": lambda val, times: [val] * int(times),
    "dedupe": lambda lst: [json.loads(v) for v in dict.fromkeys(json.dumps(v, sort_keys=True) for v in _as_list(lst))],
    "flatten": lambda lst: [item for sub in _as_list(lst) for item in _as_list(sub)],
    "vslice": lambda lst, key: [item.get(key) if isinstance(item, dict) else None for item in lst]
                               if isinstance(lst, list) else [],
    "find_by": lambda lst, key, value: next((i for i in _as_list(lst) if isinstance(i, dict) and i.get(key) == value),
                                            None),
    "zip": _zip,
    "keyed_zip": _keyed_zip,
    "enrich_with_fields": _enrich_with_fields,
    "merge_on": _merge_on,
    "cartesian_with": lambda lst1, lst2, template: [template.format(a=b, b=a)
                                                    for a in _as_list(lst1) for b in _as_list(lst2)]
                                                   if lst1 and lst2 else [],
    "cartesian_map": lambda lst, titles, template: [[template.format(a=item, b=title) for title in _as_list(titles)]
                                                    for item in _as_list(lst)] if lst and titles else [],
    "op_on": _op_on,
    "include": lambda name, lst, *args: [i for i in _as_list(lst) if _builtin(name)(i, *args)],
    "exclude": lambda name, lst, *args: [i for i in _as_list(lst) if not _builtin(name)(i, *args)],
    "exclude_on_fields": lambda name, lst, field, *args: [
        i for i in _as_list(lst) if not _builtin(name)(i.get(field) if isinstance(i, dict) else None, *args)],
    # objects
    "get": lambda obj, key, default=None: obj.get(key, default) if isinstance(obj, dict) else default,
    "gets": lambda obj, keys, default=None: [obj.get(k, default) if isinstance(obj, dict) else default for k in keys],
    "set": lambda obj, key, value: {**obj, key: value} if isinstance(obj, dict) else None,
    "sets": lambda obj, values: {**obj, **values} if isinstance(obj, dict) and isinstance(values, dict) else None,
    "has_any_value": lambda obj: isinstance(obj, dict) and any(v not in (None, "", {}, []) for v in obj.values()),
    "all_empty": lambda obj: not isinstance(obj, dict) or all(v in (None, "", {}, []) for v in obj.values()),
    # numbers
    "min": lambda lst: min(lst) if isinstance(lst, list) and lst else None,
    "max": lambda lst: max(lst) if isinstance(lst, list) and lst else None,
    "sum": lambda lst: sum(lst) if isinstance(lst, list) else None,
    "multiply": lambda a, b: a * b,
    # comparisons and logic
    "eq": lambda a, b: a == b,
    "ne": lambda a, b: a != b,
    "gt": lambda a, b: a > b,
    "gte": lambda a, b: a >= b,
    "lt": lambda a, b: a < b,
    "lte": lambda a, b: a <= b,
    "contains": lambda container, item: item in container if container else False,
    "is_empty": lambda val: not val if isinstance(val, (list, dict, str)) else val is None,
    "is_null": lambda val: val is None,
    "not": lambda val: not val,
    "and": lambda *args: all(args),
    "or": lambda *args: any(args),
}
BUILTINS["index_of"] = BUILTINS["nth"]  # the old name


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
