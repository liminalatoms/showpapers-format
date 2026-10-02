#!/usr/bin/env python3
"""Reference reader/writer for readable ShowPapers archives. Standard library only."""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import stat
import struct
import sys
import uuid
import zipfile
import zlib
from contextlib import contextmanager

VERSION = 1
# The published specification (docs/SHOWPAPERS_SPECIFICATION.md) that this reader/writer implements.
SPECIFICATION_VERSION = "1.0.0"
SUPPORTED_FORMAT_VERSIONS = (1, 2, 3)
# Archive profile 4 (full fidelity) lives in showpapers_fidelity.py. The operation, undo, conflict and workspace
# machinery keeps SUPPORTED_FORMAT_VERSIONS; readers that accept profile 4 say so with full_fidelity=True.
FULL_FIDELITY_FORMAT_VERSION = 4
READABLE_FORMAT_VERSIONS = SUPPORTED_FORMAT_VERSIONS + (FULL_FIDELITY_FORMAT_VERSION,)
# Current collections have no product count ceiling; directory bytes bound ZIP metadata allocations.
FULL_FIDELITY_MAX_ENTRIES = None
SUPPORTED_OPERATION_PROTOCOL_VERSIONS = (1, 2, 3, 4)
MAX_REVISION = 2**53 - 1
MAX_OPERATIONS = 32
MAX_ORIGINALS = 100
MAX_NOTES = 100
MAX_FOLDERS = 100
MAX_ORIGINAL_BYTES = 20 * 1024 * 1024
MAX_NOTE_BYTES = 128 * 1024
MAX_RICH_NOTE_BYTES = 512 * 1024
MAX_NOTE_UTF16 = 32768
MAX_NOTE_MARKS = 2048
MAX_NOTE_PAPER_LINKS = 640
MAX_NOTE_RECORD_BYTES = 512 * 1024
MAX_NOTE_RECORD_ITEMS = 80
MAX_NOTE_RECORD_LABEL = 160
MAX_NOTE_ITEM_PAPERS = 8
MAX_TIMESTAMP = 2**63 - 1
NOTE_TEMPLATES = ("Domestic", "International", "Custom")
NOTE_ICONS = ("checklist", "domestic", "international", "work", "study", "family", "appointment",
              "folder", "paper", "passport", "identity", "receipt", "shield")
MAX_MANIFEST_BYTES = 1024 * 1024
MAX_PAYLOAD_BYTES = 100 * 1024 * 1024
MAX_ENTRIES = 1 + MAX_ORIGINALS + MAX_NOTES
MAX_ZIP_DIRECTORY_BYTES = 2 * 1024 * 1024
MAX_ARCHIVE_BYTES = MAX_PAYLOAD_BYTES + MAX_MANIFEST_BYTES + MAX_ZIP_DIRECTORY_BYTES
MAX_EXPANSION_RATIO = 100
MAX_TITLE = 200
MAX_FOLDER_NAME = 100
MAX_IMAGE_CHUNKS = 10000
MAX_IMAGE_PIXELS = 100_000_000
MAX_SCOPE_IDS = 100 + MAX_OPERATIONS
MAX_SOURCES = MAX_OPERATIONS
MAX_SOURCE_PATH = 4096
# Closed manifest field sets for every supported archive profile. A new record category
# (details, People, review history, ...) needs a new formatVersion; it is never an extra key here.
MANIFEST_FIELDS = ("format", "formatVersion", "protection", "collection", "originals", "notes", "folders")
COLLECTION_FIELDS = ("id", "revision", "title")
ORIGINAL_FIELDS = ("id", "title", "path", "size", "sha256", "mediaType")
NOTE_FIELDS = ("id", "title", "path", "size", "sha256", "encoding", "paperIds")
FOLDER_FIELDS = ("id", "name", "paperIds", "noteIds")
NOTE_PAYLOADS = {
    1: {"representation": "plain-utf8", "extension": "txt", "minimumBytes": 0, "maximumBytes": MAX_NOTE_BYTES,
        "titleScalars": MAX_TITLE, "titleEdgeWhitespace": False},
    2: {"representation": "rich-document", "extension": "json", "minimumBytes": 1, "maximumBytes": MAX_RICH_NOTE_BYTES,
        "titleScalars": MAX_TITLE, "titleEdgeWhitespace": False},
    3: {"representation": "native-note-record", "extension": "json", "minimumBytes": 1, "maximumBytes": MAX_NOTE_RECORD_BYTES,
        "titleScalars": MAX_NOTE_RECORD_LABEL, "titleEdgeWhitespace": True},
}
MEDIA = {"application/pdf": "pdf", "image/png": "png", "image/jpeg": "jpg", "image/webp": "webp"}
HEX = re.compile(r"[a-f0-9]{64}\Z")
_FIDELITY_MODULE = None


def local_resource(*relatives):
    """Return an available bundled resource, relative to this tool, without exposing host paths."""
    base = Path(__file__).parent
    return next((relative for relative in relatives if (base / relative).is_file()), None)


def documentation_resource(name):
    """Repository, public toolkit and workspace skill carry documentation in different places."""
    return local_resource(f"docs/{name}", f"../docs/{name}", f"../../references/{name}")


def current_collection_guidance():
    """Add current authoring guidance without changing the compatibility CLI contracts."""
    return {
        "specificationVersion": SPECIFICATION_VERSION, "formatVersion": FULL_FIDELITY_FORMAT_VERSION,
        "description": "Use formatVersion 4 for new collections. Archive identifiers are independent of the specification version.",
        "documentation": documentation_resource("SHOWPAPERS_FULL_FIDELITY.md"),
        "agentGuide": documentation_resource("SHOWPAPERS_AGENT_GUIDE.md"),
        "schemaCommands": {kind: ["schema", "--kind", kind, "--version", str(FULL_FIDELITY_FORMAT_VERSION)]
                           for kind in ("manifest", "build-spec")},
        "editWith": ["edit + create", "apply --changes"],
        "discoverySections": {"commands": "fullFidelity.commandDescriptors", "schemas": "fullFidelity.schemas",
                              "limits": "fullFidelity.limits", "authoring": "fullFidelity.noteAuthoringGuidance"},
        "example": local_resource("examples/create_checklist_collection.py"),
        "compatibility": {"archiveFormatVersions": list(SUPPORTED_FORMAT_VERSIONS),
                          "bareSchemaCommandVersion": VERSION,
                          "legacyMetadata": ["formatVersion", "supportedFormatVersions", "limits", "manifest", "unsupported", "android"],
                          "commandScope": "Top-level preview/apply --operations descriptors describe the older operation contracts. Current collections use --changes via fullFidelity.commandDescriptors.",
                          "description": "Older schema defaults and metadata remain for compatibility. Use currentCollection and fullFidelity for current authoring; do not remove bundled compatibility dependencies."},
    }


class _ThisModule:
    """Attribute access to this module's globals when it is not registered in sys.modules."""
    def __getattr__(self, name):
        try:
            return globals()[name]
        except KeyError:
            raise AttributeError(name) from None


def _fidelity():
    """The profile 4 module, bound to this exact reference module even when it is loaded under another name."""
    global _FIDELITY_MODULE
    if _FIDELITY_MODULE is None:
        # A caller may load this file by path without registering it (the protected tool does); bind to its globals.
        this = sys.modules.get(__name__)
        if this is None or getattr(this, "__dict__", None) is not globals():
            this = _ThisModule()
        existing = sys.modules.get("showpapers_fidelity")
        if existing is not None and getattr(existing, "fmt", None) is this:
            _FIDELITY_MODULE = existing
            return existing
        name = "showpapers_fidelity" if __name__ == "showpapers_format" else f"_{__name__}_showpapers_fidelity"
        spec = importlib.util.spec_from_file_location(name, Path(__file__).with_name("showpapers_fidelity.py"))
        module = importlib.util.module_from_spec(spec)
        saved = sys.modules.get("showpapers_format")
        sys.modules["showpapers_format"] = this
        try:
            sys.modules[name] = module
            spec.loader.exec_module(module)
        except BaseException:
            sys.modules.pop(name, None)
            raise
        finally:
            if saved is None and __name__ != "showpapers_format":
                sys.modules.pop("showpapers_format", None)
            elif saved is not None:
                sys.modules["showpapers_format"] = saved
        _FIDELITY_MODULE = module
    return _FIDELITY_MODULE


class FormatError(Exception):
    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)


def need(condition, code="manifest", message="The manifest is invalid."):
    if not condition:
        raise FormatError(code, message)


def _object_pairs(pairs):
    result = {}
    for key, value in pairs:
        need(key not in result, "duplicate", "Duplicate JSON properties are not allowed.")
        result[key] = value
    return result


