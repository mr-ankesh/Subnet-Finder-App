"""
Shared restricted-expression evaluator for the small condition language used
throughout advisor_kb/ (questions.yaml's skip_if/stop_if/escalate_if,
rules/storage_decision_matrix.yaml's when: clauses, mapping's include_if).

Not a general-purpose expression language: these strings are static content
shipped in this repo's own advisor_kb/ files, never user input. The KB
authors write plain-English-adjacent conditions like:

    "subscription_available == false"
    "purpose in [analytics_datalake]"
    "'external_system' in consumer"
    "performance_evidence is empty"
    "pattern.design.change_feed is defined"
    "capacity_estimate contains 'not sure'"

Two things stop this from being valid Python as-is:
  1. Non-Python phrases: "is empty", "is defined", "contains", "always".
  2. Bare enum values (analytics_datalake, restricted_sovereign, ...) are
     unquoted identifiers in the YAML, not string literals — eval() would
     try to look them up as variables and raise NameError.

_evaluate() rewrites both, then runs eval() with __builtins__ stripped and
only the provided namespace as locals — restricted to boolean logic over
that namespace, nothing else reachable.
"""
import logging
import re

log = logging.getLogger(__name__)

_PY_KEYWORDS = {"and", "or", "not", "in", "is", "None", "True", "False", "if", "else"}
_IDENTIFIER_RE = re.compile(r"(?<!\.)\b[a-zA-Z_][a-zA-Z0-9_]*\b")
_QUOTED_RE = re.compile(r"'[^']*'|\"[^\"]*\"")
_BOOL_WORD_RE = re.compile(r"\btrue\b|\bfalse\b")


class AttrDict(dict):
    """Dict that also supports attribute access (derived.lifecycle_to_archive,
    pattern.design.change_feed) — nested dicts are wrapped too. Missing keys
    resolve to None (not AttributeError) so `X is defined`/`X is empty`
    checks work without a KeyError."""

    def __getattr__(self, name):
        v = self.get(name)
        if isinstance(v, dict) and not isinstance(v, AttrDict):
            v = AttrDict(v)
        return v


def _rewrite_phrases(cond: str) -> str:
    cond = re.sub(r"([\w.]+)\s+is\s+empty", r"(not (\1))", cond)
    cond = re.sub(r"([\w.]+)\s+is\s+defined", r"((\1) is not None)", cond)
    cond = re.sub(r"([\w.]+)\s+contains\s+('[^']*'|\"[^\"]*\")", r"(\2 in ((\1) or ''))", cond)
    if cond.strip() == "always":
        return "True"
    return cond


def _rewrite_bool_words(cond: str) -> str:
    """YAML-style lowercase true/false -> Python True/False. Must run only
    on unquoted segments, same reasoning as _quote_bare_enums."""
    parts = _QUOTED_RE.split(cond)
    quoted = _QUOTED_RE.findall(cond)
    out = []
    for i, part in enumerate(parts):
        part = re.sub(r"\btrue\b", "True", part)
        part = re.sub(r"\bfalse\b", "False", part)
        out.append(part)
        if i < len(quoted):
            out.append(quoted[i])
    return "".join(out)


def _quote_bare_enums(cond: str, known_names: set) -> str:
    """Any identifier token that isn't a known field/context name and isn't a
    Python keyword is a bare enum value (e.g. `analytics_datalake` in
    `purpose in [analytics_datalake]`) — quote it so eval() treats it as a
    string, not a variable lookup.

    Must skip identifiers that are already inside a quoted string literal
    (e.g. the `external_system` in `'external_system' in consumer`) — split
    on quoted spans first and only rewrite the unquoted segments."""

    def repl(m):
        tok = m.group(0)
        if tok in _PY_KEYWORDS or tok in known_names:
            return tok
        return f"'{tok}'"

    parts = _QUOTED_RE.split(cond)
    quoted = _QUOTED_RE.findall(cond)
    out = []
    for i, part in enumerate(parts):
        out.append(_IDENTIFIER_RE.sub(repl, part))
        if i < len(quoted):
            out.append(quoted[i])
    return "".join(out)


_OPERATOR_RE = re.compile(r"==|!=|<=|>=|<|>|\bin\b|\bis\b|\band\b|\bor\b|\bnot\b")


