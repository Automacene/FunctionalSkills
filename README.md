# FunctionalSkills

Functions an agent or a workflow runs directly, deployed to [nuclio](https://nuclio.io). Each one is nuclio's own
function config next to its one code file. Their prompt counterparts are in
[SemanticSkills](https://github.com/Automacene/SemanticSkills).

## Layout

```
Skills/<category>/<skill>/<skill>_<category>.yaml
Skills/<category>/<skill>/<module>.py
```

```
Skills/math/basic/basic_math.yaml
Skills/math/basic/basic_math.py
```

A skill's folder holds everything that is its own. The config's filename is its `metadata.name`, which is the
function's name on nuclio, so it is unique across every category. Names use `_`, never `-`: Kafka topics use `-`, and
keeping functions and schemas on `_` keeps the two from being mistaken for each other.

A function is one code file. Something that needs several is a service, not a skill.

## The built-ins

Most of what a workflow does between service calls is in four functions, so a stack runs four nuclio functions for
it rather than one per kind of step. Each takes either a named operation (`op`) or an expression of built-ins, a call
being an object with one key that starts with `$` and the list of its arguments: `{"$get": [{"$first": [x]}, "id"]}`.

| Skill | What it holds | Waits on |
|---|---|---|
| `builtins/data` | The shape of JSON values: lists, objects, comparisons, `min` / `max`; ops `rows`, `pluck`, `group`, `find` | nothing |
| `builtins/text` | Strings, encodings, JSON text; ops `format`, `parse_json` | nothing |
| `builtins/web` | Outside HTTP: ops `get`, `gets`, `scrape`, `poll`, `fan_out`, `token` (Google OAuth) | outside servers |
| `builtins/stack` | DAEGON's own services and functions: ops `fan_out` (Kafka), `op_on` (a nuclio function per item) | the stack |

Others stand alone: `math/basic` (to grow into a larger math function) and `diagnostics/probe` (for test suites).

## A skill

A minimal one, for the format:

```yaml
apiVersion: nuclio.io/v1beta1
kind: NuclioFunction
metadata:
  name: encode_text
  labels:
    automacene.org/category: text
    automacene.org/skill: encode
    automacene.org/version: "1.0.0"
  annotations:
    automacene.org/title: Base64 Text Encoding
    automacene.org/authors: Codie Petersen <codie@asteres-technologies.com>
spec:
  description: Encode a string as base64.
  runtime: python:3.11
  handler: encode_text:handler
  minReplicas: 1
  maxReplicas: 1
  triggers:
    http:
      kind: http
      maxWorkers: 4

  inputs:
    - name: text
      type: string
      description: The string to encode.
      required: true
  output:
    - name: encoded
      type: string
      description: The string's UTF-8 bytes, base64 encoded.
      required: true
  examples:
    - input: {text: hello}
      output: {encoded: aGVsbG8=}
    - input: {}
      status: 400
```

Everything but `spec.inputs`, `spec.output` and `spec.examples` is nuclio's own
[function configuration](https://docs.nuclio.io/en/stable/reference/function-configuration/function-configuration-reference.html),
so dependencies go in `spec.build.commands` (`pip install ...`), configuration in `spec.env`, and other triggers
(kafka, cron) in `spec.triggers`.

| Field | | |
|---|---|---|
| `metadata.name` | required | `<skill>_<category>`, lowercase letters, digits and `_`. Matches the filename. |
| `metadata.namespace` | absent | Set when deployed. |
| `labels[automacene.org/category]` | required | The category folder. |
| `labels[automacene.org/skill]` | required | The skill folder. |
| `labels[automacene.org/version]` | required | Semver. |
| `annotations[automacene.org/title]` | required | A human name. |
| `annotations[automacene.org/authors]` | required | npm author strings: `Name <email> (url)`, comma separated. |
| `spec.description` | required | |
| `spec.handler` | required | `<module>:<function>`. `<module>` names the code file. |
| `spec.runtime` | required | Picks the code file's extension: `python` `.py`, `golang` `.go`, `nodejs` `.js`, `shell` `.sh`. |
| `spec.build` | optional | Never `path` or `functionSourceCode`: the code file is what gets deployed. |
| `spec.inputs[]` | | `name`, `type`, `description`, and `required` or `default`. The JSON body's keys. |
| `spec.output[]` | | `name`, `type`, `description`, `required`. The JSON reply's keys. |
| `spec.examples[]` | | `input`, and `output` and/or `status` (200 when left out). |

Types are JSON's: `string`, `integer`, `number`, `boolean`, `array`, `object`. A value that can be of any type is
`object`, and its description says so.

A handler takes a JSON body and answers with a JSON body. A bad request answers 400 with `{"error": ...}`; anything
it raises is a 500.

## The contract on nuclio

Nuclio keeps annotations but drops spec keys it does not know. When a skill is deployed, its `inputs` and `output` are
copied into the annotation `automacene.org/contract` as JSON, so a deployed function still says what it takes and
returns. `examples` stay in the repo.

## Deploying

The repo holds plaintext. Deploying inlines the code into `spec.build.functionSourceCode` as base64
(`codeEntryType: sourceCode`), which is how nuclio itself stores a function.

From DAEGON:

```
daegon functions add builtins/text                     from GitHub, Automacene/FunctionalSkills/main
daegon functions add builtins/text --from <folder>     from a checkout
daegon functions add builtins/text --from <owner>/<repo>/<branch>
daegon functions                                       what is deployed, with inputs and outputs
daegon functions remove text_builtins
```

## Checking

```
python3 Scripts/validate.py [Skills/<category>/<skill> ...]
```

Checks the layout and fields above, then runs each Python skill's `examples` against its handler in-process, with a
stand-in for nuclio's context, so no nuclio is needed. Exits non-zero if anything fails.