def parse_json(data: bytes):
    need(len(data) <= MAX_MANIFEST_BYTES, "limit", "The JSON document is too large.")
    try:
        value = json.loads(data.decode("utf-8"), object_pairs_hook=_object_pairs,
                           parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
    except FormatError:
        raise
    except (ValueError, UnicodeError, RecursionError):
        raise FormatError("json", "Expected bounded, valid UTF-8 JSON.") from None
    return value


def json_bytes(value):
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n").encode("utf-8")


def keys(value, expected):
    need(isinstance(value, dict) and set(value) == set(expected))


def bounded_text(value, maximum=MAX_TITLE):
    need(isinstance(value, str) and 0 < len(value) <= maximum and value.strip() == value)
    need(not any(ord(c) < 32 or 127 <= ord(c) <= 159 or 0xD800 <= ord(c) <= 0xDFFF for c in value))


def note_record_label(value):
    """Native record labels retain authored edge whitespace; they are not trimmed titles."""
    need(isinstance(value, str) and 0 < len(value) <= MAX_NOTE_RECORD_LABEL and bool(value.strip()),
         "note", "Expected a bounded, nonblank Scratch record label.")
    need(not any(ord(c) < 32 or 127 <= ord(c) <= 159 or 0xD800 <= ord(c) <= 0xDFFF for c in value),
         "note", "Unsupported character in a Scratch record label.")


def identifier(value):
    try:
        need(isinstance(value, str) and str(uuid.UUID(value)) == value)
    except (ValueError, AttributeError):
        raise FormatError("manifest", "Expected a canonical UUID.") from None


def integer(value, low, high):
    need(type(value) is int and low <= value <= high)


def references(values, available):
    # Lists are already bounded by the 1 MiB JSON limit; distinct resolved IDs cannot outnumber
    # their targets, so a dangling ID reports "reference" rather than a generic shape error.
    need(isinstance(values, list) and all(isinstance(v, str) for v in values))
    need(len(set(values)) == len(values), "duplicate", "Repeated references are not allowed.")
    need(set(values) <= available, "reference", "A reference points outside this collection.")


def validate_manifest(manifest):
    need(isinstance(manifest, dict))
    need(type(manifest.get("formatVersion")) is int and manifest["formatVersion"] in SUPPORTED_FORMAT_VERSIONS,
         "version", "This format version is not supported.")
    version = manifest["formatVersion"]
    keys(manifest, MANIFEST_FIELDS)
    need(manifest["format"] == "showpapers" and manifest["protection"] == "readable")
    keys(manifest["collection"], COLLECTION_FIELDS)
    identifier(manifest["collection"]["id"])
    integer(manifest["collection"]["revision"], 1, MAX_REVISION)
    bounded_text(manifest["collection"]["title"])
    all_ids = {manifest["collection"]["id"]}
    paths = set()
    total = 0
    for name, maximum in (("originals", MAX_ORIGINALS), ("notes", MAX_NOTES), ("folders", MAX_FOLDERS)):
        rows = manifest[name]
        need(isinstance(rows, list) and len(rows) <= maximum)
        for row in rows:
            need(isinstance(row, dict))
            identifier(row.get("id"))
            need(row["id"] not in all_ids, "duplicate", "Record IDs must be unique across the collection.")
            all_ids.add(row["id"])
            if name == "folders":
                keys(row, FOLDER_FIELDS)
                bounded_text(row["name"], MAX_FOLDER_NAME)
                continue
            keys(row, ORIGINAL_FIELDS if name == "originals" else NOTE_FIELDS)
            if name == "notes" and version == 3:
                note_record_label(row["title"])
            else:
                bounded_text(row["title"])
            need(isinstance(row["sha256"], str) and HEX.fullmatch(row["sha256"]) is not None)
            if name == "originals":
                need(isinstance(row["mediaType"], str) and row["mediaType"] in MEDIA)
                path = f"originals/{row['id']}.{MEDIA[row['mediaType']]}"
                integer(row["size"], 1, MAX_ORIGINAL_BYTES)
            else:
                need(row["encoding"] == "utf-8")
                payload = NOTE_PAYLOADS[version]
                path = f"notes/{row['id']}.{payload['extension']}"
                integer(row["size"], payload["minimumBytes"], payload["maximumBytes"])
            need(row["path"] == path, "path", "Payload paths must use their canonical record IDs.")
            need(path not in paths, "duplicate", "Payload paths must be unique.")
            paths.add(path)
            total += row["size"]
    need(total <= MAX_PAYLOAD_BYTES, "limit", "The collection exceeds the payload limit.")
    papers = {row["id"] for row in manifest["originals"]}
    notes = {row["id"] for row in manifest["notes"]}
    for note in manifest["notes"]:
        references(note["paperIds"], papers)
    for folder in manifest["folders"]:
        references(folder["paperIds"], papers)
        references(folder["noteIds"], notes)
    return manifest


def media_type(data):
    """Conservative signature/container checks, not a PDF or raster decoder."""
    if re.match(rb"%PDF-(?:1\.[0-7]|2\.0)(?:\r|\n|\s)", data) and b"%%EOF" in data[-1024:]:
        return "application/pdf"
    if len(data) >= 45 and data[:8] == b"\x89PNG\r\n\x1a\n":
        offset, chunks, ended = 8, [], False
        while offset + 12 <= len(data):
            need(len(chunks) < MAX_IMAGE_CHUNKS, "limit", "The PNG contains too many chunks.")
            length = struct.unpack_from(">I", data, offset)[0]
            need(length <= len(data) - offset - 12, "media", "Invalid PNG structure.")
            kind = data[offset + 4:offset + 8]
            body = data[offset + 8:offset + 8 + length]
            crc = struct.unpack_from(">I", data, offset + 8 + length)[0]
            need(zlib.crc32(kind + body) & 0xFFFFFFFF == crc, "media", "Invalid PNG checksum.")
            if not chunks:
                need(kind == b"IHDR" and length == 13, "media", "Invalid PNG header.")
                width, height = struct.unpack_from(">II", body)
                need(width > 0 and height > 0 and width * height <= MAX_IMAGE_PIXELS, "media", "Image dimensions exceed the supported bounds.")
            else:
                need(kind != b"IHDR", "media", "Duplicate PNG header.")
            chunks.append(kind)
            offset += length + 12
            if kind == b"IEND":
                need(length == 0 and offset == len(data), "media", "Invalid PNG ending.")
                ended = True
                break
        need(ended and b"IDAT" in chunks, "media", "PNG image data is missing.")
        return "image/png"
    if len(data) >= 4 and data[:3] == b"\xff\xd8\xff" and data[-2:] == b"\xff\xd9":
        return "image/jpeg"
    if len(data) >= 20 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        need(struct.unpack_from("<I", data, 4)[0] + 8 == len(data), "media", "Invalid WebP size.")
        offset, image, chunks = 12, False, 0
        while offset + 8 <= len(data):
            chunks += 1
            need(chunks <= MAX_IMAGE_CHUNKS, "limit", "The WebP contains too many chunks.")
            kind, size = data[offset:offset + 4], struct.unpack_from("<I", data, offset + 4)[0]
            need(size <= len(data) - offset - 8, "media", "Invalid WebP chunk.")
            image |= kind in (b"VP8 ", b"VP8L") and size > 0
            offset += 8 + size + size % 2
        need(image and offset == len(data), "media", "Invalid WebP structure.")
        return "image/webp"
    raise FormatError("media", "Expected a supported PDF, PNG, JPEG or WebP original.")


def note_text(data):
    need(len(data) <= MAX_NOTE_BYTES, "limit", "The note is too large.")
    try:
        text = data.decode("utf-8")
        need(len(text.encode("utf-16-le")) // 2 <= MAX_NOTE_UTF16, "limit", "The note text is too long.")
        need(not any((ord(c) < 32 and c not in "\n\r\t") or 127 <= ord(c) <= 159 for c in text),
             "note", "Unsupported control character in a note.")
        return text
    except UnicodeError:
        raise FormatError("note", "Notes must contain valid UTF-8 text.") from None


def validate_note_document(document, available_paper_ids, reference_limit=MAX_ORIGINALS):
    """Validate modern Scratch content without changing text, marks, references or their order."""
    keys(document, {"noteVersion", "text", "marks", "paperIds", "paperLinks"})
    need(type(document["noteVersion"]) is int and document["noteVersion"] == 1,
         "version", "This Scratch document version is not supported.")
    text = document["text"]
    need(isinstance(text, str), "note", "Note text must be a UTF-8 string.")
    boundaries = {0}
    length = 0
    for char in text:
        point = ord(char)
        need(not (0xD800 <= point <= 0xDFFF) and
             not ((point < 32 and char not in "\n\t") or 127 <= point <= 159),
             "note", "Unsupported character in a Scratch document.")
        length += 2 if point > 0xFFFF else 1
        need(length <= MAX_NOTE_UTF16, "limit", "The note text is too long.")
        boundaries.add(length)
    marks = document["marks"]
    links = document["paperLinks"]
    need(isinstance(marks, list) and len(marks) <= MAX_NOTE_MARKS,
         "limit", "The note contains too many formatting marks.")
    need(isinstance(links, list) and len(links) <= MAX_NOTE_PAPER_LINKS,
         "limit", "The note contains too many Paper links.")
    need(isinstance(document["paperIds"], list) and len(document["paperIds"]) <= reference_limit,
         "limit", "The note contains too many Paper references.")
    for paper_id in document["paperIds"]:
        identifier(paper_id)
    references(document["paperIds"], set(available_paper_ids))

    def offsets(value):
        integer(value["start"], 0, length)
        integer(value["end"], 1, length)
        need(value["start"] < value["end"] and value["start"] in boundaries and value["end"] in boundaries,
             "note", "Note offsets must describe complete UTF-16 characters.")

    for mark in marks:
        keys(mark, {"start", "end", "style"})
        offsets(mark)
        integer(mark["style"], 1, 2)
    for link in links:
        keys(link, {"start", "end", "paperId"})
        offsets(link)
        identifier(link["paperId"])
        need(link["paperId"] in document["paperIds"], "reference", "A Paper link must be a declared note reference.")
    ordered = sorted(links, key=lambda link: link["start"])
    need(all(left["end"] <= right["start"] for left, right in zip(ordered, ordered[1:])),
         "note", "Paper links must not overlap.")
    return document


def note_document(data, available_paper_ids):
    need(len(data) <= MAX_RICH_NOTE_BYTES, "limit", "The Scratch document is too large.")
    return validate_note_document(parse_json(data), available_paper_ids)


def _note_document_bytes(document, available_paper_ids):
    validate_note_document(document, available_paper_ids)
    data = json_bytes(document)
    need(len(data) <= MAX_RICH_NOTE_BYTES, "limit", "The Scratch document is too large.")
    return data


def note_record_paper_ids(record):
    """First-occurrence attachment union of a validated record, retaining dormant rows too."""
    values = [paper_id for item in record["items"] for paper_id in item["paperIds"]]
    if record["document"] is not None:
        values.extend(record["document"]["paperIds"])
    return list(dict.fromkeys(values))


def validate_note_record(record, available_paper_ids, reference_limit=MAX_ORIGINALS):
    """Validate complete bounded Scratch record semantics without converting legacy rows."""
    keys(record, {"recordVersion", "template", "iconId", "createdAt", "updatedAt", "automaticTitle", "items", "document"})
    need(type(record["recordVersion"]) is int and record["recordVersion"] == 1,
         "version", "This Scratch record version is not supported.")
    need(isinstance(record["template"], str) and record["template"] in NOTE_TEMPLATES,
         "note", "Unsupported Scratch record template.")
    need(isinstance(record["iconId"], str) and record["iconId"] in NOTE_ICONS,
         "note", "Unsupported Scratch record icon.")
    times = []
    for field in ("createdAt", "updatedAt"):
        value = record[field]
        need(isinstance(value, str) and len(value) <= 19 and re.fullmatch(r"0|[1-9][0-9]*", value) is not None,
             "note", "Scratch record times must be canonical unsigned decimal strings.")
        number = int(value)
        need(number <= MAX_TIMESTAMP, "note", "A Scratch record time exceeds its supported bound.")
        times.append(number)
    need(times[1] >= times[0], "note", "The Scratch update time cannot precede its creation time.")
    need(type(record["automaticTitle"]) is bool, "note", "The automatic title flag must be a boolean.")
    items = record["items"]
    need(isinstance(items, list) and len(items) <= MAX_NOTE_RECORD_ITEMS,
         "limit", "The Scratch record contains too many legacy rows.")
    seen = set()
    available = set(available_paper_ids)
    for item in items:
        keys(item, {"id", "label", "checked", "checkable", "paperIds"})
        identifier(item["id"])
        need(item["id"] not in seen, "duplicate", "Legacy row IDs must be unique within the note.")
        seen.add(item["id"])
        note_record_label(item["label"])
        need(type(item["checked"]) is bool and type(item["checkable"]) is bool,
             "note", "Legacy row state must use boolean flags.")
        need(isinstance(item["paperIds"], list) and len(item["paperIds"]) <= MAX_NOTE_ITEM_PAPERS,
             "limit", "A legacy row contains too many Paper references.")
        for paper_id in item["paperIds"]:
            identifier(paper_id)
        references(item["paperIds"], available)
    if record["document"] is not None:
        validate_note_document(record["document"], available, MAX_NOTE_PAPER_LINKS if reference_limit is None else reference_limit)
    union = note_record_paper_ids(record)
    need(reference_limit is None or len(union) <= reference_limit, "limit", "The Scratch record contains too many Paper references.")
    references(union, available)
    return record


def note_record(data, available_paper_ids, reference_limit=MAX_ORIGINALS):
    need(len(data) <= MAX_NOTE_RECORD_BYTES, "limit", "The Scratch record is too large.")
    return validate_note_record(parse_json(data), available_paper_ids, reference_limit)


def _note_record_bytes(record, available_paper_ids):
    validate_note_record(record, available_paper_ids)
    data = json_bytes(record)
    need(len(data) <= MAX_NOTE_RECORD_BYTES, "limit", "The Scratch record is too large.")
    return data


def _archive_digest(source):
    """Hash a bounded open input, not a second path lookup that could name another snapshot."""
    need(os.fstat(source.fileno()).st_size <= MAX_ARCHIVE_BYTES, "limit", "The archive is too large.")
    source.seek(0)
    digest, total = hashlib.sha256(), 0
    while True:
        block = source.read(64 * 1024)
        if not block:
            break
        total += len(block)
        need(total <= MAX_ARCHIVE_BYTES, "limit", "The archive is too large.")
        digest.update(block)
    return digest.hexdigest()


def _source_identity(source):
    value = os.fstat(source.fileno())
    return value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns, value.st_ctime_ns


def _zip_directory_bounds(stream, maximum=MAX_ENTRIES):
    stream.seek(0, 2)
    size = stream.tell()
    need(22 <= size <= MAX_ARCHIVE_BYTES, "limit", "The archive size is outside the supported bounds.")
    stream.seek(0)
    need(stream.read(4) == b"PK\x03\x04", "archive", "Expected an ordinary ZIP archive.")
    stream.seek(max(0, size - 65557))
    tail = stream.read(65557)
    index = tail.rfind(b"PK\x05\x06")
    need(index >= 0 and index + 22 <= len(tail), "archive", "A ZIP directory is required.")
    _, disk, directory_disk, disk_entries, entries, length, offset, comment = struct.unpack_from("<4s4H2IH", tail, index)
    need(disk == directory_disk == 0 and disk_entries == entries and 1 <= entries < 65535 and
         (maximum is None or entries <= maximum),
         "limit", "Split, ZIP64 or oversized archives are not supported.")
    need(length <= MAX_ZIP_DIRECTORY_BYTES and offset + length == size - len(tail) + index and index + 22 + comment == len(tail),
         "archive", "Invalid or unsupported ZIP directory layout.")
    # Check before ZipFile constructs one object per directory row. Even deceptive EOCD counts
    # cannot allocate more metadata than the already bounded central directory can represent.
    need(entries <= length // 46, "archive", "The ZIP count exceeds its directory bytes.")
    stream.seek(0)
    return entries


def _entry_bytes(archive, entry, limit):
    need(entry.file_size <= limit, "limit", "A payload exceeds its size limit.")
    result = bytearray()
    with archive.open(entry) as source:
        while True:
            chunk = source.read(min(65536, limit + 1 - len(result)))
            if not chunk:
                break
            result.extend(chunk)
            need(len(result) <= limit, "limit", "A payload expands beyond its limit.")
    need(len(result) == entry.file_size, "archive", "The ZIP payload size is inconsistent.")
    return bytes(result)


def _validate_local_header(stream, entry):
    """Do not let central-directory metadata hide an unsupported or contradictory local header."""
    need(0 <= entry.header_offset <= MAX_ARCHIVE_BYTES - 30,
         "archive", "Invalid ZIP payload header position.")
    stream.seek(entry.header_offset)
    header = stream.read(30)
    need(len(header) == 30 and header[:4] == b"PK\x03\x04",
         "archive", "A ZIP payload header is missing.")
    _, _, flags, compression, _, _, crc, compressed, size, _, extra = struct.unpack("<4s5H3I2H", header)
    need(flags == entry.flag_bits and compression == entry.compress_type,
         "archive", "ZIP payload and directory metadata disagree.")
    need(extra == 0, "archive", "ZIP extra fields are outside this format profile.")
    # Streaming writers use a data descriptor and leave these local fields unknown (zero).
    # The reader still checks the actual expanded size/CRC and manifest digest afterward.
    descriptor = bool(flags & 8)
    need(all(local == expected or descriptor and local == 0 for local, expected in
             ((crc, entry.CRC), (compressed, entry.compress_size), (size, entry.file_size))),
         "archive", "ZIP payload and directory integrity metadata disagree.")


@contextmanager
def _regular_file(path):
    # Refuse pipes and devices without blocking on their open/read operations.
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_NONBLOCK", 0))
    try:
        need(stat.S_ISREG(os.fstat(fd).st_mode), "path", "Expected a regular file.")
        source = os.fdopen(fd, "rb")
        fd = None
        with source:
            yield source
    finally:
        if fd is not None:
            os.close(fd)


def _validate_payload(row, data, format_version=VERSION, available_paper_ids=()):
    need(len(data) == row["size"], "archive", "A payload size does not match its manifest.")
    need(hashlib.sha256(data).hexdigest() == row["sha256"], "digest", "A payload does not match its manifest digest.")
    if "mediaType" in row:
        need(media_type(data) == row["mediaType"], "media", "The original does not match its declared media type.")
        return None
    if format_version == 2:
        document = note_document(data, available_paper_ids)
        need(document["paperIds"] == row["paperIds"], "reference", "Note references must match their manifest order.")
        return document
    if format_version == 3:
        record = note_record(data, available_paper_ids)
        need(note_record_paper_ids(record) == row["paperIds"], "reference", "Scratch record references must match their manifest order.")
        return record
    return note_text(data)


def _read_archive_stream(stream, include_content=False, payloads=None, *, full_fidelity=False):
    """Validate the same open inode, including during creation.

    Profile 4 archives are read only when the caller says it understands them (full_fidelity=True); every other
    caller keeps refusing them as an unsupported version, exactly as before profile 4 existed.
    """
    try:
        count = _zip_directory_bounds(stream, FULL_FIDELITY_MAX_ENTRIES if full_fidelity else MAX_ENTRIES)
        with zipfile.ZipFile(stream) as archive:
            entries = archive.infolist()
            need(len(entries) == count, "archive", "Inconsistent ZIP directory count.")
            names = set()
            total = 0
            for entry in entries:
                name = entry.orig_filename
                need(name == entry.filename and "\\" not in name and not name.startswith("/") and
                     all(p not in ("", ".", "..") for p in name.split("/")) and ":" not in name,
                     "path", "Unsafe archive path.")
                need(name not in names, "duplicate", "Duplicate archive entries are not allowed.")
                names.add(name)
                kind = stat.S_IFMT(entry.external_attr >> 16)
                need(not entry.is_dir() and kind in (0, stat.S_IFREG) and not entry.external_attr & 0x10,
                     "path", "Only regular payload files are permitted.")
                need(not entry.flag_bits & 1 and entry.compress_type in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED),
                     "archive", "ZIP encryption and this compression method are not supported.")
                need(not entry.extra, "archive", "ZIP extra fields are outside this format profile.")
                _validate_local_header(stream, entry)
                need(entry.file_size <= max(MAX_ORIGINAL_BYTES, MAX_MANIFEST_BYTES) and
                     entry.file_size <= max(1, entry.compress_size) * MAX_EXPANSION_RATIO, "limit", "Unsafe archive expansion ratio.")
                total += entry.file_size
            need(total <= MAX_PAYLOAD_BYTES + MAX_MANIFEST_BYTES, "limit", "The archive expands beyond the collection limit.")
            need("manifest.json" in names, "manifest", "The manifest is missing.")
            raw = parse_json(_entry_bytes(archive, archive.getinfo("manifest.json"), MAX_MANIFEST_BYTES))
            if isinstance(raw, dict) and type(raw.get("formatVersion")) is int and raw["formatVersion"] == FULL_FIDELITY_FORMAT_VERSION:
                need(full_fidelity, "version", "This operation supports archive profiles 1-3; profile 4 uses the full-fidelity commands.")
                return _fidelity().read_payloads(archive, raw, names, include_content, payloads)
            need(count <= MAX_ENTRIES, "limit", "Split, ZIP64 or oversized archives are not supported.")
            manifest = validate_manifest(raw)
            rows = manifest["originals"] + manifest["notes"]
            need(names == {"manifest.json"} | {r["path"] for r in rows}, "archive", "The archive contains missing or undeclared files.")
            notes = {}
            available_papers = {row["id"] for row in manifest["originals"]}
            for row in rows:
                entry = archive.getinfo(row["path"])
                need(entry.file_size == row["size"], "archive", "A payload size does not match its manifest.")
                data = _entry_bytes(archive, entry, row["size"])
                text = _validate_payload(row, data, manifest["formatVersion"], available_papers)
                if include_content and text is not None:
                    notes[row["id"]] = text
                if payloads is not None:
                    payloads[row["path"]] = data
            return manifest, notes
    except FormatError:
        raise
    except (OSError, ValueError, EOFError, zipfile.BadZipFile, RuntimeError, NotImplementedError, RecursionError, zlib.error):
        raise FormatError("archive", "The archive could not be read safely.") from None


def read_archive(path, include_content=False, *, full_fidelity=False):
    """Validate every payload without extracting files. Content is opt-in and memory-only."""
    try:
        with _regular_file(path) as stream:
            return _read_archive_stream(stream, include_content, full_fidelity=full_fidelity)
    except FormatError:
        raise
    except OSError:
        raise FormatError("archive", "The archive could not be read safely.") from None


def _source_bytes(base, relative, limit):
    """No absolute paths, traversal or symlinks, including intermediate directories."""
    need(isinstance(relative, str) and relative and "\\" not in relative and ":" not in relative and
         not relative.startswith("/") and all(p not in ("", ".", "..") for p in relative.split("/")),
         "path", "Source paths must be relative files within the build-spec directory.")
    need(hasattr(os, "O_NOFOLLOW") and os.open in os.supports_dir_fd, "platform", "Safe creation requires POSIX directory handles.")
    opened = []
    try:
        directory = os.open(base, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        opened.append(directory)
        parts = relative.split("/")
        for part in parts[:-1]:
            directory = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory)
            opened.append(directory)
        fd = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
        opened.append(fd)
        info = os.fstat(fd)
        need(stat.S_ISREG(info.st_mode) and info.st_size <= limit, "limit", "Expected a bounded regular source file.")
        with os.fdopen(os.dup(fd), "rb") as source:
            data = source.read(limit + 1)
        after = os.fstat(fd)
        need(len(data) <= limit and info.st_size == len(data) and info.st_mtime_ns == after.st_mtime_ns and
             info.st_ctime_ns == after.st_ctime_ns and info.st_size == after.st_size,
             "source", "A source changed while it was being read.")
        return data
    except FormatError:
        raise
    except (OSError, ValueError):
        raise FormatError("source", "A source file is unavailable or unsafe.") from None
    finally:
        for fd in reversed(opened):
            os.close(fd)


def _write_entry(archive, path, data):
    info = zipfile.ZipInfo(path, date_time=(1980, 1, 1, 0, 0, 0))
    info.create_system = 3
    info.external_attr = (stat.S_IFREG | 0o600) << 16
    info.compress_type = zipfile.ZIP_STORED
    archive.writestr(info, data)


@contextmanager
def _output_stage(destination, publication):
    """A private directory and retained handles prevent staging-path substitution."""
    need(hasattr(os, "O_NOFOLLOW") and os.open in os.supports_dir_fd and os.link in os.supports_dir_fd,
         "platform", "Safe creation requires POSIX directory handles.")
    parent = staging = None
    stage_name = None
    archive_created = False
    try:
        parent = os.open(destination.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            os.stat(destination.name, dir_fd=parent, follow_symlinks=False)
        except FileNotFoundError:
            pass
        else:
            raise FormatError("exists", "The output already exists; choose a new file.")
        for _ in range(8):
            candidate = f".showpapers-{uuid.uuid4().hex}.tmp"
            try:
                os.mkdir(candidate, mode=0o700, dir_fd=parent)
                stage_name = candidate
                break
            except FileExistsError:
                continue
        need(stage_name is not None, "storage", "A private staging directory could not be created.")
        staging = os.open(stage_name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
        stage_info = os.fstat(staging)
        need(stage_info.st_uid == os.geteuid() and stat.S_IMODE(stage_info.st_mode) & 0o077 == 0,
             "storage", "The staging directory is not private.")
        fd = os.open("archive.tmp", os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=staging)
        archive_created = True
        with os.fdopen(fd, "w+b") as stream:
            yield stream, staging, parent
    except Exception:
        if publication["published"]:
            publication["cleanupPending"] = True
        else:
            raise
    finally:
        cleanup_error = None
        try:
            if staging is not None:
                try:
                    if archive_created:
                        os.unlink("archive.tmp", dir_fd=staging)
                except FileNotFoundError:
                    pass
                finally:
                    try:
                        visible = os.stat(stage_name, dir_fd=parent, follow_symlinks=False)
                        held = os.fstat(staging)
                        if (visible.st_dev, visible.st_ino) == (held.st_dev, held.st_ino):
                            os.rmdir(stage_name, dir_fd=parent)
                    except FileNotFoundError:
                        pass
                    finally:
                        os.close(staging)
        except Exception as failure:
            cleanup_error = failure
        finally:
            if parent is not None:
                try:
                    os.close(parent)
                except Exception as failure:
                    cleanup_error = cleanup_error or failure
        if cleanup_error is not None:
            if publication["published"]:
                publication["cleanupPending"] = True
            else:
                raise cleanup_error


def _publish_archive(destination, write_payloads, cleanup_warnings=None, *, before_publish=None,
                     manifest_validator=None, full_fidelity=False):
    """The shared new-snapshot writer: validate the retained inode before linking it."""
    destination = Path(destination)
    need(destination.suffix == ".showpapers", "path", "The output must have the .showpapers suffix.")
    publication = {"published": False, "cleanupPending": False}
    with _output_stage(destination, publication) as (stream, staging, parent):
        with zipfile.ZipFile(stream, "w", allowZip64=False) as archive:
            manifest = (manifest_validator or validate_manifest)(write_payloads(archive))
            _write_entry(archive, "manifest.json", json_bytes(manifest))
        stream.flush()
        _read_archive_stream(stream, full_fidelity=full_fidelity)
        os.fsync(stream.fileno())
        held, visible = os.fstat(stream.fileno()), os.stat("archive.tmp", dir_fd=staging, follow_symlinks=False)
        need((held.st_dev, held.st_ino) == (visible.st_dev, visible.st_ino),
             "storage", "The staging file changed before publication.")
        if before_publish is not None:
            before_publish()
        try:
            os.link("archive.tmp", destination.name, src_dir_fd=staging, dst_dir_fd=parent, follow_symlinks=False)
        except FileExistsError:
            raise FormatError("exists", "The output already exists; choose a new file.") from None
        publication["published"] = True
    if publication["cleanupPending"] and cleanup_warnings is not None:
        cleanup_warnings.append({"code": "cleanup_pending", "message": "The new file was saved, but a temporary file could not be removed."})
    return manifest


def create_archive(spec_path, destination, cleanup_warnings=None):
    """Build from explicit local sources; publish a fully validated new file atomically."""
    spec_path, destination = Path(spec_path), Path(destination)
    try:
        with _regular_file(spec_path) as source:
            spec = parse_json(source.read(MAX_MANIFEST_BYTES + 1))
        if isinstance(spec, dict) and type(spec.get("formatVersion")) is int and spec["formatVersion"] == FULL_FIDELITY_FORMAT_VERSION:
            return _fidelity().create_from_spec(spec, spec_path.parent.resolve(), destination, cleanup_warnings)
        need(isinstance(spec, dict) and type(spec.get("formatVersion")) is int and spec["formatVersion"] in SUPPORTED_FORMAT_VERSIONS,
             "version", "Unsupported build-spec version.")
        version = spec["formatVersion"]
        keys(spec, {"formatVersion", "collection", "originals", "notes", "folders"})
        need(destination.suffix == ".showpapers", "path", "The output must have the .showpapers suffix.")
        for field, maximum in (("originals", MAX_ORIGINALS), ("notes", MAX_NOTES), ("folders", MAX_FOLDERS)):
            need(isinstance(spec[field], list) and len(spec[field]) <= maximum)
        manifest = {"format": "showpapers", "formatVersion": version, "protection": "readable",
                    "collection": spec["collection"], "originals": [], "notes": [], "folders": spec["folders"]}
        keys(spec["collection"], {"id", "revision", "title"})
        identifier(spec["collection"]["id"])
        seen = {spec["collection"]["id"]}
        for row in spec["originals"] + spec["notes"] + spec["folders"]:
            need(isinstance(row, dict))
            identifier(row.get("id"))
            need(row["id"] not in seen, "duplicate", "Record IDs must be unique across the collection.")
            seen.add(row["id"])
        available_papers = {row["id"] for row in spec["originals"]}
        def write_payloads(archive):
            total = 0
            for group in ("originals", "notes"):
                for row in spec[group]:
                    keys(row, {"id", "title", "source"} | ({"paperIds"} if group == "notes" else set()))
                    identifier(row["id"])
                    if group == "notes" and version == 3:
                        note_record_label(row["title"])
                    else:
                        bounded_text(row["title"])
                    note_limit = MAX_NOTE_RECORD_BYTES if version == 3 else MAX_RICH_NOTE_BYTES if version == 2 else MAX_NOTE_BYTES
                    data = _source_bytes(spec_path.parent.resolve(), row["source"], MAX_ORIGINAL_BYTES if group == "originals" else note_limit)
                    total += len(data)
                    need(total <= MAX_PAYLOAD_BYTES, "limit", "The collection exceeds the payload limit.")
                    output = {"id": row["id"], "title": row["title"], "size": len(data), "sha256": hashlib.sha256(data).hexdigest()}
                    if group == "originals":
                        media = media_type(data)
                        output.update(path=f"originals/{row['id']}.{MEDIA[media]}", mediaType=media)
                    else:
                        if version == 2:
                            document = note_document(data, available_papers)
                            need(document["paperIds"] == row["paperIds"], "reference", "Note references must match their manifest order.")
                        elif version == 3:
                            record = note_record(data, available_papers)
                            need(note_record_paper_ids(record) == row["paperIds"], "reference", "Scratch record references must match their manifest order.")
                        else:
                            note_text(data)
                        output.update(path=f"notes/{row['id']}.{'json' if version >= 2 else 'txt'}",
                                      encoding="utf-8", paperIds=row["paperIds"])
                    manifest[group].append(output)
                    _write_entry(archive, output["path"], data)
            return manifest
        return _publish_archive(destination, write_payloads, cleanup_warnings)
    except FormatError:
        raise
    except (OSError, ValueError, TypeError, KeyError, zipfile.BadZipFile):
        raise FormatError("storage", "The collection could not be created; existing output was not replaced.") from None


# These are local caller-supplied constraints, not authenticated host permissions.
OPERATION_FIELDS = {
    "note.add": ({"id", "title", "text", "paperIds"}, set()),
    "note.edit": ({"id"}, {"text", "paperIds"}),
    "note.remove": ({"id"}, set()),
    "record.rename": ({"id", "recordType", "title"}, set()),
    "folder.add": ({"id", "name", "paperIds", "noteIds"}, set()),
    "folder.rename": ({"id", "name"}, set()),
    "folder.remove": ({"id"}, set()),
    "folder.addMembers": ({"id", "paperIds", "noteIds"}, set()),
    "folder.removeMembers": ({"id", "paperIds", "noteIds"}, set()),
}
OPERATION_FIELDS_V2 = {
    **OPERATION_FIELDS,
    "note.add": ({"id", "title", "document"}, set()),
    "note.edit": ({"id", "document"}, set()),
}
OPERATION_FIELDS_V3 = {
    **OPERATION_FIELDS,
    "note.add": ({"id", "title", "record"}, set()),
    "note.edit": ({"id", "record"}, set()),
}
OPERATION_FIELDS_V4 = {
    **OPERATION_FIELDS_V3,
    "paper.add": ({"id", "title", "sourceId"}, set()),
    "paper.remove": ({"id"}, set()),
    "records.reorder": ({"id", "recordType", "recordIds"}, set()),
    "folder.reorderMembers": ({"id", "paperIds", "noteIds"}, set()),
    "collection.rename": ({"id", "title"}, set()),
}
RECORD_GROUPS = {"paper": "originals", "note": "notes", "folder": "folders"}


def _read_json_file(path):
    with _regular_file(path) as source:
        return parse_json(source.read(MAX_MANIFEST_BYTES + 1))


def _id_list(values, maximum=100):
    need(isinstance(values, list) and len(values) <= maximum, "operation", "Invalid record-ID list.")
    for value in values:
        identifier(value)
    need(len(set(values)) == len(values), "duplicate", "Repeated record IDs are not allowed.")
    return set(values)


def _operation_constraints(value, version=1):
    need(isinstance(value, dict) and type(value.get("scopeVersion")) is int and value["scopeVersion"] == version,
         "scope", "Explicit versioned scope constraints are required.")
    keys(value, {"scopeVersion", "collectionId", "actions", "paperIds", "noteIds", "folderIds"} |
         ({"sourceIds"} if version == 2 else set()))
    identifier(value["collectionId"])
    actions = value["actions"]
    allowed = OPERATION_FIELDS_V4 if version == 2 else OPERATION_FIELDS
    need(isinstance(actions, list) and len(actions) <= len(allowed) and
         all(isinstance(action, str) and action in allowed for action in actions),
         "scope", "Only explicitly listed supported actions are allowed.")
    need(len(set(actions)) == len(actions), "scope", "Scope actions must be unique.")
    result = {"collectionId": value["collectionId"], "actions": set(actions)}
    seen = {value["collectionId"]}
    for kind in RECORD_GROUPS:
        field = kind + "Ids"
        ids = _id_list(value[field], MAX_SCOPE_IDS)
        need(not (seen & ids), "scope", "Scope IDs must have one explicit record type.")
        result[field] = ids
        seen |= ids
    if version == 2:
        result["sourceIds"] = _id_list(value["sourceIds"], MAX_SOURCES)
    return result


def _require_scope(constraints, kind, ids):
    need(set(ids) <= constraints[kind + "Ids"], "scope", "The change is outside the supplied record scope.")


def _record(manifest, kind, record_id):
    for row in manifest[RECORD_GROUPS[kind]]:
        if row["id"] == record_id:
            return row
    raise FormatError("reference", "An operation refers to a missing record.")


def _checked_references(manifest, constraints, kind, values):
    ids = _id_list(values)
    _require_scope(constraints, kind, ids)
    need(ids <= {row["id"] for row in manifest[RECORD_GROUPS[kind]]},
         "reference", "An operation refers to a missing record.")
    return ids


def _note_bytes(value):
    need(isinstance(value, str), "note", "Note text must be a UTF-8 string.")
    try:
        data = value.encode("utf-8")
    except UnicodeError:
        raise FormatError("note", "Notes must contain valid UTF-8 text.") from None
    note_text(data)
    return data


def _set_note_text(row, data, payloads):
    row.update(size=len(data), sha256=hashlib.sha256(data).hexdigest())
    payloads[row["path"]] = data


def _v4_local_bytes(base, relative, limit):
    """The caller selects the root; every source-controlled component uses held no-follow FDs."""
    need(isinstance(relative, str) and relative and len(relative) <= MAX_SOURCE_PATH and
         not any(ord(c) < 32 or 127 <= ord(c) <= 159 or 0xD800 <= ord(c) <= 0xDFFF for c in relative) and
         "\\" not in relative and ":" not in relative and not relative.startswith("/") and
         all(p not in ("", ".", "..") for p in relative.split("/")),
         "source", "Expected a bounded relative source path.")
    need(hasattr(os, "O_NOFOLLOW") and os.open in os.supports_dir_fd,
         "platform", "Safe source admission requires directory handles.")
    opened = []
    try:
        directory = os.open(base, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        opened.append(directory)
        root = os.fstat(directory)
        for part in relative.split("/")[:-1]:
            directory = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory)
            opened.append(directory)
        fd = os.open(relative.split("/")[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
        opened.append(fd)
        info = os.fstat(fd)
        need(stat.S_ISREG(info.st_mode) and info.st_size <= limit,
             "source", "Expected a bounded regular source file.")
        with os.fdopen(os.dup(fd), "rb") as stream:
            before = _source_identity(stream)
            data = stream.read(limit + 1)
            need(len(data) <= limit and len(data) == info.st_size and _source_identity(stream) == before,
                 "source", "A selected source changed while it was read.")
        return data, (root.st_dev, root.st_ino, *before)
    except FormatError:
        raise
    except (OSError, ValueError, UnicodeError):
        raise FormatError("source", "A selected source is unavailable or unsafe.") from None
    finally:
        for fd in reversed(opened):
            os.close(fd)


class _OperationSources:
    """No file path comes from operation JSON. Remember identities, not arbitrary live handles."""
    def __init__(self, path, collection_id, allowed):
        self.path = Path(path)
        raw, self.identity = _v4_local_bytes(self.path.parent, self.path.name, MAX_MANIFEST_BYTES)
        self.digest = hashlib.sha256(raw).hexdigest()
        value = parse_json(raw)
        need(isinstance(value, dict) and type(value.get("sourcesVersion")) is int and value["sourcesVersion"] == 1,
             "version", "Expected a supported source-manifest version.")
        keys(value, {"sourcesVersion", "collectionId", "sources"})
        identifier(value["collectionId"])
        need(value["collectionId"] == collection_id, "scope", "Sources belong to a different collection.")
        need(isinstance(value["sources"], list) and len(value["sources"]) <= MAX_SOURCES,
             "limit", "The source manifest exceeds its limit.")
        self.sources, self.used = {}, {}
        total = 0
        for row in value["sources"]:
            keys(row, {"id", "path", "size", "sha256"})
            identifier(row["id"])
            need(row["id"] not in self.sources, "duplicate", "Source IDs must be unique.")
            need(row["id"] in allowed, "scope", "A source is outside the supplied source scope.")
            integer(row["size"], 1, MAX_ORIGINAL_BYTES)
            need(isinstance(row["sha256"], str) and HEX.fullmatch(row["sha256"]),
                 "digest", "Expected a canonical source SHA-256 digest.")
            # Check paths even for unconsumed entries, without opening those files.
            relative = row["path"]
            need(isinstance(relative, str) and 0 < len(relative) <= MAX_SOURCE_PATH and
                 not any(ord(c) < 32 or 127 <= ord(c) <= 159 or 0xD800 <= ord(c) <= 0xDFFF for c in relative) and
                 "\\" not in relative and ":" not in relative and not relative.startswith("/") and
                 all(p not in ("", ".", "..") for p in relative.split("/")),
                 "source", "Expected a bounded relative source path.")
            total += row["size"]
            need(total <= MAX_PAYLOAD_BYTES, "limit", "Selected sources exceed the payload limit.")
            self.sources[row["id"]] = row

    def read(self, source_id):
        identifier(source_id)
        need(source_id in self.sources, "scope", "The requested source was not admitted.")
        row = self.sources[source_id]
        data, identity = _v4_local_bytes(self.path.parent, row["path"], MAX_ORIGINAL_BYTES)
        need(len(data) == row["size"] and hashlib.sha256(data).hexdigest() == row["sha256"],
             "digest", "The selected source does not match its expected bytes.")
        need(source_id not in self.used or identity == self.used[source_id],
             "source", "A selected source changed during the operation.")
        self.used[source_id] = identity
        return data

    def check(self):
        raw, identity = _v4_local_bytes(self.path.parent, self.path.name, MAX_MANIFEST_BYTES)
        need(identity == self.identity and hashlib.sha256(raw).hexdigest() == self.digest,
             "source", "The source manifest changed during the operation.")
        for source_id in self.used:
            self.read(source_id)


def _apply_v4_operation(manifest, payloads, operation, constraints, used_ids, sources):
    action, record_id = operation["type"], operation["id"]
    change = {"type": action, "id": record_id}
    if action in ("collection.rename", "records.reorder"):
        need(record_id == manifest["collection"]["id"], "scope", "The operation targets a different collection.")
        if action == "collection.rename":
            bounded_text(operation["title"])
            if operation["title"] == manifest["collection"]["title"]:
                return None
            manifest["collection"]["title"] = operation["title"]
        else:
            kind = operation["recordType"]
            need(isinstance(kind, str) and kind in RECORD_GROUPS, "operation", "Unknown ordered record type.")
            requested = operation["recordIds"]
            ids = _id_list(requested)
            _require_scope(constraints, kind, ids)
            rows = manifest[RECORD_GROUPS[kind]]
            need(ids == {row["id"] for row in rows}, "reference", "Ordering requires an exact permutation of existing records.")
            if requested == [row["id"] for row in rows]:
                return None
            by_id = {row["id"]: row for row in rows}
            manifest[RECORD_GROUPS[kind]] = [by_id[record] for record in requested]
            change.update(recordType=kind, recordIds=list(requested))
        return change
    if action in ("paper.add", "paper.remove"):
        _require_scope(constraints, "paper", [record_id])
        if action == "paper.add":
            need(record_id not in used_ids, "duplicate", "A new record must have an unused ID.")
            bounded_text(operation["title"])
            identifier(operation["sourceId"])
            need(sources is not None and operation["sourceId"] in constraints["sourceIds"],
                 "scope", "Paper addition requires an explicitly admitted source.")
            data = sources.read(operation["sourceId"])
            media = media_type(data)
            row = {"id": record_id, "title": operation["title"], "size": len(data),
                   "sha256": hashlib.sha256(data).hexdigest(), "mediaType": media,
                   "path": f"originals/{record_id}.{MEDIA[media]}"}
            used_ids.add(record_id)
            manifest["originals"].append(row)
            payloads[row["path"]] = data
            change.update(sourceId=operation["sourceId"], size=len(data), sha256=row["sha256"], mediaType=media)
        else:
            row = _record(manifest, "paper", record_id)
            need(not any(record_id in note["paperIds"] for note in manifest["notes"]) and
                 not any(record_id in folder["paperIds"] for folder in manifest["folders"]),
                 "reference", "Remove all Note and folder references before removing this Paper.")
            manifest["originals"].remove(row)
            del payloads[row["path"]]
        return change
    # Both typed membership arrays are ordered independently; no interleaved order is invented.
    _require_scope(constraints, "folder", [record_id])
    row = _record(manifest, "folder", record_id)
    changed = False
    for kind in ("paper", "note"):
        field = kind + "Ids"
        requested = operation[field]
        ids = _checked_references(manifest, constraints, kind, requested)
        need(ids == set(row[field]), "reference", "Ordering requires an exact permutation of existing members.")
        changed |= requested != row[field]
    if not changed:
        return None
    for field in ("paperIds", "noteIds"):
        row[field] = list(operation[field])
        change[field] = list(operation[field])
    return change


def _apply_one_operation(manifest, payloads, operation, constraints, used_ids):
    action, record_id = operation["type"], operation["id"]
    rich_notes = manifest["formatVersion"] >= 2
    full_records = manifest["formatVersion"] == 3
    kind = "note" if action.startswith("note.") else "folder"
    if action == "record.rename":
        kind = operation["recordType"]
        need(isinstance(kind, str) and kind in ("paper", "note"), "operation", "Only Papers or notes can be renamed here.")
    _require_scope(constraints, kind, [record_id])
    change = {"type": action, "id": record_id}
    if action in ("note.add", "folder.add"):
        need(record_id not in used_ids, "duplicate", "A new record must have an unused ID.")
        used_ids.add(record_id)
        if action == "note.add" and full_records:
            document = validate_note_record(operation["record"], {paper["id"] for paper in manifest["originals"]})
            paper_ids = note_record_paper_ids(document)
        elif action == "note.add" and rich_notes:
            document = validate_note_document(operation["document"], {paper["id"] for paper in manifest["originals"]})
            paper_ids = document["paperIds"]
        else:
            paper_ids = operation["paperIds"]
        _checked_references(manifest, constraints, "paper", paper_ids)
        if action == "note.add":
            if full_records:
                note_record_label(operation["title"])
            else:
                bounded_text(operation["title"])
            row = {"id": record_id, "title": operation["title"], "path": f"notes/{record_id}.{'json' if rich_notes else 'txt'}",
                   "encoding": "utf-8", "paperIds": list(paper_ids)}
            available = {paper["id"] for paper in manifest["originals"]}
            data = (_note_record_bytes(document, available) if full_records else
                    _note_document_bytes(document, available) if rich_notes else _note_bytes(operation["text"]))
            _set_note_text(row, data, payloads)
            manifest["notes"].append(row)
        else:
            bounded_text(operation["name"], MAX_FOLDER_NAME)
            _checked_references(manifest, constraints, "note", operation["noteIds"])
            manifest["folders"].append({"id": record_id, "name": operation["name"],
                                        "paperIds": list(operation["paperIds"]), "noteIds": list(operation["noteIds"])})
            change["noteIds"] = sorted(operation["noteIds"])
        change["paperIds"] = sorted(paper_ids)
        return change

    row = _record(manifest, kind, record_id)
    if action == "note.edit":
        if rich_notes:
            available = {paper["id"] for paper in manifest["originals"]}
            document = (validate_note_record(operation["record"], available) if full_records else
                        validate_note_document(operation["document"], available))
            paper_ids = note_record_paper_ids(document) if full_records else document["paperIds"]
            incoming = _checked_references(manifest, constraints, "paper", paper_ids)
            previous = set(row["paperIds"])
            _require_scope(constraints, "paper", previous)
            # An equivalent replacement must not normalize an untouched JSON payload.
            decode = note_record if full_records else note_document
            if document == decode(payloads[row["path"]], available):
                return None
            data = _note_record_bytes(document, available) if full_records else _note_document_bytes(document, available)
            _set_note_text(row, data, payloads)
            row["paperIds"] = list(paper_ids)
            change.update(fields=["record" if full_records else "document"], addedPaperIds=sorted(incoming - previous),
                          removedPaperIds=sorted(previous - incoming))
            return change
        fields = []
        if "text" in operation:
            data = _note_bytes(operation["text"])
            if data != payloads[row["path"]]:
                _set_note_text(row, data, payloads)
                fields.append("text")
        if "paperIds" in operation:
            incoming = _checked_references(manifest, constraints, "paper", operation["paperIds"])
            previous = set(row["paperIds"])
            _require_scope(constraints, "paper", previous)
            if incoming != previous:
                row["paperIds"] = list(operation["paperIds"])
                fields.append("paperIds")
                change.update(addedPaperIds=sorted(incoming - previous), removedPaperIds=sorted(previous - incoming))
        if fields:
            change["fields"] = fields
            return change
        return None

    if action in ("record.rename", "folder.rename"):
        field = "name" if kind == "folder" else "title"
        if full_records and kind == "note":
            note_record_label(operation[field])
        else:
            bounded_text(operation[field], MAX_FOLDER_NAME if kind == "folder" else MAX_TITLE)
        if row[field] == operation[field]:
            return None
        row[field] = operation[field]
        if action == "record.rename":
            change["recordType"] = kind
        return change

    if action == "note.remove":
        _require_scope(constraints, "paper", row["paperIds"])
        need(not any(record_id in folder["noteIds"] for folder in manifest["folders"]),
             "reference", "Remove this note from its folders before removing the note.")
        manifest["notes"].remove(row)
        del payloads[row["path"]]
        change["paperIds"] = sorted(row["paperIds"])
        return change

    if action == "folder.remove":
        _require_scope(constraints, "paper", row["paperIds"])
        _require_scope(constraints, "note", row["noteIds"])
        manifest["folders"].remove(row)
        change.update(paperIds=sorted(row["paperIds"]), noteIds=sorted(row["noteIds"]))
        return change

    affected = {}
    for kind in ("paper", "note"):
        field = kind + "Ids"
        requested = _checked_references(manifest, constraints, kind, operation[field])
        previous = set(row[field])
        if action == "folder.addMembers":
            changed = requested - previous
            row[field].extend(item for item in operation[field] if item in changed)
        else:
            changed = requested & previous
            row[field] = [item for item in row[field] if item not in changed]
        affected[field] = sorted(changed)
    if any(affected.values()):
        change.update(affected)
        return change
    return None


def _validate_snapshot(manifest, payloads):
    validate_manifest(manifest)
    need(len(json_bytes(manifest)) <= MAX_MANIFEST_BYTES, "limit", "The resulting manifest is too large.")
    rows = manifest["originals"] + manifest["notes"]
    need(set(payloads) == {row["path"] for row in rows}, "archive", "The snapshot contains missing or undeclared payloads.")
    available = {paper["id"] for paper in manifest["originals"]}
    for row in rows:
        _validate_payload(row, payloads[row["path"]], manifest["formatVersion"], available)


def _plan_operations(archive_path, operations_path, constraints_path, *, sources_path=None, field_policy=None):
    need(constraints_path is not None, "scope", "Explicit scope constraints are required.")
    request = _read_json_file(operations_path)
    need(isinstance(request, dict) and type(request.get("protocolVersion")) is int and request["protocolVersion"] in SUPPORTED_OPERATION_PROTOCOL_VERSIONS,
         "version", "This operation-request version is not supported.")
    protocol = request["protocolVersion"]
    format_version = request.get("formatVersion") if protocol == 4 else protocol
    need(type(format_version) is int and format_version in SUPPORTED_FORMAT_VERSIONS,
         "version", "Expected a supported archive format version.")
    constraints = _operation_constraints(_read_json_file(constraints_path), 2 if protocol == 4 else 1)
    operation_fields = {1: OPERATION_FIELDS, 2: OPERATION_FIELDS_V2, 3: OPERATION_FIELDS_V3}[format_version]
    if protocol == 4:
        operation_fields = {**OPERATION_FIELDS_V4, **operation_fields}
    need(sources_path is None or protocol == 4, "version", "Source manifests require operation protocol 4.")
    keys(request, {"protocolVersion", "operationId", "collectionId", "expectedRevision", "operations"} |
         ({"expectedArchiveSha256"} if protocol >= 3 else set()) | ({"formatVersion"} if protocol == 4 else set()))
    if protocol >= 3:
        need(isinstance(request["expectedArchiveSha256"], str) and HEX.fullmatch(request["expectedArchiveSha256"]) is not None,
             "digest", "Expected a canonical SHA-256 digest for the input archive.")
    identifier(request["operationId"])
    identifier(request["collectionId"])
    integer(request["expectedRevision"], 1, MAX_REVISION)
    need(request["collectionId"] == constraints["collectionId"], "scope", "The collection is outside the supplied scope.")
    operations = request["operations"]
    need(isinstance(operations, list) and 1 <= len(operations) <= MAX_OPERATIONS,
         "operation", "An operation batch must contain between 1 and 32 operations.")
    for operation in operations:
        need(isinstance(operation, dict) and isinstance(operation.get("type"), str) and operation["type"] in operation_fields,
             "operation", "This operation is not supported.")
        required, optional = operation_fields[operation["type"]]
        need(required | {"type"} <= set(operation) <= required | optional | {"type"},
             "operation", "The operation fields are invalid.")
        if operation["type"] == "note.edit" and format_version == 1:
            need(bool(set(operation) & optional), "operation", "A note edit must supply text or Paper references.")
        identifier(operation["id"])
        need(operation["type"] in constraints["actions"], "scope", "The action is outside the supplied scope.")
    payloads = {}
    with _regular_file(archive_path) as source:
        if protocol >= 3:
            identity = _source_identity(source)
            source_digest = _archive_digest(source)
            need(source_digest == request["expectedArchiveSha256"], "digest", "The input snapshot does not match the expected archive digest.")
        manifest, _ = _read_archive_stream(source, payloads=payloads)
        if protocol >= 3:
            need(_archive_digest(source) == source_digest and _source_identity(source) == identity,
                 "source", "The input snapshot changed while it was being read.")
    need(manifest["formatVersion"] in SUPPORTED_FORMAT_VERSIONS,
         "version", "This archive format supports create, validate and inspect only; offline edits are not supported.")
    need(manifest["formatVersion"] == format_version, "version", "The request must match the archive format version.")
    need(manifest["collection"]["id"] == request["collectionId"], "scope", "The input collection does not match the supplied scope.")
    revision = manifest["collection"]["revision"]
    need(revision == request["expectedRevision"], "revision", "The input revision does not match the expected revision.")
    need(revision < MAX_REVISION, "revision", "The collection revision cannot be incremented safely.")
    baseline = json_bytes(manifest)
    baseline_papers = {row["id"] for row in manifest["originals"]}
    baseline_note_payloads = ({row["id"]: payloads[row["path"]] for row in manifest["notes"]}
                              if format_version >= 2 else {})
    used_ids = {manifest["collection"]["id"]} | {row["id"] for group in RECORD_GROUPS.values() for row in manifest[group]}
    sources = _OperationSources(sources_path, request["collectionId"], constraints["sourceIds"]) if sources_path is not None else None
    changes = []
    for operation in operations:
        if field_policy is not None:
            field_policy.authorize_operation(manifest, payloads, operation)
        change = (_apply_v4_operation(manifest, payloads, operation, constraints, used_ids, sources)
                  if operation["type"] in OPERATION_FIELDS_V4.keys() - OPERATION_FIELDS.keys() else
                  _apply_one_operation(manifest, payloads, operation, constraints, used_ids))
        validate_manifest(manifest)
        if change is not None:
            changes.append(change)
    if format_version >= 2:
        # All intermediate edits have already passed their own scope and shape checks.
        # Preserve an existing note's exact bytes when the batch restores its content.
        edited = {change["id"] for change in changes if change["type"] == "note.edit"}
        restored = set()
        available = {paper["id"] for paper in manifest["originals"]}
        for row in manifest["notes"]:
            if row["id"] not in edited or row["id"] not in baseline_note_payloads:
                continue
            original = baseline_note_payloads[row["id"]]
            current = payloads[row["path"]]
            decode = note_record if format_version == 3 else note_document
            if current == original or decode(current, available) == decode(original, baseline_papers):
                _set_note_text(row, original, payloads)
                restored.add(row["id"])
        changes = [change for change in changes if change["type"] != "note.edit" or change["id"] not in restored]
    need(json_bytes(manifest) != baseline, "no_change", "The requested operations do not change this snapshot.")
    manifest["collection"]["revision"] = revision + 1
    _validate_snapshot(manifest, payloads)
    summary = {"ok": True, "validated": True, "applied": False, "protocolVersion": protocol,
               "operationId": request["operationId"], "collectionId": request["collectionId"],
               "fromRevision": revision, "toRevision": revision + 1, "changes": changes}
    if protocol >= 3:
        summary["inputArchiveSha256"] = source_digest
    def check_inputs():
        if field_policy is not None:
            field_policy.check()
        if protocol == 4:
            with _regular_file(archive_path) as current:
                need(_source_identity(current) == identity and _archive_digest(current) == source_digest,
                     "source", "The input archive changed during the operation.")
            if sources is not None:
                sources.check()
    if protocol == 4:
        summary["formatVersion"] = format_version
    if field_policy is not None:
        summary["changes"] = field_policy.project_changes(summary["changes"])
    check_inputs()
    return manifest, payloads, summary, check_inputs


def process_operations(archive_path, operations_path, constraints_path, destination=None, *, sources_path=None, field_policy=None):
    """Validate offline changes; optionally write one new snapshot, never the input."""
    try:
        manifest, payloads, summary, check_inputs = _plan_operations(archive_path, operations_path, constraints_path,
                                                                    sources_path=sources_path, field_policy=field_policy)
        if destination is not None:
            def write_payloads(archive):
                for row in manifest["originals"] + manifest["notes"]:
                    _write_entry(archive, row["path"], payloads[row["path"]])
                return manifest
            cleanup_warnings = []
            if summary["protocolVersion"] == 4 or field_policy is not None:
                _publish_archive(destination, write_payloads, cleanup_warnings, before_publish=check_inputs)
            else:
                _publish_archive(destination, write_payloads, cleanup_warnings)
            summary["applied"] = True
            if cleanup_warnings:
                summary["warnings"] = cleanup_warnings
        return summary
    except FormatError:
        raise
    except (OSError, ValueError, TypeError, KeyError, zipfile.BadZipFile):
        raise FormatError("operation", "The operation could not be completed; the input was not changed.") from None


def inspect_summary(manifest):
    return {"ok": True, "format": "showpapers", "formatVersion": manifest["formatVersion"], "protection": "readable",
            "collectionId": manifest["collection"]["id"], "revision": manifest["collection"]["revision"],
            "originals": [{k: row[k] for k in ("id", "mediaType", "size", "sha256")} for row in sorted(manifest["originals"], key=lambda r: r["id"])],
            "notes": [{k: row[k] for k in ("id", "size", "sha256")} for row in sorted(manifest["notes"], key=lambda r: r["id"])],
            "folderCount": len(manifest["folders"]), "totalPayloadBytes": sum(r["size"] for r in manifest["originals"] + manifest["notes"])}


# The checklist's record categories, mapped to what readable profiles 1-3 actually carry.
# "represented: False" is a declared omission, not an optional field: readers refuse such keys.
CONTENT_COVERAGE = (
    {"category": "originals", "represented": True,
     "structure": ["manifest.originals[]", "originals/<id>.<pdf|png|jpg|webp>"]},
    {"category": "notes", "represented": True,
     "structure": ["manifest.notes[]", "notes/<id>.<txt|json>"]},
    {"category": "groups", "represented": True,
     "structure": ["manifest.folders[]"]},
    {"category": "links", "represented": True,
     "structure": ["manifest.notes[].paperIds", "manifest.folders[].paperIds", "manifest.folders[].noteIds",
                   "note-document.paperIds", "note-document.paperLinks[]", "note-record.items[].paperIds"]},
    {"category": "details", "represented": False, "structure": []},
    {"category": "people", "represented": False, "structure": []},
    {"category": "reviewHistory", "represented": False, "structure": []},
)
NOT_INCLUDED = ("reading-and-ocr-output", "reviewed-details", "review-history", "people-and-people-groups",
                "sdk-organization-relationships-and-decisions", "automatic-placement-and-folder-rules",
                "reminders", "conversations-and-ai-history", "app-settings", "keys-and-credentials",
                "native-vault-envelopes")


def contents_statement(manifest, notes):
    """A content-free, versioned statement of what one validated archive holds and omits.

    It is derived only after every payload has passed size, digest and profile validation. It
    carries no titles, names or text; IDs and digests remain private metadata, not analytics.
    """
    version = manifest["formatVersion"]
    documents = [] if version == 1 else [note if version == 2 else note["document"] for note in notes.values()]
    documents = [document for document in documents if document is not None]
    links = {"notePaperReferences": sum(len(row["paperIds"]) for row in manifest["notes"]),
             "folderPaperMemberships": sum(len(row["paperIds"]) for row in manifest["folders"]),
             "folderNoteMemberships": sum(len(row["noteIds"]) for row in manifest["folders"]),
             "inlinePaperLinks": sum(len(document["paperLinks"]) for document in documents),
             "legacyRowAttachments": sum(len(item["paperIds"]) for note in notes.values() for item in note["items"])
                                     if version == 3 else 0}
    payload = NOTE_PAYLOADS[version]
    return {"ok": True, "contentsVersion": 1, "specificationVersion": SPECIFICATION_VERSION,
            "format": "showpapers", "formatVersion": version, "protection": "readable",
            "collectionId": manifest["collection"]["id"], "revision": manifest["collection"]["revision"],
            "manifestSchema": f"urn:showpapers:format:manifest:{version}",
            "noteRepresentation": payload["representation"],
            "operationProtocolVersions": [version, 4],
            "originals": [{k: row[k] for k in ("id", "path", "mediaType", "size", "sha256")} for row in manifest["originals"]],
            "notes": [{**{k: row[k] for k in ("id", "path", "size", "sha256")}, "paperReferences": len(row["paperIds"])}
                      for row in manifest["notes"]],
            "folders": [{"id": row["id"], "papers": len(row["paperIds"]), "notes": len(row["noteIds"])}
                        for row in manifest["folders"]],
            "counts": {"originals": len(manifest["originals"]), "notes": len(manifest["notes"]),
                       "folders": len(manifest["folders"])},
            "links": links,
            "totalPayloadBytes": sum(r["size"] for r in manifest["originals"] + manifest["notes"]),
            "verified": {"everyDeclaredPayloadPresent": True, "noUndeclaredEntries": True,
                         "sizesAndSha256": True, "mediaSignatures": True, "references": True},
            "coverage": {row["category"]: row["represented"] for row in CONTENT_COVERAGE},
            "notIncluded": list(NOT_INCLUDED),
            "selectedCollection": True, "fullVaultBackup": False}


def archive_contents(path):
    manifest, notes = read_archive(path, include_content=True, full_fidelity=True)
    if manifest["formatVersion"] == FULL_FIDELITY_FORMAT_VERSION:
        return _fidelity().contents_statement(manifest, notes)
    return contents_statement(manifest, notes)


def summary(manifest):
    """inspect_summary for any readable profile."""
    if manifest["formatVersion"] == FULL_FIDELITY_FORMAT_VERSION:
        return _fidelity().inspect_summary(manifest)
    return inspect_summary(manifest)


def content_members(manifest, content):
    """The --include-content members for any readable profile."""
    if manifest["formatVersion"] == FULL_FIDELITY_FORMAT_VERSION:
        return {"manifest": manifest, "noteRecords": content["notes"], "paperRecords": content["papers"],
                "readings": content["readings"], "organization": _fidelity().organization_inventory(manifest, content)}
    return {"manifest": manifest, {1: "noteText", 2: "noteDocuments", 3: "noteRecords"}[manifest["formatVersion"]]: content}


class Parser(argparse.ArgumentParser):
    def error(self, _):
        raise FormatError("arguments", "Invalid arguments; use --help for usage.")


def main(argv=None):
    try:
        parser = Parser(description="Create and validate readable .showpapers files. These files are not encrypted backups.")
        sub = parser.add_subparsers(dest="command", required=True, parser_class=Parser)
        create = sub.add_parser("create")
        create.add_argument("--spec", required=True)
        create.add_argument("--output", required=True)
        sub.add_parser("contents").add_argument("archive")
        for verb in ("validate", "inspect"):
            item = sub.add_parser(verb)
            item.add_argument("archive")
            item.add_argument("--grant", help="Scope constraints required with field permissions.")
            item.add_argument("--field-policy", help="Explicit host-selected field permissions JSON.")
            if verb == "inspect":
                item.add_argument("--include-content", action="store_true", help="Explicitly print titles, references and note content.")
        for verb in ("preview", "apply"):
            item = sub.add_parser(verb)
            item.add_argument("archive")
            item.add_argument("--operations", help="An explicit versioned operation-request JSON file (profiles 1-3).")
            item.add_argument("--grant", help="Caller-supplied scope constraints; not authenticated permission.")
            item.add_argument("--changes", help="A urn:showpapers:changes:1 document; the result is a profile 4 file.")
            item.add_argument("--field-policy", help="Explicit host-selected field permissions JSON.")
            item.add_argument("--sources", help="Separately owner-selected source manifest for protocol 4 Paper addition.")
            if verb == "preview":
                item.add_argument("--include-content", action="store_true", help="Explicitly print changed authored values for review.")
            if verb == "apply":
                item.add_argument("--output", required=True, help="A new .showpapers path; existing files are never replaced.")
        for verb in ("preview-undo", "undo"):
            item = sub.add_parser(verb)
            item.add_argument("archive", help="The exact saved forward-result archive.")
            item.add_argument("--before", required=True, help="The explicitly selected pre-change archive.")
            item.add_argument("--operations", required=True, help="The exact original forward request.")
            item.add_argument("--grant", required=True, help="The unchanged original scope, also authorizing the inverse.")
            item.add_argument("--undo-request", required=True, help="A versioned request binding both snapshots and original inputs.")
            item.add_argument("--field-policy", help="Explicit host-selected field permissions JSON.")
            item.add_argument("--sources", help="Original selected-source manifest if the forward request requires it.")
            item.add_argument("--include-content", action="store_true", help="Explicitly print changed authored values for review.")
            if verb == "undo":
                item.add_argument("--output", required=True, help="A new .showpapers path; existing files are never replaced.")
        for verb in ("conflicts", "preview-resolve", "resolve"):
            item = sub.add_parser(verb)
            item.add_argument("archive", help="The named base snapshot both sides started from.")
            item.add_argument("--theirs", required=True, help="The other editor's snapshot; the result builds on it.")
            mine = item.add_mutually_exclusive_group(required=True)
            mine.add_argument("--mine", help="The caller's own saved copy.")
            mine.add_argument("--operations", help="The caller's own request that targets the base snapshot.")
            item.add_argument("--grant", required=True, help="Caller-supplied scope constraints; not authenticated permission.")
            item.add_argument("--field-policy", help="Explicit host-selected field permissions JSON.")
            item.add_argument("--sources", help="Owner-selected source manifest when the request adds a Paper.")
            if verb == "conflicts":
                item.add_argument("--include-content", action="store_true", help="Explicitly print the conflicting authored values.")
            else:
                item.add_argument("--resolution", required=True, help="One explicit choice for every reported conflict.")
            if verb == "resolve":
                item.add_argument("--output", required=True, help="A new .showpapers path; existing files are never replaced.")
        edit = sub.add_parser("edit")
        edit.add_argument("archive")
        edit.add_argument("--output-directory", required=True, help="A new directory for build.json and payload files.")
        companion = sub.add_parser("companion")
        companion.add_argument("archive")
        companion.add_argument("--output", required=True, help="A new <name>.showpapers.json file.")
        companion.add_argument("--archive-name", help="The file name AI apps will see for the archive.")
        upgrade = sub.add_parser("upgrade")
        upgrade.add_argument("archive")
        upgrade.add_argument("--output", required=True, help="A new .showpapers path for the profile 4 copy.")
        schema_parser = sub.add_parser("schema")
        schema_parser.add_argument("--kind", choices=("manifest", "build-spec", "operations", "scope", "sources", "note-document", "note-record", "undo", "field-policy", "workspace-receipt", "contents", "conflict-report", "conflict-resolution", "connection-grant", "paper-record", "reading"), default="manifest")
        schema_parser.add_argument("--version", type=int, choices=SUPPORTED_OPERATION_PROTOCOL_VERSIONS, default=VERSION,
                                   help="Schema version (default: 1 for compatibility). Use --version 4 for current manifest/build-spec schemas.")
        sub.add_parser("capabilities")
        sub.add_parser("discover")
        args = parser.parse_args(argv)
        field_policy = None
        if args.command in ("inspect", "validate"):
            need(args.grant is None or args.field_policy is not None, "permission",
                 "Scoped inspection requires explicit field permissions.")
        if getattr(args, "field_policy", None) is not None:
            if __name__ == "__main__":
                sys.modules.setdefault("showpapers_format", sys.modules[__name__])
            from showpapers_grants import load
            field_policy = load(args.field_policy, args.grant)
        if args.command == "schema":
            need((args.kind == "operations") or (args.kind == "scope" and args.version <= 3) or
                 (args.kind in ("manifest", "build-spec") and args.version in READABLE_FORMAT_VERSIONS) or
                 (args.kind in ("note-document", "note-record") and args.version <= 3) or
                 (args.kind == "contents" and args.version in (1, 2)) or
                 (args.kind in ("sources", "undo", "field-policy", "workspace-receipt", "conflict-report", "conflict-resolution",
                               "connection-grant", "paper-record", "reading") and args.version == 1),
                 "version", "This schema version is not supported.")
            suffix = f".v{args.version}" if args.version >= 2 and args.kind in ("manifest", "build-spec", "operations", "contents") else ""
            if args.kind == "scope" and args.version == 2:
                suffix = ".v2"
            name = f"{args.kind}{suffix}.schema.json"
            if args.kind in ("manifest", "build-spec") and args.version == FULL_FIDELITY_FORMAT_VERSION:
                name = f"full-fidelity-{args.kind}.schema.json"
            result = json.loads(Path(__file__).with_name(name).read_text(encoding="utf-8"))
        elif args.command == "discover":
            from showpapers_discovery import discovery
            result = discovery()
        elif args.command == "capabilities":
            result = {"ok": True, "formatVersion": VERSION, "protection": "readable",
                      "currentCollection": current_collection_guidance(),
                      "specificationVersion": SPECIFICATION_VERSION,
                      "discoveryVersion": 1, "discoveryCommand": "discover",
                      "supportedFormatVersions": list(SUPPORTED_FORMAT_VERSIONS),
                      "commands": ["create", "validate", "inspect", "contents", "preview", "apply", "preview-undo", "undo",
                                   "edit", "companion", "upgrade"],
                      "profiles": ["originals", "plain-text-notes", "rich-note-content", "folder-references", "scratch-records"],
                      "schemas": ["manifest", "build-spec", "operations", "scope", "sources", "note-document", "note-record", "undo",
                                  "field-policy", "workspace-receipt", "contents", "conflict-report", "conflict-resolution",
                                  "connection-grant", "paper-record", "reading"],
                      "readableFormatVersions": list(READABLE_FORMAT_VERSIONS),
                      "fullFidelityFormatVersion": FULL_FIDELITY_FORMAT_VERSION,
                      "fullFidelity": {"formatVersion": FULL_FIDELITY_FORMAT_VERSION, "discover": "discover.fullFidelity",
                                       "editWith": ["edit + create", "apply --changes"], "operationProtocols": [],
                                       "interchangeSchemas": ["urn:showpapers:companion:1", "urn:showpapers:companion:2",
                                                              "urn:showpapers:changes:1"]},
                      "connectionGrantVersion": 1,
                      "conditionalUndoVersion": 1,
                      "conflictResolutionVersion": 1, "conflictCommands": ["conflicts", "preview-resolve", "resolve"],
                      "offlineOperations": sorted(OPERATION_FIELDS), "operationProtocolVersion": 1,
                      "operationProtocolVersions": list(SUPPORTED_OPERATION_PROTOCOL_VERSIONS), "scratchDocumentVersion": 1,
                      "scratchRecordVersion": 1, "offlineOperationFormatVersions": list(SUPPORTED_FORMAT_VERSIONS),
                      "operationProtocol4": {"supportedFormatVersions": list(SUPPORTED_FORMAT_VERSIONS),
                                             "actions": sorted(OPERATION_FIELDS_V4), "scopeVersion": 2, "sourcesVersion": 1},
                      "formatCommands": {**{str(version): ["create", "validate", "inspect", "contents", "preview", "apply", "preview-undo", "undo"]
                                            for version in SUPPORTED_FORMAT_VERSIONS},
                                         str(FULL_FIDELITY_FORMAT_VERSION): ["create", "validate", "inspect", "contents", "edit", "preview",
                                                                             "apply", "companion", "upgrade"]},
                      "operationPreconditions": {"3": ["collectionId", "expectedRevision", "expectedArchiveSha256"],
                                                 "4": ["collectionId", "formatVersion", "expectedRevision", "expectedArchiveSha256"]},
                      "scopeAuthority": "caller-supplied-constraints", "authenticatedPermissions": False,
                      "limits": {"noteUtf16CodeUnits": MAX_NOTE_UTF16, "richNoteBytes": MAX_RICH_NOTE_BYTES,
                                 "noteMarks": MAX_NOTE_MARKS, "notePaperLinks": MAX_NOTE_PAPER_LINKS,
                                 "noteRecordBytes": MAX_NOTE_RECORD_BYTES, "noteRecordItems": MAX_NOTE_RECORD_ITEMS,
                                 "noteRecordLabelScalars": MAX_NOTE_RECORD_LABEL, "noteItemPaperReferences": MAX_NOTE_ITEM_PAPERS,
                                 "operationBatch": MAX_OPERATIONS, "operationJsonBytes": MAX_MANIFEST_BYTES},
                      "durableReplayPrevention": False, "globalLatestRevision": False,
                      "fullVaultBackup": False, "encryptedBackup": False, "androidImport": False, "liveAppConnection": False}
        elif args.command == "contents":
            result = archive_contents(args.archive)
        elif args.command == "create":
            cleanup_warnings = []
            result = summary(create_archive(args.spec, args.output, cleanup_warnings))
            if cleanup_warnings:
                result["warnings"] = result.get("warnings", []) + cleanup_warnings
        elif args.command == "edit":
            result = _fidelity().edit_archive(args.archive, args.output_directory)
        elif args.command == "companion":
            result = _fidelity().write_companion(args.archive, args.output, args.archive_name)
        elif args.command == "upgrade":
            cleanup_warnings = []
            result = _fidelity().upgrade_archive(args.archive, args.output, cleanup_warnings)
            if cleanup_warnings:
                result["warnings"] = cleanup_warnings
        elif args.command in ("preview", "apply") and args.changes is not None:
            need(args.operations is None and args.grant is None and args.field_policy is None and args.sources is None and
                 not getattr(args, "include_content", False), "arguments",
                 "A changes document is used without operations, scope, field policy or sources.")
            cleanup_warnings = []
            result = _fidelity().apply_changes(args.archive, args.changes, getattr(args, "output", None), cleanup_warnings)
            if cleanup_warnings:
                result["warnings"] = cleanup_warnings
        elif args.command in ("preview", "apply") and (args.operations is None or args.grant is None):
            raise FormatError("arguments", "Invalid arguments; use --help for usage.")
        elif args.command == "preview" and args.include_content:
            if __name__ == "__main__":
                sys.modules.setdefault("showpapers_format", sys.modules[__name__])
            from showpapers_undo import preview_with_content
            result = preview_with_content(args.archive, args.operations, args.grant, sources_path=args.sources, field_policy=field_policy)
        elif args.command in ("preview", "apply"):
            result = process_operations(args.archive, args.operations, args.grant, getattr(args, "output", None),
                                        sources_path=args.sources, field_policy=field_policy)
        elif args.command in ("preview-undo", "undo"):
            # The reusable module must share this CLI's sanitized exception class when run as a script.
            if __name__ == "__main__":
                sys.modules.setdefault("showpapers_format", sys.modules[__name__])
            from showpapers_undo import process_undo
            result = process_undo(args.before, args.archive, args.operations, args.grant, args.undo_request,
                                  getattr(args, "output", None), sources_path=args.sources, include_content=args.include_content, field_policy=field_policy)
        elif args.command in ("conflicts", "preview-resolve", "resolve"):
            if __name__ == "__main__":
                sys.modules.setdefault("showpapers_format", sys.modules[__name__])
            from showpapers_conflicts import process_conflicts
            result = process_conflicts(args.archive, args.theirs, args.grant, mine_archive=args.mine,
                                       mine_request=args.operations, resolution_path=getattr(args, "resolution", None),
                                       destination=getattr(args, "output", None), sources_path=args.sources,
                                       include_content=getattr(args, "include_content", False), field_policy=field_policy)
        elif field_policy is not None:
            import showpapers_api
            result = showpapers_api.inspect(args.archive, include_content=getattr(args, "include_content", False),
                                            constraints_path=args.grant, field_policy_path=args.field_policy)
        else:
            manifest, notes = read_archive(args.archive, include_content=getattr(args, "include_content", False),
                                           full_fidelity=True)
            result = summary(manifest)
            if getattr(args, "include_content", False):
                result.update(content_members(manifest, notes))
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        return 0
    except FormatError as failure:
        print(json.dumps({"ok": False, "error": {"code": failure.code, "message": str(failure)}}, sort_keys=True))
        return 2
    except (OSError, TypeError, ValueError, KeyError, RecursionError):
        print(json.dumps({"ok": False, "error": {"code": "invalid", "message": "The input could not be processed safely."}}, sort_keys=True))
        return 2


if __name__ == "__main__":
    sys.exit(main())
