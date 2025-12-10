"""
Schema transformation utilities for MCP tool schemas.

Provides functions to transform JSON schemas for compatibility with
different LLM providers (OpenAI, Anthropic, etc.).
"""

from copy import deepcopy
from typing import Any


def sanitize_for_openai_tools(schema: dict) -> dict:
    """
    Make a Pydantic/JSON Schema object compatible with OpenAI tool schema.

    OpenAI's function calling API has stricter requirements than standard JSON Schema:
    - Does not support 'integer' type (only 'number')
    - Does not support 'null' in union types
    - Requires simplification of oneOf/anyOf in certain cases

    Transformations applied:
    - 'integer' -> 'number' (+ multipleOf: 1 to preserve integer semantics)
    - Remove 'null' from union type arrays
    - Coerce integer-only enums to number type
    - Simplify oneOf/anyOf when they only differ by integer/number

    Args:
        schema: The JSON Schema to sanitize

    Returns:
        A new schema dict compatible with OpenAI's tool schema requirements

    Note:
        Original implementation by GPT-5 for serena, moved to nisaba framework.
    """
    s = deepcopy(schema)

    def walk(node: Any) -> Any:
        if not isinstance(node, dict):
            # lists get handled by parent calls
            return node

        # ---- handle type ----
        t = node.get("type")
        if isinstance(t, str):
            if t == "integer":
                node["type"] = "number"
                # preserve existing multipleOf but ensure it's integer-like
                if "multipleOf" not in node:
                    node["multipleOf"] = 1
        elif isinstance(t, list):
            # remove 'null' (OpenAI tools don't support nullables)
            t2 = [x if x != "integer" else "number" for x in t if x != "null"]
            if not t2:
                # fall back to object if it somehow becomes empty
                t2 = ["object"]
            node["type"] = t2[0] if len(t2) == 1 else t2
            if "integer" in t or "number" in t2:
                # if integers were present, keep integer-like restriction
                node.setdefault("multipleOf", 1)

        # ---- enums of integers -> number ----
        if "enum" in node and isinstance(node["enum"], list):
            vals = node["enum"]
            if vals and all(isinstance(v, int) for v in vals):
                node.setdefault("type", "number")
                # keep them as ints; JSON 'number' covers ints
                node.setdefault("multipleOf", 1)

        # ---- simplify anyOf/oneOf if they only differ by integer/number ----
        for key in ("oneOf", "anyOf"):
            if key in node and isinstance(node[key], list):
                # Special case: anyOf or oneOf with "type X" and "null"
                if len(node[key]) == 2:
                    types = [sub.get("type") for sub in node[key]]
                    if "null" in types:
                        non_null_type = next(t for t in types if t != "null")
                        if isinstance(non_null_type, str):
                            node["type"] = non_null_type
                            node.pop(key, None)
                            continue
                simplified = []
                changed = False
                for sub in node[key]:
                    sub = walk(sub)  # recurse
                    simplified.append(sub)
                # If all subs are the same after integer→number, collapse
                try:
                    import json

                    canon = [json.dumps(x, sort_keys=True) for x in simplified]
                    if len(set(canon)) == 1:
                        # copy the single schema up
                        only = simplified[0]
                        node.pop(key, None)
                        for k, v in only.items():
                            if k not in node:
                                node[k] = v
                        changed = True
                except Exception:
                    pass
                if not changed:
                    node[key] = simplified

        # ---- recurse into known schema containers ----
        for child_key in ("properties", "patternProperties", "definitions", "$defs"):
            if child_key in node and isinstance(node[child_key], dict):
                for k, v in list(node[child_key].items()):
                    node[child_key][k] = walk(v)

        # arrays/items
        if "items" in node:
            node["items"] = walk(node["items"])

        # allOf/if/then/else - pass through with integer→number conversions applied inside
        for key in ("allOf",):
            if key in node and isinstance(node[key], list):
                node[key] = [walk(x) for x in node[key]]

        if "if" in node:
            node["if"] = walk(node["if"])
        if "then" in node:
            node["then"] = walk(node["then"])
        if "else" in node:
            node["else"] = walk(node["else"])

        return node

    return walk(s)
