from __future__ import annotations
import re
from pathlib import Path
from agent.tools.registry import registry
from agent.core.observability import instrument
from agent.core.retry import rate_limit
from agent.core.errors import NexusError


# ── File I/O ──────────────────────────────────────────────────────────────

@registry.register(
    name="code.read_file",
    description="Read the contents of a file from the workspace",
    input_schema={"type": "object", "properties": {"file_path": {"type": "string"}}, "required": ["file_path"]},
)
@instrument(namespace="code", tool="read_file")
def read_file(file_path: str) -> dict:
    rate_limit("code")
    content = Path(file_path).read_text()
    return {"file_path": file_path, "content": content, "lines": len(content.splitlines())}


@registry.register(
    name="code.write_file",
    description="Write content to a file, creating parent directories if needed",
    input_schema={
        "type": "object",
        "properties": {"file_path": {"type": "string"}, "content": {"type": "string"}},
        "required": ["file_path", "content"],
    },
)
@instrument(namespace="code", tool="write_file")
def write_file(file_path: str, content: str) -> dict:
    rate_limit("code")
    p = Path(file_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content)
    return {"file_path": file_path, "bytes_written": len(content)}


@registry.register(
    name="code.list_dir",
    description="List files and directories at the given path",
    input_schema={"type": "object", "properties": {"directory": {"type": "string"}}, "required": ["directory"]},
)
@instrument(namespace="code", tool="list_dir")
def list_dir(directory: str) -> dict:
    p = Path(directory)
    entries = [
        {"name": e.name, "type": "dir" if e.is_dir() else "file", "size": e.stat().st_size if e.is_file() else 0}
        for e in sorted(p.iterdir())
    ]
    return {"directory": directory, "entries": entries}


@registry.register(
    name="code.delete_file",
    description="Delete a file from the workspace",
    input_schema={"type": "object", "properties": {"file_path": {"type": "string"}}, "required": ["file_path"]},
)
@instrument(namespace="code", tool="delete_file")
def delete_file(file_path: str) -> dict:
    Path(file_path).unlink()
    return {"deleted": file_path}


@registry.register(
    name="code.search_code",
    description="Search for a regex pattern across all files in a directory",
    input_schema={
        "type": "object",
        "properties": {"pattern": {"type": "string"}, "directory": {"type": "string"}},
        "required": ["pattern", "directory"],
    },
)
@instrument(namespace="code", tool="search_code")
def search_code(pattern: str, directory: str) -> dict:
    rate_limit("code")
    matches = []
    for path in Path(directory).rglob("*"):
        if path.is_file():
            try:
                for i, line in enumerate(path.read_text().splitlines(), 1):
                    if re.search(pattern, line):
                        matches.append({"file": str(path), "line": i, "text": line.strip()})
            except UnicodeDecodeError:
                pass
    return {"pattern": pattern, "matches": matches}


@registry.register(
    name="code.apply_patch",
    description="Replace old_string with new_string in a file (exact match required)",
    input_schema={
        "type": "object",
        "properties": {
            "file_path": {"type": "string"},
            "old_string": {"type": "string"},
            "new_string": {"type": "string"},
        },
        "required": ["file_path", "old_string", "new_string"],
    },
)
@instrument(namespace="code", tool="apply_patch")
def apply_patch(file_path: str, old_string: str, new_string: str) -> dict:
    rate_limit("code")
    p = Path(file_path)
    content = p.read_text()
    if old_string not in content:
        raise NexusError(f"apply_patch: old_string not found in {file_path}")
    p.write_text(content.replace(old_string, new_string, 1))
    return {"file_path": file_path, "patched": True}
