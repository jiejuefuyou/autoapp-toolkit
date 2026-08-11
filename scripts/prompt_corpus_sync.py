#!/usr/bin/env python3
"""Mechanically project one canonical prompt corpus into configured clients.

Dry-run is the default. A target must opt in with ``"writable": true`` and the
caller must pass ``--write`` before any file changes. Comparison normalization
belongs only to ``prompt_corpus_audit``; generated clients retain the canonical
Unicode, punctuation, and line content. After writing, the normal corpus audit
runs again and the command fails unless every selected target is an exact
logical match to canonical.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any, Mapping, Sequence

import prompt_corpus_audit as corpus


class SyncError(RuntimeError):
    """Unsafe configuration or failed post-write verification."""


def array_bounds(source: str, marker: str) -> tuple[int, int]:
    marker_index = source.find(marker)
    if marker_index < 0:
        raise SyncError(f"marker not found: {marker!r}")
    start = source.find("[", marker_index + len(marker))
    if start < 0:
        raise SyncError(f"array not found after marker: {marker!r}")

    depth = 0
    in_string = False
    escaped = False
    for index in range(start, len(source)):
        character = source[index]
        if in_string:
            if escaped:
                escaped = False
            elif character == "\\":
                escaped = True
            elif character == '"':
                in_string = False
            continue
        if character == '"':
            in_string = True
        elif character == "[":
            depth += 1
        elif character == "]":
            depth -= 1
            if depth == 0:
                return start, index + 1
    raise SyncError(f"unterminated array after marker: {marker!r}")


def _project_text(value: Any, *, field: str, surface_id: str, index: int) -> str:
    """Select canonical text without Unicode normalization or punctuation edits.

    Leading/trailing whitespace is removed because every shipping client already
    treats those edges as accidental. Internal whitespace, composed/decomposed
    Unicode, full-width punctuation, and line breaks remain exactly canonical.
    """

    projected = "" if value is None else str(value).strip()
    if not projected:
        raise SyncError(
            f"surface {surface_id}: canonical prompt {index + 1} has empty {field}"
        )
    return projected


def _project_tags(
    value: Any,
    *,
    surface_id: str,
    index: int,
) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise SyncError(
            f"surface {surface_id}: canonical prompt {index + 1} tags are not an array"
        )
    result: list[str] = []
    seen: set[str] = set()
    for raw_tag in value:
        tag = str(raw_tag).strip()
        if not tag or tag in seen:
            continue
        seen.add(tag)
        result.append(tag)
    return result


def canonical_payload(
    config: Mapping[str, Any],
    workspace: Path,
) -> list[dict[str, Any]]:
    """Read raw canonical fields after audit validation.

    Audit records are normalized comparison projections. Reusing them for output
    would silently rewrite content (for example Chinese full-width punctuation
    under NFKC). The synchronizer therefore re-reads the configured canonical
    source and applies only field selection, edge trimming, and exact tag dedupe.
    """

    canonical_id = config.get("canonical")
    surfaces = config.get("surfaces", [])
    canonical_surface = next(
        (
            surface
            for surface in surfaces
            if isinstance(surface, Mapping) and surface.get("id") == canonical_id
        ),
        None,
    )
    if canonical_surface is None:
        raise SyncError(f"canonical surface {canonical_id!r} is not configured")

    repo = canonical_surface.get("repo")
    relative_path = canonical_surface.get("path")
    source_format = canonical_surface.get("format", "json-array")
    if not isinstance(repo, str) or not repo:
        raise SyncError(f"surface {canonical_id}: repo is required")
    if not isinstance(relative_path, str) or not relative_path:
        raise SyncError(f"surface {canonical_id}: path is required")

    path = (workspace / repo / relative_path).resolve()
    raw_prompts = corpus.load_raw_prompts(
        path,
        source_format,
        canonical_surface.get("marker"),
    )
    title_fields = corpus.candidate_fields(canonical_surface, "title")
    body_fields = corpus.candidate_fields(canonical_surface, "body")
    tag_fields = corpus.candidate_fields(canonical_surface, "tags")

    payload: list[dict[str, Any]] = []
    for index, raw in enumerate(raw_prompts):
        if not isinstance(raw, Mapping):
            raise SyncError(
                f"surface {canonical_id}: canonical prompt {index + 1} is not an object"
            )
        title = _project_text(
            corpus.first_field(raw, title_fields),
            field="title",
            surface_id=str(canonical_id),
            index=index,
        )
        body = _project_text(
            corpus.first_field(raw, body_fields),
            field="body",
            surface_id=str(canonical_id),
            index=index,
        )
        tags = _project_tags(
            corpus.first_field(raw, tag_fields, []),
            surface_id=str(canonical_id),
            index=index,
        )
        payload.append({"title": title, "body": body, "tags": tags})
    return payload


def serialize_payload(payload: Sequence[Mapping[str, Any]], indent: int) -> str:
    return json.dumps(
        list(payload),
        ensure_ascii=False,
        indent=indent,
        separators=(",", ": ") if indent else (",", ":"),
    )


def render_target(
    current_source: str,
    target: Mapping[str, Any],
    payload: Sequence[Mapping[str, Any]],
) -> str:
    source_format = target.get("format", "json-array")
    sync_config = target.get("sync", {})
    indent = sync_config.get("indent", 2)
    if not isinstance(indent, int) or indent < 0 or indent > 8:
        raise SyncError(f"surface {target.get('id')}: sync.indent must be 0...8")
    rendered = serialize_payload(payload, indent)

    if source_format == "json-array":
        return rendered + "\n"
    if source_format in {"js-const-array", "commonjs-array"}:
        default_marker = (
            "const PROMPTS ="
            if source_format == "js-const-array"
            else "module.exports ="
        )
        marker = target.get("marker", default_marker)
        if not isinstance(marker, str) or not marker:
            raise SyncError(f"surface {target.get('id')}: marker is required")
        start, end = array_bounds(current_source, marker)
        return current_source[:start] + rendered + current_source[end:]
    raise SyncError(
        f"surface {target.get('id')}: unsupported writable format {source_format!r}"
    )


def atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=path.parent,
        text=True,
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        temporary.replace(path)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def selected_targets(
    config: Mapping[str, Any],
    requested: set[str] | None,
) -> list[Mapping[str, Any]]:
    canonical = config.get("canonical")
    surfaces = config.get("surfaces", [])
    targets: list[Mapping[str, Any]] = []
    known_ids = {
        surface.get("id")
        for surface in surfaces
        if isinstance(surface, Mapping)
    }
    if requested:
        unknown = requested - known_ids
        if unknown:
            raise SyncError(
                f"unknown target surface(s): {', '.join(sorted(unknown))}"
            )
        if canonical in requested:
            raise SyncError("canonical surface cannot be a sync target")

    for surface in surfaces:
        if not isinstance(surface, Mapping):
            continue
        surface_id = surface.get("id")
        if surface_id == canonical:
            continue
        if requested and surface_id not in requested:
            continue
        if surface.get("writable") is not True:
            if requested and surface_id in requested:
                raise SyncError(
                    f"surface {surface_id!r} is not opted in with writable=true"
                )
            continue
        targets.append(surface)
    if not targets:
        raise SyncError("no writable target surfaces selected")
    return targets


def sync(
    config: Mapping[str, Any],
    workspace: Path,
    requested: set[str] | None,
    write: bool,
) -> tuple[list[dict[str, Any]], corpus.AuditReport | None]:
    before = corpus.audit(config, workspace)
    canonical = next(
        item for item in before.surfaces if item.id == before.canonical
    )
    if (
        canonical.errors
        or canonical.duplicate_identities
        or canonical.exact_duplicates
        or canonical.variable_errors
    ):
        raise SyncError(
            "canonical surface is invalid; refusing to generate downstream bundles"
        )

    payload = canonical_payload(config, workspace)
    if len(payload) != len(canonical.records):
        raise SyncError(
            "raw canonical payload count differs from validated audit records"
        )
    targets = selected_targets(config, requested)
    plan: list[dict[str, Any]] = []

    for target in targets:
        path = (
            workspace / str(target["repo"]) / str(target["path"])
        ).resolve()
        if not path.is_file():
            raise SyncError(f"target file not found: {path}")
        current = path.read_text(encoding="utf-8-sig")
        proposed = render_target(current, target, payload)
        item = {
            "surface": target["id"],
            "path": str(path),
            "prompt_count": len(payload),
            "before_sha256": sha256_text(current),
            "after_sha256": sha256_text(proposed),
            "changed": current != proposed,
        }
        plan.append(item)
        if write and current != proposed:
            atomic_write(path, proposed)

    if not write:
        return plan, None

    after = corpus.audit(config, workspace)
    selected_ids = {item["surface"] for item in plan}
    selected_drift = [
        item for item in after.drift if item.surface in selected_ids
    ]
    selected_surfaces = [
        item for item in after.surfaces if item.id in selected_ids
    ]
    invalid = any(
        item.errors
        or item.duplicate_identities
        or item.exact_duplicates
        or item.variable_errors
        for item in selected_surfaces
    )
    if invalid or any(item.has_drift for item in selected_drift):
        raise SyncError(
            "post-write corpus verification failed; inspect "
            "prompt_corpus_audit output before committing"
        )
    return plan, after


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--workspace", type=Path, default=Path.cwd())
    parser.add_argument(
        "--targets",
        help=(
            "comma-separated surface ids; defaults to every writable "
            "non-canonical surface"
        ),
    )
    parser.add_argument(
        "--write",
        action="store_true",
        help="perform atomic writes; default is dry-run",
    )
    parser.add_argument(
        "--json",
        dest="json_output",
        type=Path,
        help="write sync plan/result JSON",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = build_parser().parse_args(argv)
    requested = None
    if arguments.targets:
        requested = {
            item.strip()
            for item in arguments.targets.split(",")
            if item.strip()
        }
    try:
        config = corpus.read_config(arguments.config)
        plan, report = sync(
            config,
            arguments.workspace.resolve(),
            requested,
            arguments.write,
        )
    except (
        corpus.AuditError,
        SyncError,
        OSError,
        UnicodeError,
        ValueError,
    ) as error:
        print(f"prompt-corpus-sync: {error}", file=sys.stderr)
        return 2

    mode = "WRITE" if arguments.write else "DRY-RUN"
    print(f"Prompt corpus sync — {mode}")
    for item in plan:
        state = "change" if item["changed"] else "unchanged"
        print(
            f"- {item['surface']}: {state}; prompts={item['prompt_count']}; "
            f"{item['before_sha256'][:12]} -> {item['after_sha256'][:12]}"
        )
    if report is not None:
        print("Post-write verification: MATCH")

    if arguments.json_output:
        arguments.json_output.parent.mkdir(parents=True, exist_ok=True)
        arguments.json_output.write_text(
            json.dumps(
                {
                    "mode": mode.lower(),
                    "plan": plan,
                    "post_write_match": report is not None,
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
