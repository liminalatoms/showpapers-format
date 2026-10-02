"""Closed, host-selected field constraints. Never take this policy from an AI request.

The local Python caller still controls its own process and files. MCP pins this file at
launch; this module supplies enforcement, not signatures or operating-system isolation.
"""
from __future__ import annotations

import copy
import hashlib
import showpapers_format as fmt

FIELDS = {
    "collection": frozenset(("title", "order")),
    "paper": frozenset(("title", "original")),
    "note": frozenset(("title", "content", "paperIds")),
    "folder": frozenset(("name", "paperIds", "noteIds")),
}
ACTIONS = frozenset(fmt.OPERATION_FIELDS_V4) | {"read", "search"}
MAX_POLICY_BYTES = 1024 * 1024


def denied(ok):
    fmt.need(ok, "permission", "The requested action or field is outside the host-selected permissions.")


def validate(value, scope):
    """Validate a closed policy and intersect its record authority with the old scope."""
    denied(type(value) is dict and set(value) == {
        "policyVersion", "collectionId", "actions", "collection", "papers", "notes", "folders"})
    denied(type(value["policyVersion"]) is int and value["policyVersion"] == 1)
    denied(value["collectionId"] == scope["collectionId"])
    actions = value["actions"]
    denied(type(actions) is list and len(actions) <= len(ACTIONS) and
           all(type(a) is str and a in ACTIONS for a in actions) and len(actions) == len(set(actions)))
    denied(set(actions) - {"read", "search"} <= set(scope["actions"]))
    def access(row, kind):
        denied(type(row) is dict and set(row) == {"read", "write"})
        for mode in ("read", "write"):
            names = row[mode]
            denied(type(names) is list and len(names) <= len(FIELDS[kind]) and
                   all(type(n) is str and n in FIELDS[kind] for n in names) and len(names) == len(set(names)))
    access(value["collection"], "collection")
    for kind in ("paper", "note", "folder"):
        rows = value[kind + "s"]
        denied(type(rows) is dict and len(rows) <= 132 and set(rows) <= set(scope[kind + "Ids"]))
        for row in rows.values():
            access(row, kind)
    return copy.deepcopy(value)


def _bound(path):
    with fmt._regular_file(path) as stream:
        identity = fmt._source_identity(stream)
        raw = stream.read(MAX_POLICY_BYTES + 1)
        denied(len(raw) <= MAX_POLICY_BYTES and fmt._source_identity(stream) == identity)
    digest = hashlib.sha256(raw).hexdigest()
    def check():
        with fmt._regular_file(path) as stream:
            current = stream.read(MAX_POLICY_BYTES + 1)
            denied(fmt._source_identity(stream) == identity and len(current) <= MAX_POLICY_BYTES and
                   hashlib.sha256(current).hexdigest() == digest)
    return fmt.parse_json(raw), check


def load(path, constraints_path):
    if path is None:
        return None
    denied(constraints_path is not None)
    try:
        scope, check_scope = _bound(constraints_path)
        version = scope.get("scopeVersion") if type(scope) is dict else None
        denied(type(version) is int and version in (1, 2))
        checked_scope = fmt._operation_constraints(scope, version)
        value, check_policy = _bound(path)
        def check():
            check_scope(); check_policy()
        policy = FieldPolicy(validate(value, checked_scope), checked_scope, check)
        policy.check()
        return policy
    except (fmt.FormatError, OSError, ValueError, TypeError, KeyError, RecursionError):
        raise fmt.FormatError("permission", "The host-selected permissions are invalid or changed.") from None


