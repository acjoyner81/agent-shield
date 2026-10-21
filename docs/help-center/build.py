"""Build the public Help Center and assistant retrieval artifacts from Markdown."""
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT.parent.parent))
MANIFEST = json.loads((ROOT / "manifest.json").read_text(encoding="utf-8"))
SECTION_RE = re.compile(r"^##\s+(.+?)\s+\{#([a-z0-9]+(?:-[a-z0-9]+)*)\}\s*$")
LINK_RE = re.compile(r"\[[^\]]+\]\(([^)]+)\)")


def parse_document(document):
    lines = (ROOT / document["path"]).read_text(encoding="utf-8").splitlines()
    sections = []
    current = {"section_id": "overview", "heading": document["title"], "body": []}
    for line in lines:
        match = SECTION_RE.match(line)
        if match:
            if current["body"] or current["section_id"] == "overview":
                sections.append(current)
            current = {"section_id": match.group(2), "heading": match.group(1), "body": []}
        elif line.startswith("# "):
            continue
        else:
            current["body"].append(line)
    sections.append(current)
    return sections


def blocks_for(section):
    blocks, paragraph, code, listing = [], [], [], []
    fenced = False
    def flush_paragraph():
        if paragraph:
            text = " ".join(paragraph)
            parts, cursor = [], 0
            for match in re.finditer(r"\[([^\]]+)\]\((/[^)]+)\)", text):
                if match.start() > cursor:
                    parts.append({"text": text[cursor:match.start()]})
                parts.append({"text": match.group(1), "url": match.group(2)})
                cursor = match.end()
            if cursor < len(text):
                parts.append({"text": text[cursor:]})
            blocks.append({"type": "paragraph", "text": text, "parts": parts or [{"text": text}]})
            paragraph.clear()
    def flush_list():
        if listing:
            blocks.append({"type": "list", "items": list(listing)})
            listing.clear()
    for line in section["body"]:
        if line.startswith("```"):
            if fenced:
                blocks.append({"type": "code", "text": "\n".join(code)})
                code.clear()
            else:
                flush_paragraph(); flush_list()
            fenced = not fenced
            continue
        if fenced:
            code.append(line)
        elif line.startswith("- "):
            flush_paragraph(); listing.append(line[2:])
        elif line.strip():
            flush_list(); paragraph.append(line.strip())
        else:
            flush_paragraph(); flush_list()
    flush_paragraph(); flush_list()
    return blocks


def main():
    documents, sections = [], []
    ids = set()
    known_routes = {"/docs", "/dashboard", "/logs", "/keys", "/models", "/assistant"}
    for doc in MANIFEST["documents"]:
        content = parse_document(doc)
        documents.append({"document_id": doc["document_id"], "title": doc["title"], "sections": content})
        for section in content:
            sid = section["section_id"]
            if sid in ids:
                raise SystemExit(f"Duplicate Help Center section ID: {sid}")
            ids.add(sid)
            text = "\n".join(section["body"]).strip()
            for target in LINK_RE.findall(text):
                if not target.startswith("/") or target.startswith("//") or target.split("#", 1)[0] not in known_routes:
                    raise SystemExit(f"Unsupported Help Center link: {target}")
            sections.append({
                "source_id": f"section:{sid}", "section_id": sid,
                "document_id": doc["document_id"], "title": doc["title"],
                "heading": section["heading"], "summary": "", "body": text,
                "blocks": blocks_for(section),
            })
    for suggestion in MANIFEST["suggestions"]:
        if suggestion["section_id"] not in ids:
            raise SystemExit(f"Unknown suggestion section: {suggestion['section_id']}")
    if MANIFEST.get("abstention_section_id") not in ids:
        raise SystemExit("Unknown abstention section ID")
    # Validate operation IDs against the exact public schema used by `/openapi.json`.
    from gateway.main import custom_openapi
    schema = custom_openapi()
    operations = []
    for path, path_item in schema.get("paths", {}).items():
        for method, operation in path_item.items():
            if method.lower() not in {"get", "post", "put", "patch", "delete", "options", "head", "trace"}:
                continue
            operation_id = operation.get("operationId")
            if not operation_id:
                raise SystemExit(f"Public API operation has no operationId: {method.upper()} {path}")
            section_id = MANIFEST["operation_sections"].get(operation_id)
            if section_id not in ids:
                raise SystemExit(f"Unmapped or unknown public operation: {operation_id}")
            summary = operation.get("summary", operation_id)
            description = operation.get("description", "")
            contract = json.dumps({key: operation.get(key) for key in ("parameters", "requestBody", "responses", "security") if operation.get(key) is not None}, ensure_ascii=False, sort_keys=True)
            operations.append({
                "source_id": f"operation:{operation_id}", "section_id": section_id,
                "document_id": "api", "title": "AgentShield API", "heading": summary,
                "summary": description, "body": f"{method.upper()} {path} {summary} {description} {contract}",
                "blocks": [], "method": method.upper(), "path": path,
                "contract": contract,
            })
    unmapped = set(MANIFEST["operation_sections"]) - {item["source_id"].removeprefix("operation:") for item in operations}
    if unmapped:
        raise SystemExit(f"Unknown public operation IDs in manifest: {', '.join(sorted(unmapped))}")
    sections.extend(operations)
    artifact = {"version": MANIFEST["version"], "documents": documents,
                "sections": sections, "suggestions": MANIFEST["suggestions"],
                "abstention_section_id": MANIFEST["abstention_section_id"],
                "operation_sections": MANIFEST["operation_sections"]}
    serialized = json.dumps(artifact, ensure_ascii=False, indent=2) + "\n"
    destinations = [ROOT.parent.parent / "portal-frontend/public/help-center-content.json",
                    ROOT.parent.parent / "gateway/help_center_content.json"]
    for destination in destinations:
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(serialized, encoding="utf-8")


if __name__ == "__main__":
    main()
