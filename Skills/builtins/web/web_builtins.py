"""DAEGON's web built-ins: calls to outside HTTP endpoints, in one function, as named operations (op): get, gets,
scrape, poll, fan_out, and token (a Google OAuth access token)."""
import json
import os
import time

import requests
from bs4 import BeautifulSoup

USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
TOKEN_URL = "https://oauth2.googleapis.com/token"


def handler(context, event):
    try:
        body = _body(event)
        op = _input(body, "op", str)
        if op not in OPS:
            raise BadRequest(f"unknown op {op!r}; one of {', '.join(sorted(OPS))}")
        return _reply(context, OPS[op](body, context))
    except Answer as answer:
        return _reply(context, answer.body, answer.status)
    except (BadRequest, ValueError, TypeError) as e:
        return _reply(context, {"error": str(e)}, 400)


class Answer(Exception):
    """A reply other than 200, from inside an operation."""
    def __init__(self, body, status):
        super().__init__(body.get("error"))
        self.body, self.status = body, status


def op_get(body, context):
    """Fetch a URL and return its body as text."""
    url, timeout = _input(body, "url", str), _input(body, "timeoutSeconds", (int, float), 10)
    try:
        response = requests.get(url, timeout=timeout)
    except requests.RequestException as e:
        raise Answer({"error": f"{type(e).__name__}: {e}"}, 502)
    return {"text": response.text, "status": response.status_code}


def op_gets(body, context):
    """Fetch several URLs and return their bodies as text, in order (null where a fetch failed)."""
    urls, timeout = _input(body, "urls", (list, str)), _input(body, "timeoutSeconds", (int, float), 10)
    texts, errors = [], []
    for index, url in enumerate([urls] if isinstance(urls, str) else urls):
        try:
            texts.append(requests.get(url, timeout=timeout).text)
        except requests.RequestException as e:
            texts.append(None)
            errors.append({"index": index, "error": f"{type(e).__name__}: {e}"})
    return {"texts": texts, "errors": errors}


def _scrape(url, timeout, limit):
    """A page's visible text, scripts and styles removed, up to limit characters; and why not, when it failed."""
    try:
        response = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=timeout)
        response.raise_for_status()
    except requests.RequestException as e:
        return "", f"{type(e).__name__}: {e}"
    soup = BeautifulSoup(response.content, "html.parser")
    for tag in soup(["script", "style"]):
        tag.decompose()
    chunks = (phrase.strip() for line in soup.get_text().splitlines() for phrase in line.strip().split("  "))
    return " ".join(chunk for chunk in chunks if chunk)[:limit], None


def op_scrape(body, context):
    """The visible text of a page (url), or of several (urls), in order; empty where a page could not be fetched."""
    timeout, limit = _input(body, "timeoutSeconds", (int, float), 10), _input(body, "maxChars", int, 5000)
    if body.get("urls") is None:
        text, error = _scrape(_input(body, "url", str), timeout, limit)
        return {"text": text, **({"error": error} if error else {})}
    texts, errors = [], []
    for index, url in enumerate(_input(body, "urls", list)):
        text, error = _scrape(url, timeout, limit) if isinstance(url, str) and url else ("", "no url")
        texts.append(text)
        if error:
            errors.append({"index": index, "error": error})
    return {"texts": texts, "errors": errors}


def _at(value, path):
    """A dotted path into a reply: "a.b" is value["a"]["b"]; None where a step is missing."""
    for key in path.split("."):
        value = value.get(key) if isinstance(value, dict) else None
    return value


def op_poll(body, context):
    """Call one endpoint until a field of its reply has an expected value, and return that reply."""
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
        if reply is not None and _at(reply, field) == expected:
            return {"result": reply, "attempts": attempt}
        if attempt < attempts:
            time.sleep(interval)
    raise Answer({"error": f"'{field}' was not {expected!r} after {attempts} attempts"}, 504)


def _json(response):
    if not response.content:
        return {}
    try:
        return response.json()
    except ValueError:
        return {"text": response.text}


def op_fan_out(body, context):
    """Call one endpoint once per item and return the replies in item order."""
    url, items = _input(body, "url", str), _input(body, "items", list)
    method = _input(body, "method", str, "POST").upper()
    headers, shared = _input(body, "headers", dict, {}), _input(body, "params", dict, {})
    delay = _input(body, "delayMs", (int, float), 0) / 1000
    timeout = _input(body, "timeoutSeconds", (int, float), 30)
    results = []
    for index, item in enumerate(items):
        params = {**shared, **(item if isinstance(item, dict) else {"item": item})}
        try:
            if method == "GET":
                response = requests.get(url, params=params, headers=headers, timeout=timeout)
            else:
                response = requests.request(method, url, json=params, headers=headers, timeout=timeout)
        except requests.RequestException as e:
            raise Answer({"error": f"item {index}: {type(e).__name__}: {e}", "results": results}, 502)
        if not response.ok:
            raise Answer({"error": f"item {index}: HTTP {response.status_code}", "results": results}, 502)
        results.append(_json(response))
        if delay and index < len(items) - 1:
            time.sleep(delay)
    return {"results": results}


def op_token(body, context):
    """A fresh Google OAuth access token, refreshed from a refresh token when the cached one is close to expiring."""
    data = context.user_data
    if not getattr(data, "token", None) or data.expires_at < time.time() + 300:
        names = ("GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET", "GOOGLE_REFRESH_TOKEN")
        missing = [name for name in names if not os.getenv(name)]
        if missing:
            raise Answer({"error": f"not configured: set {', '.join(missing)} in the function's env"}, 500)
        try:
            response = requests.post(TOKEN_URL, timeout=30, data={
                "client_id": os.environ["GOOGLE_CLIENT_ID"], "client_secret": os.environ["GOOGLE_CLIENT_SECRET"],
                "refresh_token": os.environ["GOOGLE_REFRESH_TOKEN"], "grant_type": "refresh_token"})
            response.raise_for_status()
        except requests.RequestException as e:
            raise Answer({"error": f"refreshing the token failed: {type(e).__name__}: {e}"}, 502)
        reply = response.json()
        data.token, data.expires_at = reply["access_token"], int(time.time()) + int(reply.get("expires_in", 3600))
    return {"token": data.token, "expiresAt": data.expires_at}


OPS = {"get": op_get, "gets": op_gets, "scrape": op_scrape, "poll": op_poll, "fan_out": op_fan_out, "token": op_token}


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
