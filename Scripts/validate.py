"""Check every functional skill against the layout, and run its examples against its handler.

Reports rather than throwing, so the whole corpus is checked in one pass. Exits non-zero when anything failed, so it
works as a build step. Examples run in-process with a stand-in for nuclio's context and event, so no nuclio is needed.

    python3 Scripts/validate.py [--quiet] [Skills/<category>/<skill> ...]
"""
import importlib.util
import json
import pathlib
import re
import sys
import yaml

sys.dont_write_bytecode = True  # importing a skill to run its examples leaves no __pycache__ in Skills/

ROOT = pathlib.Path(__file__).resolve().parent.parent
SKILLS = ROOT / "Skills"

NAME = re.compile(r"^[a-z][a-z0-9_]{0,61}[a-z0-9]$")   # nuclio function names; _ keeps them apart from topics
SEMVER = re.compile(r"^\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?$")
TYPES = {"string", "integer", "number", "boolean", "array", "object"}
EXTENSIONS = {"python": ".py", "golang": ".go", "nodejs": ".js", "shell": ".sh", "ruby": ".rb", "java": ".java"}


class Response:
    def __init__(self, body=None, headers=None, content_type="text/plain", status_code=200):
        self.body, self.headers, self.content_type, self.status_code = body, headers or {}, content_type, status_code


class Logger:
    def __getattr__(self, name):
        return lambda *args, **kwargs: None


class Context:
    Response = Response
    logger = Logger()
    user_data = type("UserData", (), {})()


class Event:
    def __init__(self, body):
        self.body, self.headers, self.method, self.path = body, {"Content-Type": "application/json"}, "POST", "/"


def check_fields(fields, where, problems):
    names = set()
    for field in fields or []:
        name = field.get("name")
        if not name:
            problems.append(f"{where}: a field has no name")
            continue
        if name in names:
            problems.append(f"{where}: {name} appears twice")
        names.add(name)
        if field.get("type") not in TYPES:
            problems.append(f"{where}.{name}: type {field.get('type')!r} is not one of {sorted(TYPES)}")
        if not field.get("description"):
            problems.append(f"{where}.{name}: no description")
    return names


def run_examples(doc, code_path, problems):
    spec = doc["spec"]
    module_name, function_name = spec["handler"].split(":")
    loader = importlib.util.spec_from_file_location(f"skill_{module_name}", code_path)
    module = importlib.util.module_from_spec(loader)
    try:
        loader.loader.exec_module(module)
    except Exception as e:
        problems.append(f"{code_path.name} will not import: {type(e).__name__}: {e}")
        return
    handler = getattr(module, function_name, None)
    if not callable(handler):
        problems.append(f"{code_path.name} has no {function_name}()")
        return
    for index, example in enumerate(spec.get("examples") or [], 1):
        try:
            reply = handler(Context(), Event(json.dumps(example.get("input", {})).encode()))
        except Exception as e:
            problems.append(f"example {index} raised {type(e).__name__}: {e}")
            continue
        status = getattr(reply, "status_code", 200)
        body = getattr(reply, "body", reply)
        if status != example.get("status", 200):
            problems.append(f"example {index}: status {status}, expected {example.get('status', 200)}")
        if "output" in example:
            try:
                got = json.loads(body) if isinstance(body, (str, bytes)) else body
            except ValueError:
                got = body
            if got != example["output"]:
                problems.append(f"example {index}: returned {got!r}, expected {example['output']!r}")


def check(config):
    """Everything wrong with one skill, as a list of sentences."""
    problems = []
    try:
        doc = yaml.safe_load(config.read_text(encoding="utf-8"))
    except Exception as error:
        return [f"will not parse: {error}"]
    if not isinstance(doc, dict):
        return ["is not a mapping"]
    if doc.get("apiVersion") != "nuclio.io/v1beta1" or doc.get("kind") != "NuclioFunction":
        problems.append("is not apiVersion nuclio.io/v1beta1, kind NuclioFunction")

    meta, spec = doc.get("metadata") or {}, doc.get("spec") or {}
    category, skill = config.parent.parent.name, config.parent.name
    name = meta.get("name")
    if not name or not NAME.match(str(name)):
        problems.append(f"metadata.name {name!r} is not lowercase letters, digits and _")
    if name != config.stem:
        problems.append(f"filename {config.stem!r} does not match metadata.name {name!r}")
    if "namespace" in meta:
        problems.append("metadata.namespace is set at deploy time; leave it out")

    labels, annotations = meta.get("labels") or {}, meta.get("annotations") or {}
    if labels.get("automacene.org/category") != category:
        problems.append(f"label automacene.org/category is {labels.get('automacene.org/category')!r}, the folder is {category!r}")
    if labels.get("automacene.org/skill") != skill:
        problems.append(f"label automacene.org/skill is {labels.get('automacene.org/skill')!r}, the folder is {skill!r}")
    if not SEMVER.match(str(labels.get("automacene.org/version", ""))):
        problems.append(f"label automacene.org/version {labels.get('automacene.org/version')!r} is not semver")
    for key in ("automacene.org/title", "automacene.org/authors"):
        if not annotations.get(key):
            problems.append(f"missing annotation {key}")
    if not spec.get("description"):
        problems.append("no spec.description")
    if "build" in spec and ("functionSourceCode" in spec["build"] or "path" in spec["build"]):
        problems.append("spec.build names the code; the code file next to the config is what gets deployed")

    handler, runtime = str(spec.get("handler", "")), str(spec.get("runtime", ""))
    extension = EXTENSIONS.get(runtime.split(":")[0])
    if ":" not in handler:
        problems.append(f"spec.handler {handler!r} is not <module>:<function>")
    if not extension:
        problems.append(f"spec.runtime {runtime!r} is not a known runtime")

    inputs = check_fields(spec.get("inputs"), "spec.inputs", problems)
    check_fields(spec.get("output"), "spec.output", problems)
    for index, example in enumerate(spec.get("examples") or [], 1):
        unknown = set(example.get("input") or {}) - inputs
        if unknown:
            problems.append(f"example {index} sends undeclared inputs: {', '.join(sorted(unknown))}")

    others = [f for f in config.parent.glob("*.yaml") if f != config]
    if others:
        problems.append(f"more than one config in {skill}/: {', '.join(f.name for f in others)}")
    if ":" in handler and extension:
        code = config.parent / (handler.split(":")[0] + extension)
        if not code.is_file():
            problems.append(f"no {code.name} next to the config")
        elif extension == ".py" and spec.get("examples") and not problems:
            run_examples(doc, code, problems)
    return problems


def main(argv):
    quiet = "--quiet" in argv
    folders = [pathlib.Path(a).resolve() for a in argv if not a.startswith("--")] or [SKILLS]
    configs = sorted(c for folder in folders for c in folder.rglob("*.yaml")
                     if len(c.relative_to(SKILLS).parts) == 3)
    failed = 0
    for config in configs:
        problems = check(config)
        if problems:
            failed += 1
            print(f"✗ {config.relative_to(ROOT)}")
            for problem in problems:
                print(f"    {problem}")
        elif not quiet:
            print(f"✓ {config.relative_to(ROOT)}")
    print(f"\n{len(configs) - failed}/{len(configs)} skills pass")
    return 1 if failed or not configs else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
