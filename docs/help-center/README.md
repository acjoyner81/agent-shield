# Help Center content

The curated Markdown pages and `manifest.json` are the source for both the public portal page and the gateway's deterministic retrieval corpus.

After changing Markdown, suggestions, section IDs, or the public gateway API schema, regenerate both checked-in artifacts from the repository root:

```bash
.venv/bin/python docs/help-center/build.py
```

The build validates stable section IDs, local portal links, suggestion targets, and the complete mapping between public OpenAPI operation IDs and Help Center sections. Update `operation_sections` when a public operation is added, removed, or renamed. The generated files are `portal-frontend/public/help-center-content.json` and `gateway/help_center_content.json`.
