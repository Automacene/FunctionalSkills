"""Send one request per item to a DAEGON service over Kafka and return the replies in item order."""
import json
import os
import time
import uuid

from confluent_kafka import Consumer, Producer
from confluent_kafka.schema_registry import SchemaRegistryClient
from confluent_kafka.schema_registry.json_schema import JSONSerializer
from confluent_kafka.serialization import MessageField, SerializationContext

SOURCE = "fan_out_daegon"


def handler(context, event):
    try:
        body = _body(event)
        topic, reply_topic = _input(body, "topic", str), _input(body, "replyTopic", str)
        target, items, shared = _input(body, "target", str), _input(body, "items", list), _input(body, "params", dict, {})
        delay = _input(body, "delayMs", (int, float), 0) / 1000
        timeout = _input(body, "timeoutSeconds", (int, float), 120)
        # The calling workflow's correlation id, so the service's logs join the request's; each item is its own call
        correlation_id = _header(event, "X-Daegon-Correlation-Id") or str(uuid.uuid4())
        results, missing = _fan_out(context.user_data, topic, reply_topic, target, items, shared, delay, timeout, correlation_id)
        if missing:
            return _reply(context, {"error": f"no reply for items {missing} within {timeout}s", "results": results}, 504)
        result = {"results": results}
    except (BadRequest, ValueError, TypeError) as e:
        return _reply(context, {"error": str(e)}, 400)
    return _reply(context, result)


def init_context(context):
    context.user_data.bootstrap = os.environ["BOOTSTRAP_SERVERS"]
    context.user_data.registry = SchemaRegistryClient({"url": os.environ["SCHEMA_REGISTRY"]})
    context.user_data.producer = Producer({"bootstrap.servers": context.user_data.bootstrap})


def _fan_out(data, topic, reply_topic, target, items, shared, delay, timeout, correlation_id):
    """Send every item, then collect replies by call id. Returns the replies in item order and the indexes with none."""
    consumer = Consumer({"bootstrap.servers": data.bootstrap, "group.id": f"{SOURCE}_{uuid.uuid4().hex[:8]}",
                         "auto.offset.reset": "latest", "enable.auto.commit": False})
    consumer.subscribe([reply_topic])
    deadline = time.time() + 30
    while not consumer.assignment() and time.time() < deadline:
        consumer.poll(0.2)  # a reply sent before the consumer is assigned would be missed

    subject = topic.replace("-", "_")  # DAEGON's convention: the subject of topic a-b-c is a_b_c
    serializer = JSONSerializer(data.registry.get_latest_version(subject).schema.schema_str, data.registry,
                                conf={"auto.register.schemas": False, "use.latest.version": True,
                                      "subject.name.strategy": lambda ctx, name: subject})
    waiting, results = {}, [None] * len(items)
    try:
        for index, item in enumerate(items):
            call_id = str(uuid.uuid4())
            waiting[call_id] = index
            payload = {**shared, **(item if isinstance(item, dict) else {"item": item}),
                       "metadata": {"target": target, "source": SOURCE, "sourceType": "function",
                                    "timestamp": time.time(), "correlationId": correlation_id, "callId": call_id}}
            data.producer.produce(topic, serializer(payload, SerializationContext(topic, MessageField.VALUE)),
                                  headers=[("daegon-target", target)])  # lets other services pass it over unread
            if delay and index < len(items) - 1:
                time.sleep(delay)
        data.producer.flush()

        deadline = time.time() + timeout
        while waiting and time.time() < deadline:
            message = consumer.poll(1.0)
            if message is None or message.error():
                continue
            reply = json.loads(message.value()[5:])  # skip the schema registry's 5-byte header
            index = waiting.pop((reply.get("metadata") or {}).get("callId"), None)
            if index is not None:
                results[index] = reply
    finally:
        consumer.close()
    return results, sorted(waiting.values())


def _header(event, name):
    """A request header, by name in any case."""
    return next((v for k, v in (event.headers or {}).items() if k.lower() == name.lower()), None)


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