def evaluate(cond: str, namespace: dict) -> bool:
    """Evaluate a KB condition string against `namespace` (field name -> value,
    values already an AttrDict where nested dotted access is needed)."""
    if cond is None:
        return False
    cond = cond.strip()
    if not cond:
        return False
    rewritten = _rewrite_phrases(cond)
    rewritten = _rewrite_bool_words(rewritten)
    rewritten = _quote_bare_enums(rewritten, set(namespace.keys()))
    # A rewritten condition with no recognized operator at all (and that
    # isn't the literal "True" the "always" phrase rewrites to) is plain
    # prose, not a condition — e.g. a six-service mapping file's
    # "egress_destinations specified". Left alone, _quote_bare_enums turns
    # every bare word into its own string literal, and Python's implicit
    # adjacent-string-literal concatenation makes the whole thing eval() to
    # a non-empty (truthy) string instead of raising — the opposite of
    # "fail closed". Catch it here, before eval ever runs.
    if rewritten != "True" and not _OPERATOR_RE.search(rewritten):
        raise ValueError(f"advisor condition has no recognizable operator: {cond!r} -> {rewritten!r}")
    try:
        return bool(eval(rewritten, {"__builtins__": {}}, namespace))
    except Exception as exc:
        raise ValueError(f"advisor condition failed to evaluate: {cond!r} -> {rewritten!r}: {exc}") from exc


def validate_condition(cond) -> None:
    """Static validity check for a KB condition string, used by
    advisor/kb_validate.py to reject a malformed condition at UPLOAD time —
    including the exact operator-less-prose bug class evaluate()'s
    _OPERATOR_RE guard already catches at runtime, but only the first time
    some real answer state happens to reach it. Never evaluates the
    condition (no real namespace exists yet at validation time): runs the
    same rewrite pipeline evaluate() uses, quoting every bare identifier
    (there is no known-names set to check field references against, so this
    only proves the string is syntactically valid Python containing a real
    operator — never that a referenced field name exists), then compiles
    (never execs) the result. Raises ValueError with the offending string on
    failure. A None/empty condition is treated as evaluate()'s own trivial
    False case, not an error — this isn't the bug class being guarded
    against."""
    if cond is None or not str(cond).strip():
        return
    cond = str(cond).strip()
    rewritten = _rewrite_phrases(cond)
    rewritten = _rewrite_bool_words(rewritten)
    rewritten = _quote_bare_enums(rewritten, set())
    if rewritten != "True" and not _OPERATOR_RE.search(rewritten):
        raise ValueError(f"advisor condition has no recognizable operator: {cond!r} -> {rewritten!r}")
    try:
        import warnings as _warnings
        with _warnings.catch_warnings():
            # Quoting every bare identifier (no known_names at validation
            # time) turns "field is defined" into "'field' is not None" —
            # a string-literal `is` comparison, which is semantically
            # meaningless here (nothing is ever executed) but triggers
            # Python's own SyntaxWarning. Expected noise, not a real issue.
            _warnings.simplefilter("ignore", SyntaxWarning)
            compile(rewritten, "<advisor-condition>", "eval")
    except SyntaxError as exc:
        raise ValueError(f"advisor condition is not valid syntax: {cond!r} -> {rewritten!r}: {exc}") from exc


def evaluate_safe(cond: str, namespace: dict) -> bool:
    """Same as evaluate(), except any condition that fails to parse/evaluate
    is treated as False instead of raising. Some six-service mapping files'
    `include_if` strings aren't valid condition-language (e.g.
    "egress_destinations specified") or reference fields no question/
    derivation ever sets (e.g. "engineer_access_needed == true") — these only
    gate OPTIONAL follow-on-request items, never blockers, so failing closed
    (item excluded) is the safe default rather than crashing the chat.
    Blockers/escalations/derivations still use the strict evaluate() —
    a malformed condition THERE is a real KB bug that should fail loudly."""
    try:
        return evaluate(cond, namespace)
    except Exception as exc:
        log.warning("advisor: unparseable condition treated as False: %r (%s)", cond, exc)
        return False


def apply_set(ctx: dict, set_str: str) -> None:
    """Parse and apply one or more `field = value` assignments from the KB's
    `set:` strings, separated by ';' for compound sets (e.g. rules.yaml's
    "access_tier = Hot; lifecycle_policy = recommended"). Shared by
    rules_engine.py (derivations, escalation overrides) and question_engine.py
    (skip_if's set:) — same tiny assignment language either way."""
    if not set_str:
        return
    for stmt in set_str.split(";"):
        stmt = stmt.strip()
        if not stmt or "=" not in stmt:
            continue
        field, _, value = stmt.partition("=")
        field, value = field.strip(), value.strip()
        if value == "true":
            value = True
        elif value == "false":
            value = False
        ctx[field] = value
