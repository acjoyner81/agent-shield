"""The Help Center artifact must carry the content the portal renders (Spec 0016).

The portal reads `documents[].sections[].blocks` to paint each Help Center
section, and the gateway's retrieval reads the flat `sections[]` by `body`. The
builder once emitted the two in different shapes: `documents[].sections` kept
`body` as a raw list of lines and carried no `blocks` at all, so the portal
rendered every section as a bare heading with no body while the assistant kept
answering from the flat list. The nav links and heading anchors still resolved,
so no link test caught it.

This builds the artifact from the real source Markdown and asserts the shape the
portal consumes, so an empty Help Center fails here rather than shipping.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
BUILD_SCRIPT = REPO_ROOT / "docs" / "help-center" / "build.py"
ARTIFACT = REPO_ROOT / "gateway" / "help_center_content.json"
PORTAL_ARTIFACT = REPO_ROOT / "portal-frontend" / "public" / "help-center-content.json"


@pytest.fixture(scope="module")
def built():
    """Run the builder and hand back the regenerated artifact."""
    result = subprocess.run(
        [sys.executable, str(BUILD_SCRIPT)],
        cwd=REPO_ROOT, capture_output=True, text=True,
    )
    assert result.returncode == 0, f"build failed:\n{result.stdout}\n{result.stderr}"
    return json.loads(ARTIFACT.read_text(encoding="utf-8"))


def test_every_rendered_section_carries_blocks(built):
    """A section with no blocks is a heading the portal cannot fill in."""
    documents = built["documents"]
    assert documents, "the Help Center has no documents to render"
    for document in documents:
        for section in document["sections"]:
            assert section.get("blocks"), (
                f"{document['document_id']}/{section['section_id']} has no blocks; "
                "the portal would render an empty section"
            )


def test_rendered_section_body_is_a_non_empty_string(built):
    """The portal's `HelpSection.body` is a string; a raw line list renders nowhere."""
    for document in built["documents"]:
        for section in document["sections"]:
            body = section.get("body")
            assert isinstance(body, str), f"{section['section_id']} body is not a string: {body!r}"
            assert body.strip(), f"{section['section_id']} body is empty"


def test_rendered_and_flat_sections_share_one_shape(built):
    """The two views of a section must not drift, or one of them goes stale.

    Keyed on `source_id`, not `section_id`: the flat list also holds one entry
    per public API operation, and those deliberately reuse a document section's
    `section_id` (an operation is retrievable as `operation:...` but cites the
    human section it belongs to).
    """
    flat = {section["source_id"]: section for section in built["sections"]}
    for document in built["documents"]:
        for section in document["sections"]:
            twin = flat[section["source_id"]]
            assert section["blocks"] == twin["blocks"]
            assert section["body"] == twin["body"]


def test_in_content_links_target_known_routes(built):
    """A block link to an unrouted URL is a dead click in the Help Center."""
    known = {"/docs", "/dashboard", "/logs", "/keys", "/models", "/assistant"}
    for document in built["documents"]:
        for section in document["sections"]:
            for block in section["blocks"]:
                for part in block.get("parts") or []:
                    url = part.get("url")
                    if url is None:
                        continue
                    assert url.split("#", 1)[0] in known, f"unroutable Help Center link: {url}"


def test_portal_artifact_matches_the_gateway_artifact(built):
    """The portal serves its own copy; a stale copy ships old content."""
    portal = json.loads(PORTAL_ARTIFACT.read_text(encoding="utf-8"))
    assert portal == built, "portal-frontend/public/help-center-content.json is stale; rerun build.py"
