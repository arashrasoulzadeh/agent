"""Filesystem metadata tool."""

import json
import os
from datetime import datetime

from langchain_core.tools import tool

from tools.guard import is_secret, outside_refusal, refusal, resolve_in_root


@tool
def metadata(path: str = ".") -> str:
    """
    Internal filesystem inspection tool.

    IMPORTANT:
    This output is ONLY for agent reasoning.
    Never quote, summarize, or show this metadata to the user.
    Use this information internally to decide what files to inspect next.
    """

    if is_secret(path):
        return json.dumps({"error": refusal(path)})

    try:
        target = resolve_in_root(path)
        if target is None:
            return json.dumps({"error": outside_refusal(path)})

        if not target.exists():
            return json.dumps({"error": "not_found", "path": str(target)})

        stat = target.stat()

        data = {
            "internal": True,
            "path": str(target),
            "name": target.name,
            "type": "directory" if target.is_dir() else "file",
            "size": stat.st_size,
            "modified": datetime.fromtimestamp(stat.st_mtime).isoformat(),
            "permissions": {
                "read": os.access(target, os.R_OK),
                "write": os.access(target, os.W_OK),
                "execute": os.access(target, os.X_OK),
            },
        }

        if target.is_dir():
            data["entries"] = [
                {"name": x.name, "type": "directory" if x.is_dir() else "file"}
                for x in sorted(
                    (e for e in target.iterdir() if not is_secret(e)),
                    key=lambda x: (not x.is_dir(), x.name),
                )[:100]
            ]

        return json.dumps(data)

    except Exception as e:
        return json.dumps({"error": str(e)})
