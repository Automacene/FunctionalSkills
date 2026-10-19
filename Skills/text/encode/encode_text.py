import base64
import json


def handler(context, event):
    try:
        body = event.body if isinstance(event.body, dict) else json.loads(event.body or b"")
        text = body["text"]
    except (ValueError, KeyError, TypeError) as e:
        return _reply(context, {"error": f"expected a JSON body with a 'text' string: {type(e).__name__}: {e}"}, 400)
    return _reply(context, {"encoded": base64.b64encode(str(text).encode()).decode()})


def _reply(context, body, status=200):
    return context.Response(body=json.dumps(body), content_type="application/json", status_code=status)
