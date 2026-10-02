#!/usr/bin/env python3
"""Standalone writer for a .showpapers collection under specification 1.0.0. Python 3.8+ standard library only; no ShowPapers code needed.

Give it a JSON plan and the original files; it writes a new archive to validate before opening:

    python3 full_fidelity_writer.py plan.json new.showpapers

plan.json:
    {"collection": {"title": "Trip papers"},
     "agent": {"name": "Example Assistant", "platform": "example.ai", "version": "2026-09"},
     "papers": [{"file": "passport.pdf", "title": "Passport", "type": "passport",
                 "details": [{"key": "expiration-date", "value": "04 MAY 2031", "normalizedValue": "2031-05-04",
                              "valueType": "date", "confidence": 0.9, "quote": "Date of expiry 04 MAY 2031",
                              "page": 0}]}],
     "folders": [{"name": "May 2031 Trip", "purpose": "Papers for the requested May 2031 trip",
                  "papers": ["passport.pdf"]}]}

Every detail and type it writes is marked as coming from the agent, so the person reviews it in the app. Always run
the real validator afterwards (`showpapers_format.py validate`, or the validator on protocol.showpapers.app).
This is creation-only: it generates fresh IDs and cannot preserve an existing collection. For an existing archive
plus new files, use the reference tool's inspect/edit/create workflow, compare original hashes and reuse relevant
folder IDs. Standard built-in categories do not need personal-folder copies. Use personal folders for a distinct
requested purpose, and do not claim to know folders in an unseen library.
"""
import hashlib
import json
import os
import sys
import uuid
import zipfile

MEDIA = {b"%PDF-": ("application/pdf", "pdf"), b"\x89PNG\r\n\x1a\n": ("image/png", "png"),
         b"\xff\xd8\xff": ("image/jpeg", "jpg")}


def media_of(data):
    for signature, value in MEDIA.items():
        if data.startswith(signature):
            return value
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return ("image/webp", "webp")
    raise SystemExit("Originals must be PDF, PNG, JPEG or WebP.")


def name_uuid(text):
    """UUID.nameUUIDFromBytes: MD5, version 3. Used for the agent ID so proposals from one agent share it."""
    digest = bytearray(hashlib.md5(text.encode("utf-8")).digest())
    digest[6] = (digest[6] & 0x0F) | 0x30
    digest[8] = (digest[8] & 0x3F) | 0x80
    return str(uuid.UUID(bytes=bytes(digest)))


def canonical(value):
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")


def entry(archive, path, data):
    info = zipfile.ZipInfo(path, date_time=(1980, 1, 1, 0, 0, 0))
    info.external_attr = 0o100600 << 16
    info.compress_type = zipfile.ZIP_STORED  # stored, no extra fields, no directory entries
    archive.writestr(info, data)


