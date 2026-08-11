#!/usr/bin/env python3
"""Audit one prompt corpus across multiple local product repositories.

The tool never executes JavaScript. It supports plain JSON arrays plus the two
packaged formats currently used by PromptVault surfaces:

- ``const PROMPTS = [ ... ];``
- ``module.exports = [ ... ];``

A canonical surface is compared against every other surface by a configurable
identity (title by default). Count drift, missing/extra prompts, body/tag/schema
differences, duplicate identities, exact duplicate content, and malformed typed
variables are reported deterministically.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import unicodedata
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

VARIABLE_PATTERN = re.compile(r"\{\{\s*([^}]+?)\s*\}\}")
ALLOWED_VARIABLE_TYPES = {"string", "int", "multiline"}
SUPPORTED_FORMATS = {"json-array", "js-const-array", "commonjs-array"}


class AuditError(RuntimeError):
    """Configuration, extraction, or schema failure."""


@dataclass(frozen=True)
class VariableSpec:
    name: str
    type: str
    default: str | None


@dataclass(frozen=True)
class PromptRecord:
    surface: str
    index: int
    identity: str
    title: str
    body: str
    tags: tuple[str, ...]
    variables: tuple[VariableSpec, ...]
    content_hash: str


@dataclass
class SurfaceResult:
    id: str
    path: str
    count: int = 0
    records: list[PromptRecord] = field(default_factory=list)
    duplicate_identities: dict[str, list[int]] = field(default_factory=dict)
    exact_duplicates: list[list[int]] = field(default_factory=list)
    variable_errors: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


@dataclass
class DriftResult:
    surface: str
    missing: list[str] = field(default_factory=list)
    extra: list[str] = field(default_factory=list)
    changed: list[dict[str, Any]] = field(default_factory=list)
    order_changed: bool = False

    @property
    def has_drift(self) -> bool:
        return bool(self.missing or self.extra or self.changed or self.order_changed)


@dataclass
class AuditReport:
    canonical: str
    surfaces: list[SurfaceResult]
    drift: list[DriftResult]
    invalid: bool
    has_drift: bool

    def to_json_dict(self) -> dict[str, Any]:
        return {
            "canonical": self.canonical,
            "invalid": self.invalid,
            "has_drift": self.has_drift,
            "surfaces": [surface_to_dict(item) for item in self.surfaces],
            "drift": [asdict(item) | {"has_drift": item.has_drift} for item in self.drift],
        }


def normalize_text(value: Any) -> str:
    text = unicodedata.normalize("NFKC", "" if value is None else str(value))
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    lines = [line.rstrip() for line in text.split("\n")]
    while lines and not lines[0]:
        lines.pop(0)
    while lines and not lines[-1]:
        lines.pop()
    return "\n".join(lines)


def normalize_tag(value: Any) -> str:
    return " ".join(normalize_text(value).split())


def parse_variable_token(raw_token: str) -> VariableSpec | None:
    raw = raw_token.strip()
    if not raw:
        return None
    name_part, separator, descriptor = raw.partition(":")
    name = name_part.strip()
    if not name:
        return None

    variable_type = "string"
    default: str | None = None
    if separator:
        type_part, equals, default_part = descriptor.strip().partition("=")
        requested_type = type_part.strip()
        variable_type = requested_type if requested_type in ALLOWED_VARIABLE_TYPES else "string"
        if equals:
            default = default_part
    return VariableSpec(name=name, type=variable_type, default=default)


def parse_variables(body: str) -> tuple[tuple[VariableSpec, ...], list[str]]:
    seen: dict[str, VariableSpec] = {}
    ordered: list[VariableSpec] = []
    errors: list[str] = []
    for match in VARIABLE_PATTERN.finditer(body):
        spec = parse_variable_token(match.group(1))
        if spec is None:
            errors.append(f"invalid empty variable token at character {match.start()}")
            continue
        existing = seen.get(spec.name)
        if existing is None:
            seen[spec.name] = spec
            ordered.append(spec)
        elif existing != spec:
            errors.append(
                f"variable {spec.name!r} is declared with conflicting schemas: "
                f"{existing.type}/{existing.default!r} vs {spec.type}/{spec.default!r}"
            )
    return tuple(ordered), errors


def find_json_array(source: str, marker: str) -> list[Any]:
    marker_index = source.find(marker)
    if marker_index < 0:
        raise AuditError(f"marker not found: {marker!r}")
    start = source.find("[", marker_index + len(marker))
    if start < 0:
        raise AuditError(f"JSON array not found after marker: {marker!r}")

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
                payload = source[start : index + 1]
                try:
                    parsed = json.loads(payload)
                except json.JSONDecodeError as error:
                    raise AuditError(
                        f"array after {marker!r} is not strict JSON: {error}"
                    ) from error
                if not isinstance(parsed, list):
                    raise AuditError("extracted payload is not an array")
                return parsed
    raise AuditError(f"unterminated JSON array after marker: {marker!r}")


def load_raw_prompts(path: Path, source_format: str, marker: str | None) -> list[Any]:
    if not path.is_file():
        raise AuditError(f"file not found: {path}")
    source = path.read_text(encoding="utf-8-sig")
    if source_format == "json-array":
        try:
            parsed = json.loads(source)
        except json.JSONDecodeError as error:
            raise AuditError(f"invalid JSON in {path}: {error}") from error
        if not isinstance(parsed, list):
            raise AuditError(f"expected a JSON array in {path}")
        return parsed
    if source_format == "js-const-array":
        return find_json_array(source, marker or "const PROMPTS =")
    if source_format == "commonjs-array":
        return find_json_array(source, marker or "module.exports =")
    raise AuditError(f"unsupported format {source_format!r}; expected one of {sorted(SUPPORTED_FORMATS)}")


def first_field(raw: Mapping[str, Any], candidates: Sequence[str], default: Any = "") -> Any:
    for candidate in candidates:
        if candidate in raw and raw[candidate] is not None:
            value = raw[candidate]
            if value != "" and value != []:
                return value
    return default


def candidate_fields(surface: Mapping[str, Any], logical_name: str) -> tuple[str, ...]:
    field_map = surface.get("fields", {})
    raw = field_map.get(logical_name, logical_name)
    if isinstance(raw, str):
        return (raw,)
    if isinstance(raw, list) and raw and all(isinstance(item, str) for item in raw):
        return tuple(raw)
    raise AuditError(f"surface {surface.get('id')!r}: fields.{logical_name} must be a string or non-empty string array")


def normalize_record(
    surface_id: str,
    index: int,
    raw: Any,
    surface: Mapping[str, Any],
    identity_fields: Sequence[str],
) -> tuple[PromptRecord, list[str]]:
    if not isinstance(raw, Mapping):
        raise AuditError(f"surface {surface_id}: prompt {index + 1} is not an object")

    title = normalize_text(first_field(raw, candidate_fields(surface, "title")))
    body = normalize_text(first_field(raw, candidate_fields(surface, "body")))
    raw_tags = first_field(raw, candidate_fields(surface, "tags"), [])
    if not title or not body:
        raise AuditError(f"surface {surface_id}: prompt {index + 1} lacks title/body after field mapping")
    if raw_tags is None:
        raw_tags = []
    if not isinstance(raw_tags, list):
        raise AuditError(f"surface {surface_id}: prompt {index + 1} tags are not an array")
    tags = tuple(dict.fromkeys(tag for tag in (normalize_tag(item) for item in raw_tags) if tag))

    logical_values = {"title": title, "body": body, "tags": "\u001f".join(tags)}
    try:
        identity_parts = [logical_values[name] for name in identity_fields]
    except KeyError as error:
        raise AuditError(f"unsupported identity field: {error.args[0]!r}") from error
    identity = "\u001e".join(identity_parts)
    if not identity:
        raise AuditError(f"surface {surface_id}: prompt {index + 1} has an empty identity")

    variables, variable_errors = parse_variables(body)
    digest_payload = json.dumps(
        {
            "title": title,
            "body": body,
            "tags": tags,
            "variables": [asdict(item) for item in variables],
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    content_hash = hashlib.sha256(digest_payload).hexdigest()
    record = PromptRecord(
        surface=surface_id,
        index=index,
        identity=identity,
        title=title,
        body=body,
        tags=tags,
        variables=variables,
        content_hash=content_hash,
    )
    errors = [f"prompt {index + 1} ({title}): {item}" for item in variable_errors]
    return record, errors


def load_surface(
    surface: Mapping[str, Any],
    workspace: Path,
    identity_fields: Sequence[str],
) -> SurfaceResult:
    surface_id = surface.get("id")
    repo = surface.get("repo")
    relative_path = surface.get("path")
    source_format = surface.get("format", "json-array")
    if not isinstance(surface_id, str) or not surface_id:
        raise AuditError("every surface requires a non-empty id")
    if not isinstance(repo, str) or not repo:
        raise AuditError(f"surface {surface_id}: repo is required")
    if not isinstance(relative_path, str) or not relative_path:
        raise AuditError(f"surface {surface_id}: path is required")
    if source_format not in SUPPORTED_FORMATS:
        raise AuditError(f"surface {surface_id}: unsupported format {source_format!r}")

    path = (workspace / repo / relative_path).resolve()
    result = SurfaceResult(id=surface_id, path=str(path))
    try:
        raw_prompts = load_raw_prompts(path, source_format, surface.get("marker"))
        for index, raw in enumerate(raw_prompts):
            record, variable_errors = normalize_record(
                surface_id, index, raw, surface, identity_fields
            )
            result.records.append(record)
            result.variable_errors.extend(variable_errors)
        result.count = len(result.records)

        identities: dict[str, list[int]] = defaultdict(list)
        hashes: dict[str, list[int]] = defaultdict(list)
        for record in result.records:
            identities[record.identity].append(record.index + 1)
            hashes[record.content_hash].append(record.index + 1)
        result.duplicate_identities = {
            identity: indexes for identity, indexes in identities.items() if len(indexes) > 1
        }
        result.exact_duplicates = [indexes for indexes in hashes.values() if len(indexes) > 1]
    except (AuditError, OSError, UnicodeError, TypeError, ValueError) as error:
        result.errors.append(str(error))
    return result


def compare_records(canonical: PromptRecord, candidate: PromptRecord) -> list[str]:
    changed: list[str] = []
    if canonical.title != candidate.title:
        changed.append("title")
    if canonical.body != candidate.body:
        changed.append("body")
    if canonical.tags != candidate.tags:
        changed.append("tags")
    if canonical.variables != candidate.variables:
        changed.append("variables")
    return changed


def compare_surface(canonical: SurfaceResult, candidate: SurfaceResult) -> DriftResult:
    result = DriftResult(surface=candidate.id)
    if canonical.errors or candidate.errors:
        return result
    canonical_map = {record.identity: record for record in canonical.records}
    candidate_map = {record.identity: record for record in candidate.records}
    result.missing = sorted(set(canonical_map) - set(candidate_map))
    result.extra = sorted(set(candidate_map) - set(canonical_map))
    for identity in sorted(set(canonical_map) & set(candidate_map)):
        changed_fields = compare_records(canonical_map[identity], candidate_map[identity])
        if changed_fields:
            result.changed.append(
                {
                    "identity": identity,
                    "title": canonical_map[identity].title,
                    "fields": changed_fields,
                    "canonical_hash": canonical_map[identity].content_hash,
                    "candidate_hash": candidate_map[identity].content_hash,
                }
            )
    common = set(canonical_map) & set(candidate_map)
    canonical_order = [record.identity for record in canonical.records if record.identity in common]
    candidate_order = [record.identity for record in candidate.records if record.identity in common]
    result.order_changed = canonical_order != candidate_order
    return result


def audit(config: Mapping[str, Any], workspace: Path) -> AuditReport:
    canonical_id = config.get("canonical")
    surfaces_config = config.get("surfaces")
    identity_fields = config.get("identity_fields", ["title"])
    if not isinstance(canonical_id, str) or not canonical_id:
        raise AuditError("config requires canonical surface id")
    if not isinstance(surfaces_config, list) or len(surfaces_config) < 2:
        raise AuditError("config requires at least two surfaces")
    if not isinstance(identity_fields, list) or not identity_fields or not all(
        item in {"title", "body", "tags"} for item in identity_fields
    ):
        raise AuditError("identity_fields must be a non-empty array of title/body/tags")

    surfaces = [load_surface(item, workspace, identity_fields) for item in surfaces_config]
    ids = [item.id for item in surfaces]
    if len(ids) != len(set(ids)):
        raise AuditError("surface ids must be unique")
    if canonical_id not in ids:
        raise AuditError(f"canonical surface {canonical_id!r} is not configured")
    canonical = next(item for item in surfaces if item.id == canonical_id)
    drift = [compare_surface(canonical, item) for item in surfaces if item.id != canonical_id]
    invalid = any(
        item.errors
        or item.duplicate_identities
        or item.exact_duplicates
        or item.variable_errors
        for item in surfaces
    )
    has_drift = any(item.has_drift for item in drift)
    return AuditReport(
        canonical=canonical_id,
        surfaces=surfaces,
        drift=drift,
        invalid=invalid,
        has_drift=has_drift,
    )


def surface_to_dict(surface: SurfaceResult) -> dict[str, Any]:
    return {
        "id": surface.id,
        "path": surface.path,
        "count": surface.count,
        "duplicate_identities": surface.duplicate_identities,
        "exact_duplicates": surface.exact_duplicates,
        "variable_errors": surface.variable_errors,
        "errors": surface.errors,
        "records": [
            {
                "index": record.index,
                "identity": record.identity,
                "title": record.title,
                "tags": list(record.tags),
                "variables": [asdict(item) for item in record.variables],
                "content_hash": record.content_hash,
            }
            for record in surface.records
        ],
    }


def render_text(report: AuditReport, detail_limit: int = 20) -> str:
    lines = [f"Prompt corpus audit — canonical: {report.canonical}", ""]
    lines.append("Surfaces:")
    for surface in report.surfaces:
        status = "INVALID" if (
            surface.errors
            or surface.duplicate_identities
            or surface.exact_duplicates
            or surface.variable_errors
        ) else "OK"
        lines.append(f"- {surface.id}: {surface.count} prompts [{status}] — {surface.path}")
        for error in surface.errors:
            lines.append(f"  - error: {error}")
        if surface.duplicate_identities:
            lines.append(f"  - duplicate identities: {len(surface.duplicate_identities)}")
        if surface.exact_duplicates:
            lines.append(f"  - exact duplicate groups: {len(surface.exact_duplicates)}")
        if surface.variable_errors:
            lines.append(f"  - variable schema errors: {len(surface.variable_errors)}")

    lines.extend(["", "Drift:"])
    for drift in report.drift:
        status = "DRIFT" if drift.has_drift else "MATCH"
        lines.append(
            f"- {drift.surface}: {status}; missing={len(drift.missing)}, "
            f"extra={len(drift.extra)}, changed={len(drift.changed)}, "
            f"order_changed={str(drift.order_changed).lower()}"
        )
        for label, items in (("missing", drift.missing), ("extra", drift.extra)):
            for identity in items[:detail_limit]:
                lines.append(f"  - {label}: {identity}")
            if len(items) > detail_limit:
                lines.append(f"  - {label}: … {len(items) - detail_limit} more")
        for item in drift.changed[:detail_limit]:
            lines.append(
                f"  - changed: {item['title']} [{', '.join(item['fields'])}]"
            )
        if len(drift.changed) > detail_limit:
            lines.append(f"  - changed: … {len(drift.changed) - detail_limit} more")

    lines.extend(
        [
            "",
            f"Result: invalid={str(report.invalid).lower()}, drift={str(report.has_drift).lower()}",
        ]
    )
    return "\n".join(lines)


def read_config(path: Path) -> Mapping[str, Any]:
    try:
        parsed = json.loads(path.read_text(encoding="utf-8-sig"))
    except FileNotFoundError as error:
        raise AuditError(f"config not found: {path}") from error
    except json.JSONDecodeError as error:
        raise AuditError(f"invalid config JSON: {error}") from error
    if not isinstance(parsed, Mapping):
        raise AuditError("config root must be an object")
    return parsed


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True, type=Path, help="JSON audit configuration")
    parser.add_argument(
        "--workspace",
        type=Path,
        default=Path.cwd(),
        help="directory containing the configured repository folders",
    )
    parser.add_argument("--json", dest="json_output", type=Path, help="write full JSON report")
    parser.add_argument("--text", dest="text_output", type=Path, help="write human-readable report")
    parser.add_argument(
        "--fail-on",
        choices=("invalid", "drift", "never"),
        default="drift",
        help="exit policy: invalid only, invalid or drift, or never fail",
    )
    parser.add_argument("--detail-limit", type=int, default=20)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = build_parser().parse_args(argv)
    try:
        report = audit(read_config(arguments.config), arguments.workspace.resolve())
    except AuditError as error:
        print(f"prompt-corpus-audit: {error}", file=sys.stderr)
        return 2

    text_report = render_text(report, max(0, arguments.detail_limit))
    print(text_report)
    if arguments.json_output:
        arguments.json_output.parent.mkdir(parents=True, exist_ok=True)
        arguments.json_output.write_text(
            json.dumps(report.to_json_dict(), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    if arguments.text_output:
        arguments.text_output.parent.mkdir(parents=True, exist_ok=True)
        arguments.text_output.write_text(text_report + "\n", encoding="utf-8")

    if arguments.fail_on == "never":
        return 0
    if report.invalid:
        return 1
    if arguments.fail_on == "drift" and report.has_drift:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
