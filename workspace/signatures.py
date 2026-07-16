"""Extracts lightweight structural signatures from source files —
function/class/variable declarations and a one-line summary (their
docstring's first line, when present) — never full function/method
bodies or module-level statement bodies.

Python only for now, via the stdlib `ast` module (parses, never
executes, so this is safe to run against untrusted source and needs no
new dependency). A file whose language has no registered extractor, or
whose content fails to parse (a syntax error, or a non-Python file
misdetected), contributes no signatures — this is best-effort
enrichment layered onto workspace/indexer.py's metadata, never a hard
requirement blocking indexing.

EXTRACTORS is keyed by the same language names workspace/indexer.py's
detect_language() produces, so adding a new language later is one new
function + one registry entry, not a redesign.
"""

import ast
from typing import Any


def _unparse(node: ast.AST | None) -> str | None:
    if node is None:
        return None
    try:
        return ast.unparse(node)
    except Exception:
        return None


def _first_line(docstring: str | None) -> str | None:
    if not docstring:
        return None
    return docstring.strip().splitlines()[0].strip()


def _params(args: ast.arguments) -> list[dict[str, Any]]:
    params = []
    n_no_default = len(args.args) - len(args.defaults)
    defaults_by_index = {
        n_no_default + i: default for i, default in enumerate(args.defaults)
    }

    for i, arg in enumerate(args.args):
        params.append(
            {
                "name": arg.arg,
                "annotation": _unparse(arg.annotation),
                "default": _unparse(defaults_by_index.get(i)),
            }
        )
    if args.vararg is not None:
        params.append(
            {
                "name": f"*{args.vararg.arg}",
                "annotation": _unparse(args.vararg.annotation),
                "default": None,
            }
        )
    for arg, default in zip(args.kwonlyargs, args.kw_defaults, strict=True):
        params.append(
            {
                "name": arg.arg,
                "annotation": _unparse(arg.annotation),
                "default": _unparse(default),
            }
        )
    if args.kwarg is not None:
        params.append(
            {
                "name": f"**{args.kwarg.arg}",
                "annotation": _unparse(args.kwarg.annotation),
                "default": None,
            }
        )
    return params


def _function_signature(
    node: ast.FunctionDef | ast.AsyncFunctionDef,
) -> dict[str, Any]:
    return {
        "name": node.name,
        "async": isinstance(node, ast.AsyncFunctionDef),
        "params": _params(node.args),
        "returns": _unparse(node.returns),
        "decorators": [_unparse(d) for d in node.decorator_list],
        "summary": _first_line(ast.get_docstring(node)),
    }


def _class_signature(node: ast.ClassDef) -> dict[str, Any]:
    return {
        "name": node.name,
        "bases": [_unparse(b) for b in node.bases],
        "summary": _first_line(ast.get_docstring(node)),
        "methods": [
            _function_signature(item)
            for item in node.body
            if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef))
        ],
    }


def _variable_signature(
    name: str, annotation: ast.AST | None, value: ast.AST | None
) -> dict[str, Any]:
    # Only a simple literal value is worth surfacing (e.g. a default
    # timeout, a feature flag) - anything else just gets its name and
    # annotation, never the full expression (that would drift back
    # towards "the code," not "a signature").
    literal = repr(value.value) if isinstance(value, ast.Constant) else None
    return {"name": name, "annotation": _unparse(annotation), "value": literal}


def extract_python(source: str) -> dict[str, Any] | None:
    """Parse Python source and return its top-level structural
    signatures (functions, classes with their methods, module-level
    variables), or None if it doesn't parse or has nothing to report.
    Never raises - a syntax error (or a non-Python file misdetected as
    Python) is just "no signatures," not a failure."""
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError, RecursionError):
        return None

    functions = []
    classes = []
    variables = []

    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            functions.append(_function_signature(node))
        elif isinstance(node, ast.ClassDef):
            classes.append(_class_signature(node))
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            variables.append(
                _variable_signature(node.target.id, node.annotation, node.value)
            )
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    variables.append(_variable_signature(target.id, None, node.value))

    if not functions and not classes and not variables:
        return None
    return {"functions": functions, "classes": classes, "variables": variables}


EXTRACTORS = {
    "python": extract_python,
}


def extract_signatures(language: str | None, source: str) -> dict[str, Any] | None:
    """Dispatch to the registered extractor for `language`. None if
    there's no extractor for it, extraction found nothing, or the
    source failed to parse."""
    extractor = EXTRACTORS.get(language)
    if extractor is None:
        return None
    return extractor(source)