def main(plan_path, output):
    if os.path.exists(output) or not output.endswith(".showpapers"):
        raise SystemExit("Choose a new file ending in .showpapers.")
    base = os.path.dirname(os.path.abspath(plan_path))
    with open(plan_path, encoding="utf-8") as stream:
        plan = json.load(stream)
    agent = plan["agent"]
    agent_id = name_uuid(f"showpapers:agent:1:{agent['name']}\n{agent['platform']}\n{agent['version']}")
    manifest = {"format": "showpapers", "formatVersion": 4, "protection": "readable",
                "generator": {"name": agent["name"], "platform": agent["platform"], "version": agent["version"]},
                "collection": {"id": str(uuid.uuid4()), "revision": 1, "title": " ".join(plan["collection"]["title"].split())[:200].strip()},
                "agents": [{"id": agent_id, "name": agent["name"], "platform": agent["platform"],
                            "version": agent["version"]}],
                "originals": [], "papers": [], "folders": []}
    payloads, by_file = {}, {}
    for paper in plan["papers"]:
        with open(os.path.join(base, paper["file"]), "rb") as stream:
            data = stream.read()
        media, extension = media_of(data)
        paper_id = str(uuid.uuid4())
        by_file[paper["file"]] = paper_id
        title = " ".join(paper["title"].replace("/", " ").replace("\\", " ").split())[:100].strip()
        path = f"originals/{paper_id}.{extension}"
        payloads[path] = data
        manifest["originals"].append({"id": paper_id, "title": title, "path": path, "size": len(data),
                                      "sha256": hashlib.sha256(data).hexdigest(), "mediaType": media})
        record = {"paperVersion": 1, "details": []}
        if paper.get("type"):
            record["type"] = {"detected": None, "suggested": [], "selected": None, "custom": None,
                              "agentSuggestion": {"agentId": agent_id, "kind": paper["type"],
                                                  "custom": paper.get("customType"), "confidence": None}}
        for item in paper.get("details", []):
            record["details"].append({
                "id": str(uuid.uuid4()), "origin": "agent", "key": item["key"],
                "label": item.get("label"), "value": item["value"], "normalizedValue": item.get("normalizedValue"),
                "valueType": item.get("valueType", "text"), "concerns": [],
                "evidence": [{"page": item.get("page", 0), "quote": item["quote"], "region": None}]
                if item.get("quote") else [],
                "confidence": item.get("confidence"), "agentId": agent_id,
                "review": {"confirmed": False, "correctedValue": None}})
        data = canonical(record)
        path = f"papers/{paper_id}.json"
        payloads[path] = data
        manifest["papers"].append({"id": paper_id, "path": path, "size": len(data),
                                   "sha256": hashlib.sha256(data).hexdigest()})
    for folder in plan.get("folders", []):
        manifest["folders"].append({"id": folder.get("id", str(uuid.uuid4())), "name": " ".join(folder["name"].split())[:80].strip(), "color": "blue",
                                    "icon": None, "purpose": folder.get("purpose", ""), "rule": None,
                                    "paperIds": [by_file[name] for name in folder["papers"]],
                                    **({"parentId": folder["parentId"]} if "parentId" in folder else {})})
    # Explicit folder IDs let a creation plan describe a tree without guessing
    # which generated ID belongs to a parent. They do not make this an editor.
    folders = manifest["folders"]
    if len(folders) > 64:
        raise SystemExit("A collection holds at most 64 personal folders.")
    used_ids = {manifest["collection"]["id"], agent_id, *by_file.values()}
    parents, names = {}, set()
    destinations = {"root", "identity", "visas", "notices", "applications", "residency", "work-authorization",
                    "travel-entry", "study", "home", "finance", "health", "education", "work", "family", "legal",
                    "other", "permits", "other-folders"}
    for folder in folders:
        identifier, parent = folder["id"], folder.get("parentId")
        try:
            canonical_id = isinstance(identifier, str) and str(uuid.UUID(identifier)) == identifier
        except (ValueError, AttributeError, TypeError):
            canonical_id = False
        if not canonical_id or identifier in used_ids:
            raise SystemExit("Folder IDs must be distinct canonical lowercase UUIDs.")
        used_ids.add(identifier)
        if parent is not None and not isinstance(parent, str):
            raise SystemExit("A folder parent must be a destination or folder ID.")
        parents[identifier] = parent
        sibling_name = (parent if parent is not None else "other-folders", folder["name"].lower())
        if sibling_name in names:
            raise SystemExit("Sibling folder names must be unique ignoring case.")
        names.add(sibling_name)
    for identifier, parent in parents.items():
        if parent is not None and parent not in destinations and parent not in parents:
            raise SystemExit("A folder parent must exist in the collection.")
        visited = {identifier}
        while parent in parents:
            if parent in visited:
                raise SystemExit("Folder parents must not contain a cycle.")
            visited.add(parent)
            parent = parents[parent]
    with zipfile.ZipFile(output, "x", allowZip64=False) as archive:
        for path, data in payloads.items():
            entry(archive, path, data)
        entry(archive, "manifest.json", canonical(manifest))
    print(json.dumps({"ok": True, "output": output, "collectionId": manifest["collection"]["id"]}))


if __name__ == "__main__":
    if len(sys.argv) != 3:
        raise SystemExit("usage: full_fidelity_writer.py plan.json new.showpapers")
    main(sys.argv[1], sys.argv[2])