class FieldPolicy:
    def __init__(self, value, scope, check=lambda: None):
        self.value, self.scope, self._check = value, scope, check

    def check(self):
        try:
            self._check()
        except (fmt.FormatError, OSError, ValueError, TypeError):
            raise fmt.FormatError("permission", "The host-selected permissions changed or are unavailable.") from None

    def action(self, name):
        denied(name in self.value["actions"])

    def allowed(self, kind, record_id, name, mode="read"):
        if kind == "collection":
            if record_id != self.value["collectionId"]:
                return False
            row = self.value["collection"]
        else:
            row = self.value[kind + "s"].get(record_id, {})
        return name in row.get(mode, []) and (mode != "read" or "read" in self.value["actions"])

    def visible_refs(self, row, kind):
        return all(set(row.get(target + "Ids", [])) <= self.scope[target + "Ids"]
                   for target in (("paper", "note") if kind == "folder" else ("paper",)))

    def content(self, row):
        return self.allowed("note", row["id"], "content") and self.allowed("note", row["id"], "paperIds") and self.visible_refs(row, "note")

    def authorize_operation(self, manifest, payloads, op):
        self.check()
        denied(manifest["collection"]["id"] == self.value["collectionId"])
        name = op["type"]
        self.action(name)
        kind = (op.get("recordType") if name == "record.rename" else
                "collection" if name in ("records.reorder", "collection.rename") else name.split(".")[0])
        if name.endswith((".add", ".remove")):
            fields = FIELDS[kind]
        elif name == "note.edit":
            fields = ({"content"} if "text" in op else set()) | ({"paperIds"} if "paperIds" in op else set())
            if manifest["formatVersion"] >= 2:
                fields = {"content", "paperIds"}  # A complete native payload also carries links/metadata.
        elif name == "records.reorder":
            fields = {"order"}
        elif name in ("folder.addMembers", "folder.removeMembers", "folder.reorderMembers"):
            fields = {key for key in ("paperIds", "noteIds") if op[key] or name == "folder.reorderMembers"}
        else:
            fields = {"name" if kind == "folder" else "title"}
        denied(all(self.allowed(kind, op["id"], field, "write") for field in fields))

    def authorize_inverse(self, change):
        name, kind, record_id = change["type"], change["recordType"], change["id"]
        self.action(name)
        if name.endswith((".add", ".remove")):
            fields = FIELDS[kind]
        elif name == "note.edit":
            old, restored = change["before"], change["after"]
            fields = ({"content"} if old.get("sha256") != restored.get("sha256") else set()) | (
                {"paperIds"} if old.get("paperIds") != restored.get("paperIds") else set())
        elif name == "records.reorder":
            fields = {"order"}
        elif name in ("folder.addMembers", "folder.removeMembers", "folder.reorderMembers"):
            fields = {key for key in ("paperIds", "noteIds")
                      if (change["before"] or {}).get(key) or (change["after"] or {}).get(key)}
        else:
            fields = {"name" if kind == "folder" else "title"}
        denied(all(self.allowed(kind, record_id, field, "write") for field in fields))

    def project_values(self, kind, record_id, values):
        if values is None:
            return None
        result = {}
        for key, value in values.items():
            field = ("original" if kind == "paper" and key in ("size", "sha256", "mediaType") else
                     "content" if kind == "note" and key in ("size", "sha256", "text", "document", "record", "body") else
                     "order" if kind == "collection" and key in ("recordType", "recordIds") else key)
            if not self.allowed(kind, record_id, field):
                continue
            if field == "content" and not self.allowed(kind, record_id, "paperIds"):
                continue
            if key in ("paperIds", "noteIds") and not set(value) <= self.scope[key]:
                continue
            result[key] = copy.deepcopy(value)
        return result

    def project_changes(self, changes):
        result = []
        for original in changes:
            change = copy.deepcopy(original)
            kind = change.get("recordType") if change["type"] == "record.rename" else (
                "collection" if change["type"] in ("collection.rename", "records.reorder") else change["type"].split(".")[0])
            if "before" in change:
                change["before"] = self.project_values(kind, change["id"], change["before"])
                change["after"] = self.project_values(kind, change["id"], change["after"])
            else:
                for key in ("paperIds", "noteIds", "addedPaperIds", "removedPaperIds", "recordIds", "size", "sha256", "mediaType"):
                    field = "paperIds" if key in ("addedPaperIds", "removedPaperIds") else "order" if key == "recordIds" else "original" if key in ("size", "sha256", "mediaType") else key
                    if key in change and not self.allowed(kind, change["id"], field):
                        del change[key]
            result.append(change)
        return result

    def project_inspection(self, result, include_content):
        self.action("read")
        manifest = result["manifest"]
        denied(manifest["collection"]["id"] == self.value["collectionId"])
        projected = {key: result[key] for key in ("ok", "format", "formatVersion", "protection", "collectionId", "revision")}
        projected["fieldPolicyVersion"] = 1
        groups = {}
        for kind, key in fmt.RECORD_GROUPS.items():
            groups[key] = []
            for row in manifest[key]:
                if row["id"] not in self.scope[kind + "Ids"]:
                    continue
                fields = self.project_values(kind, row["id"], row)
                if not include_content:
                    fields = {k: v for k, v in fields.items() if k in ("size", "sha256", "mediaType")}
                if kind == "note" and not self.visible_refs(row, kind):
                    fields = {}  # No authored field or content-derived metadata through hidden references.
                elif kind == "folder" and not self.visible_refs(row, kind):
                    fields = {}
                groups[key].append({"id": row["id"], **fields})
            if not self.allowed("collection", result["collectionId"], "order"):
                groups[key].sort(key=lambda row: row["id"])
        projected.update(originals=groups["originals"], notes=groups["notes"], folderCount=len(groups["folders"]))
        if include_content:
            projected["manifest"] = {"collection": {"id": result["collectionId"], "revision": result["revision"],
                **self.project_values("collection", result["collectionId"], manifest["collection"])}, **groups}
            key = {1: "noteText", 2: "noteDocuments", 3: "noteRecords"}[result["formatVersion"]]
            projected[key] = {row["id"]: result[key][row["id"]] for row in sorted(manifest["notes"], key=lambda row: row["id"])
                              if row["id"] in self.scope["noteIds"] and self.content(row)}
        self.check()
        return projected
