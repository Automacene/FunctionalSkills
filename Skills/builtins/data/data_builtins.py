"""DAEGON's data built-ins: the shape of JSON values, in one function. An expression of nested calls written as
{"$name": [args...]} (expr, fields), or a named operation (op): rows, pluck, group, find. Text work (encoding,
templates, parsing JSON text) is builtins/text."""
import base64
import json
import re


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
    "dedupe_vals": lambda lst: sorted(set(_as_list(lst)), key=lambda v: (str(type(v)), v)),
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


def op_rows(body):
    """One object per position from lists of values, with shared fields on each; some fields folded into one as JSON
    text; each row under a key if wrap is given."""
    columns = _input(body, "columns", dict)
    shared, as_json = _input(body, "shared", dict, {}), _input(body, "json", dict, {})
    wrap = _input(body, "wrap", str, "")
    for name, values in columns.items():
        if not isinstance(values, list):
            raise BadRequest(f"columns.{name} must be a list, not {type(values).__name__}")
    rows = []
    for i in range(max((len(values) for values in columns.values()), default=0)):
        row = {**shared, **{name: values[i] if i < len(values) else None for name, values in columns.items()}}
        for name, keys in as_json.items():
            row[name] = json.dumps({key: row.pop(key, None) for key in keys})
        rows.append({wrap: row} if wrap else row)
    return {"rows": rows}


def _columns(rows, names):
    return {name: [row.get(name) for row in rows] for name in names}


def op_pluck(body):
    """Pick fields (by path) out of items: of the items themselves, or of the list at `path` in each, flattened.
    `limit` reads only the first items."""
    fields, path = _input(body, "fields", dict), _input(body, "path", str, "")
    items = _input(body, "items", list)
    limit = _input(body, "limit", int, None)
    rows = []
    for item in items[:limit] if limit is not None else items:
        inner = _at(item, path) if path else [item]
        for entry in inner if isinstance(inner, list) else []:
            rows.append({name: _at(entry, where) for name, where in fields.items()})
    return {"rows": rows, "columns": _columns(rows, fields)}


def op_group(body):
    """One row per distinct value of `by` (a path), in the order found. `fields` (name to path) fill each row: those
    named in `collect` gather every distinct non-empty value as a list, the rest keep the last non-empty one."""
    by, fields = _input(body, "by", str), _input(body, "fields", dict)
    collect = set(_input(body, "collect", list, []))
    groups = {}
    for item in _input(body, "items", list):
        key = _at(item, by)
        if key in (None, ""):
            continue
        row = groups.setdefault(json.dumps(key, sort_keys=True),
                                {name: [] if name in collect else None for name in fields})
        for name, where in fields.items():
            value = _at(item, where)
            if value in (None, ""):
                continue
            if name in collect:
                if value not in row[name]:
                    row[name].append(value)
            else:
                row[name] = value
    rows = list(groups.values())
    return {"rows": rows, "columns": _columns(rows, fields)}


def op_find(body):
    """The value at `field` (a path) of the first object, depth first from `item` itself, whose paths in `where` have
    those values; with `fallback`, of the first object that has the field when none matches. `decode: base64` decodes
    it (base64 or base64url)."""
    item, field = _input(body, "item"), _input(body, "field", str)
    where = _input(body, "where", dict, {})
    found = []

    def walk(node):
        if isinstance(node, dict):
            if _at(node, field) is not None:
                found.append((all(_at(node, k) == v for k, v in where.items()), _at(node, field)))
            children = node.values()
        elif isinstance(node, list):
            children = node
        else:
            return
        for child in children:
            walk(child)
    walk(item)
    matched = [value for ok, value in found if ok]
    value = matched[0] if matched else (found[0][1] if found and _input(body, "fallback", bool, False) else None)
    if value is not None and _input(body, "decode", str, "") == "base64":
        value = _decode(value)
    return {"value": value}


OPS = {"rows": op_rows, "pluck": op_pluck, "group": op_group, "find": op_find}


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
