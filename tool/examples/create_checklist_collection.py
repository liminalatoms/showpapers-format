#!/usr/bin/env python3
"""Build a synthetic current collection with native checklist markers and rich formatting.

Run from the extracted ShowPapers reference tool directory:
    python3 examples/create_checklist_collection.py --output checklist-example.showpapers

Adapt the complete saved record for the person's task. Existing collections must be edited
with edit/create so their record IDs, source bytes and unrelated formatting are preserved.
"""
from __future__ import annotations

import json
from pathlib import Path
import struct
import sys
import tempfile
import uuid
import zlib

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import showpapers_api as api
import showpapers_format as fmt


def utf16_length(text):
    return len(text.encode("utf-16-le")) // 2


def text_range(text, label):
    """Use this for an unambiguous label; repeated labels need the intended occurrence."""
    start = text.index(label)
    return {"start": utf16_length(text[:start]), "end": utf16_length(text[:start + len(label)])}


def checklist_line(label, *, checked=False):
    """Author one native task. Do not use this to normalize an existing saved record."""
    if not isinstance(checked, bool) or not isinstance(label, str) or "\n" in label or "\r" in label:
        raise ValueError("A checklist line needs one label and a boolean checked state.")
    return ("☑ " if checked else "☐ ") + label


def create_example(output):
    paper_id, note_id, collection_id = (str(uuid.uuid4()) for _ in range(3))
    text = "\n".join([
        "Packing 🌍", checklist_line("Pack water"),
        checklist_line("Check tickets", checked=True), "Open example paper", "• Keep originals safe", ""])
    record = {
        "recordVersion": 1, "template": "Custom", "iconId": "paper", "createdAt": "1790900000000",
        "updatedAt": "1790900000000", "automaticTitle": False, "items": [],
        "document": {"noteVersion": 1, "text": text,
                     "marks": [{**text_range(text, "Packing 🌍"), "style": 1},
                               {**text_range(text, "Pack water"), "style": 2}],
                     "paperIds": [paper_id],
                     "paperLinks": [{**text_range(text, "Open example paper"), "paperId": paper_id}]}}

    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xffffffff)

    png = (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)) +
           chunk(b"IDAT", zlib.compress(b"\0\0\x80\xff")) + chunk(b"IEND", b""))
    with tempfile.TemporaryDirectory(prefix="showpapers-checklist-") as directory:
        base = Path(directory)
        (base / "example.png").write_bytes(png)
        (base / "note.json").write_bytes(fmt.json_bytes(record))
        spec = {"formatVersion": 4, "collection": {"id": collection_id, "revision": 1, "title": "Checklist Example"},
                "originals": [{"id": paper_id, "title": "Example Paper", "source": "example.png"}],
                "notes": [{"id": note_id, "title": "Packing list", "source": "note.json"}]}
        (base / "build.json").write_bytes(fmt.json_bytes(spec))
        result = api.create(base / "build.json", output)
    api.validate(output)
    actual = api.inspect(output, include_content=True)["noteRecords"][note_id]
    if actual != record:
        raise RuntimeError("The saved checklist did not preserve its rich record.")
    return result


def main():
    parser = fmt.Parser(description=__doc__.splitlines()[0])
    parser.add_argument("--output", required=True, help="A new .showpapers filename in an existing directory.")
    try:
        print(json.dumps(create_example(parser.parse_args().output), sort_keys=True))
        return 0
    except fmt.FormatError as error:
        print(json.dumps({"ok": False, "error": {"code": error.code, "message": str(error)}}))
        return 2


if __name__ == "__main__":
    sys.exit(main())
