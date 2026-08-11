# Cross-repository prompt corpus audit

A product can have five working clients and still ship five different prompt libraries. Per-repository tests prove that each bundle is valid; they do not prove that Chrome, VS Code, WeChat, web, and iOS contain the same records or interpret variables the same way.

`prompt_corpus_audit.py` closes that gap without executing source files or adding package dependencies.

## Workspace layout

The audit reads normal local checkouts:

```text
~/portfolio/repos/
  autoapp-prompt-vault/
  promptvault-chrome/
  promptvault-vscode/
  promptvault-wechat-miniprogram/
```

Run:

```bash
python3 autoapp-toolkit/scripts/prompt_corpus_audit.py \
  --config autoapp-toolkit/examples/promptvault-corpus.json \
  --workspace ~/portfolio/repos \
  --json reports/prompt-corpus.json \
  --text reports/prompt-corpus.txt
```

The default exit policy is `--fail-on drift`: invalid data or any difference from the canonical surface exits 1. During an initial migration, use `--fail-on never` to generate a baseline report without hiding the drift.

## Configuration

```json
{
  "canonical": "ios",
  "identity_fields": ["title"],
  "surfaces": [
    {
      "id": "ios",
      "repo": "autoapp-prompt-vault",
      "path": "PromptVault/Resources/starter_prompts.json",
      "format": "json-array",
      "fields": {
        "title": ["title_en", "title"],
        "body": ["body_en", "body"],
        "tags": ["tags_en", "tags"]
      }
    }
  ]
}
```

Field arrays are fallback chains. In the example, English content is selected when present and the base field is used otherwise.

Supported packaged formats:

- `json-array`
- `js-const-array` — a strict JSON array following `const PROMPTS =`
- `commonjs-array` — a strict JSON array following `module.exports =`

JavaScript is parsed as text. The tool never imports or evaluates a client bundle.

## What fails

### Invalid surface

- file missing or invalid UTF-8 / JSON
- prompt is not an object
- mapped title or body is empty
- mapped tags are not an array
- duplicate identity within one surface
- exact duplicate record within one surface
- conflicting typed-variable declarations such as:

```text
{{count:int=5}} ... {{count:string=five}}
```

### Cross-surface drift

- count difference
- canonical prompt missing from another surface
- extra prompt not present in canonical
- same identity but different body, tags, or variable schema
- common prompts appear in a different order

The JSON report includes normalized identities, content hashes, variable schemas, and bounded human-readable differences. It deliberately excludes full prompt bodies so CI artifacts do not become an accidental content redistribution channel.

## Identity choice

`["title"]` is appropriate only when canonical titles are unique and intentionally stable. A stronger identity can be configured:

```json
"identity_fields": ["title", "body"]
```

That detects duplicate titles but treats every body edit as a remove/add pair instead of a changed record. For long-lived products, the best design is an explicit immutable content ID in the canonical source, mapped to a logical `id` field by a future schema revision.

## Safe sync sequence

1. Choose one canonical corpus and document why.
2. Run the audit with `--fail-on never` and save the report.
3. Resolve duplicate identities and variable-schema conflicts in canonical first.
4. Generate downstream bundles mechanically; do not hand-copy 100+ prompts.
5. Run each client’s local tests.
6. Run this audit with `--fail-on drift`.
7. Commit generated bundles and the report summary together.
8. Keep platform-specific descriptions, IDs, or translations in explicit sidecar fields rather than silently editing shared title/body content.

## CI use

A blocking consumer job should run after all relevant checkouts are available:

```bash
python3 toolkit/scripts/prompt_corpus_audit.py \
  --config orchestrator/prompt-corpus.json \
  --workspace repos \
  --fail-on drift
```

A scheduled observability job may use `--fail-on never`, publish JSON/text artifacts, and raise an issue when `has_drift` changes from false to true. It must not report green synchronization merely because the command itself exited zero.
