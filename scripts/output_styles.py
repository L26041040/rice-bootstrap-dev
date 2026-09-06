"""RICE-BOOTSTRAP-023: mechanical lookup of Claude Code output styles.

Not a registry -- a read of the native locations Claude Code itself reads:
project `.claude/output-styles/*.md`, user `<config dir>/output-styles/*.md`,
and every installed plugin's `output-styles/*.md` (addressed as
`<plugin>:<name>`), plus the built-in names. Used by the verifier to check
that a configured `outputStyle` resolves to something real, and by the
bootstrap skill to list what the OWNER can choose from.
"""
import json
import os
from pathlib import Path

# Claude Code's built-in styles. "default" and an absent key both mean the
# native default. The other two names are from the official output-style
# docs; inferred, not runtime-tested here.
BUILTIN = ("default", "Explanatory", "Learning")


def frontmatter_name(path: Path) -> str | None:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return None
    if not lines or lines[0].strip() != "---":
        return None
    for line in lines[1:40]:
        if line.strip() == "---":
            break
        if line.startswith("name:"):
            return line.split(":", 1)[1].strip().strip('"').strip("'")
    return None


def _config_dir() -> Path:
    return Path(os.environ.get("CLAUDE_CONFIG_DIR") or (Path.home() / ".claude"))


def _installed_plugins(plugin_probe) -> list[dict]:
    try:
        r = plugin_probe(["claude", "plugin", "list", "--json"])
        entries = json.loads(r.stdout or "[]") if r.returncode == 0 else []
    except (OSError, json.JSONDecodeError, AttributeError):
        return []
    return [e for e in entries if isinstance(e, dict)] if isinstance(entries, list) else []


def available(repo_root, plugin_probe, self_plugin=None) -> list[dict]:
    """Every style value that would resolve, with where it comes from.
    `self_plugin=(name, root)` adds the styles of the build running this
    code -- which, once plugin:build passes, is the installed build."""
    repo_root = Path(repo_root)
    out = [{"value": b, "source": "builtin", "path": None} for b in BUILTIN]
    if self_plugin:
        name, root = self_plugin
        for f in sorted((Path(root) / "output-styles").glob("*.md")) if (Path(root) / "output-styles").is_dir() else []:
            sname = frontmatter_name(f)
            if sname:
                out.append({"value": f"{name}:{sname}", "source": f"this build ({name})", "path": str(f)})
    for scope, d in (("project", repo_root / ".claude" / "output-styles"), ("user", _config_dir() / "output-styles")):
        if d.is_dir():
            for f in sorted(d.glob("*.md")):
                name = frontmatter_name(f)
                if name:
                    out.append({"value": name, "source": scope, "path": str(f)})
    for e in _installed_plugins(plugin_probe):
        plugin = str(e.get("id", "")).split("@", 1)[0]
        root = Path(e["installPath"]) if e.get("installPath") else None
        if plugin and root and (root / "output-styles").is_dir():
            for f in sorted((root / "output-styles").glob("*.md")):
                name = frontmatter_name(f)
                if name:
                    out.append({"value": f"{plugin}:{name}", "source": f"plugin {e.get('id')}", "path": str(f)})
    return out


def resolve(value, repo_root, plugin_probe, self_plugin=None) -> tuple[bool, str]:
    """(ok, detail) for a configured outputStyle value. None = key absent."""
    if value is None:
        return True, "outputStyle absent -> Claude native default"
    if not isinstance(value, str) or not value.strip():
        return False, f"outputStyle is {value!r}, not a style name"
    for s in available(repo_root, plugin_probe, self_plugin):
        if s["value"] == value:
            return True, f"{value!r} -> {s['source']}" + (f" ({s['path']})" if s["path"] else "")
    return False, f"outputStyle {value!r} does not resolve to any built-in, project, user, or installed-plugin style"
