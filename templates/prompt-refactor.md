# Prompt Pattern: Refactor

> Use case: code refactors with safety net
> Tested: Python / TypeScript / Swift / Markdown

## Pattern
```
Role: expert {{language}} engineer.

Task: {{describe refactor goal}}

Constraints:
- No breaking changes to public API
- Preserve all test coverage
- Document any behavior change

Steps:
1. Read {{file_path}}
2. List all callers / dependents
3. Plan the minimal change
4. Show the diff
5. Run verification: {{test_command}}

Safety check: if any step fails, stop and report.
```

## When to use
- Migration: Python 2->3, JS->TS, etc.
- Style: add typing, lint fixes
- Architecture: extract class, split module

---
*Part of the free B2B AI templates library: jiejuefuyou.github.io/free-templates.html*
