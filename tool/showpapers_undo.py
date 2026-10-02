"""One-step conditional saved-copy Undo. No caller-authored inverse or persisted global authority."""
from __future__ import annotations

from contextlib import contextmanager
import copy
import hashlib
import os
import stat
import zipfile

import showpapers_format as fmt

UNDO_VERSION = 1
MAX_REVIEW_BYTES = 4 * 1024 * 1024
_FIELDS = {"undoVersion", "undoId", "collectionId", "formatVersion", "beforeRevision",
           "beforeArchiveSha256", "expectedRevision", "expectedArchiveSha256",
           "forwardRequestSha256", "constraintsSha256"}


@contextmanager
def _regular(path):
    fmt.need(hasattr(os, "O_NOFOLLOW"), "platform", "Safe Undo requires no-follow file admission.")
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_NONBLOCK", 0))
    try:
        fmt.need(stat.S_ISREG(os.fstat(fd).st_mode), "path", "Expected a regular Undo input.")
        stream = os.fdopen(fd, "rb")
        fd = None
        with stream:
            yield stream
    finally:
        if fd is not None:
            os.close(fd)


def _json_input(path):
    with _regular(path) as stream:
        identity = fmt._source_identity(stream)
        raw = stream.read(fmt.MAX_MANIFEST_BYTES + 1)
        value = fmt.parse_json(raw)
        fmt.need(fmt._source_identity(stream) == identity, "source", "An Undo input changed while being read.")
    digest = hashlib.sha256(raw).hexdigest()
    def check():
        with _regular(path) as current:
            data = current.read(fmt.MAX_MANIFEST_BYTES + 1)
            fmt.need(fmt._source_identity(current) == identity and len(data) <= fmt.MAX_MANIFEST_BYTES and
                     hashlib.sha256(data).hexdigest() == digest,
                     "source", "An Undo input changed during the operation.")
    return value, digest, check


def _archive_input(path, expected_digest, retain=False):
    payloads = {} if retain else None
    with _regular(path) as stream:
        identity = fmt._source_identity(stream)
        digest = fmt._archive_digest(stream)
        fmt.need(expected_digest is None or digest == expected_digest, "digest", "The Undo snapshot does not match its expected digest.")
        manifest, _ = fmt._read_archive_stream(stream, payloads=payloads)
        fmt.need(fmt._source_identity(stream) == identity and fmt._archive_digest(stream) == digest,
                 "source", "An Undo snapshot changed while being read.")
    def check():
        with _regular(path) as current:
            fmt.need(fmt._source_identity(current) == identity and fmt._archive_digest(current) == digest,
                     "source", "An Undo snapshot changed during the operation.")
    return manifest, payloads, check


def _request(value):
    fmt.keys(value, _FIELDS)
    fmt.need(type(value["undoVersion"]) is int and value["undoVersion"] == UNDO_VERSION,
             "version", "This Undo-request version is not supported.")
    fmt.need(type(value["formatVersion"]) is int and value["formatVersion"] in fmt.SUPPORTED_FORMAT_VERSIONS,
             "version", "Expected a supported archive format version.")
    fmt.identifier(value["undoId"])
    fmt.identifier(value["collectionId"])
    fmt.integer(value["beforeRevision"], 1, fmt.MAX_REVISION - 2)
    fmt.integer(value["expectedRevision"], 2, fmt.MAX_REVISION - 1)
    fmt.need(value["expectedRevision"] == value["beforeRevision"] + 1,
             "revision", "Undo requires the immediate saved result of the original operation.")
    for name in ("beforeArchiveSha256", "expectedArchiveSha256", "forwardRequestSha256", "constraintsSha256"):
        fmt.need(isinstance(value[name], str) and fmt.HEX.fullmatch(value[name]) is not None,
                 "digest", "Expected canonical Undo input digests.")
    return value


def snapshot_changes(current, before, current_payloads, before_payloads, constraints, include_content, *, require_actions=True, field_policy=None):
    """Review a net source→target delta; Undo additionally checks derived inverse rights."""
    changes = []
    version = before["formatVersion"]
    def action(name, kind, record_id, old, restored):
        if require_actions:
            fmt.need(name in constraints["actions"], "scope", "An inverse action is outside the supplied scope.")
        if kind != "collection":
            fmt._require_scope(constraints, kind, [record_id])
        value = {"type": name, "recordType": kind, "id": record_id, "before": old, "after": restored}
        if field_policy is not None:
            if require_actions:
                field_policy.authorize_inverse(value)
            value = field_policy.project_changes([value])[0]
        changes.append(value)
    def refs(row, kind):
        if row is None:
            return
        for target in (("paper", "note") if kind == "folder" else ("paper",) if kind == "note" else ()):
            fmt._require_scope(constraints, target, row[target + "Ids"])
    def summary(row, kind, payloads):
        if row is None:
            return None
        result = {key: copy.deepcopy(row[key]) for key in ("size", "sha256", "mediaType", "paperIds", "noteIds") if key in row}
        if include_content:
            result["name" if kind == "folder" else "title"] = row["name" if kind == "folder" else "title"]
            if kind == "note":
                raw = payloads[row["path"]]
                result[{1: "text", 2: "document", 3: "record"}[version]] = (
                    fmt.note_text(raw) if version == 1 else fmt.parse_json(raw))
        return result

    if before["collection"]["title"] != current["collection"]["title"]:
        action("collection.rename", "collection", before["collection"]["id"],
               {"title": current["collection"]["title"]} if include_content else {},
               {"title": before["collection"]["title"]} if include_content else {})
    for kind, group in fmt.RECORD_GROUPS.items():
        original = {row["id"]: row for row in before[group]}
        active = {row["id"]: row for row in current[group]}
        for record_id in sorted(active.keys() - original.keys()):
            refs(active[record_id], kind)
            action(kind + ".remove", kind, record_id, summary(active[record_id], kind, current_payloads), None)
        for record_id in sorted(original.keys() - active.keys()):
            refs(original[record_id], kind)
            # This exact before-snapshot is the sole source for restoration; no new source or ID choice.
            action(kind + ".add", kind, record_id, None, summary(original[record_id], kind, before_payloads))
        for record_id in sorted(original.keys() & active.keys()):
            old, new = active[record_id], original[record_id]
            label = "name" if kind == "folder" else "title"
            if old[label] != new[label]:
                if include_content:
                    refs(old, kind); refs(new, kind)
                action("folder.rename" if kind == "folder" else "record.rename", kind, record_id,
                       {label: old[label]} if include_content else {}, {label: new[label]} if include_content else {})
            if kind == "note" and (old["sha256"] != new["sha256"] or old["paperIds"] != new["paperIds"]):
                refs(old, kind); refs(new, kind)
                action("note.edit", kind, record_id, summary(old, kind, current_payloads), summary(new, kind, before_payloads))
            if kind == "folder":
                removed, added = {}, {}
                reordered = False
                for target in ("paper", "note"):
                    field = target + "Ids"
                    old_ids, new_ids = set(old[field]), set(new[field])
                    removed[field], added[field] = sorted(old_ids - new_ids), sorted(new_ids - old_ids)
                    fmt._require_scope(constraints, target, removed[field] + added[field])
                    reordered |= ([i for i in old[field] if i in new_ids] != [i for i in new[field] if i in old_ids])
                if any(removed.values()):
                    action("folder.removeMembers", kind, record_id, removed, {field: [] for field in removed})
                if any(added.values()):
                    action("folder.addMembers", kind, record_id, {field: [] for field in added}, added)
                if reordered:
                    refs(old, kind); refs(new, kind)
                    old_order = {key: old[key] for key in ("paperIds", "noteIds")}
                    new_order = {key: new[key] for key in ("paperIds", "noteIds")}
                    if "folder.reorderMembers" in constraints["actions"]:
                        action("folder.reorderMembers", kind, record_id, old_order, new_order)
                    else:
                        # Older scopes have no reorder action: explicit remove+add authority is needed.
                        action("folder.removeMembers", kind, record_id, old_order, {"paperIds": [], "noteIds": []})
                        action("folder.addMembers", kind, record_id, {"paperIds": [], "noteIds": []}, new_order)
        common = original.keys() & active.keys()
        old_order = [row["id"] for row in current[group] if row["id"] in common]
        new_order = [row["id"] for row in before[group] if row["id"] in common]
        if old_order != new_order:
            fmt._require_scope(constraints, kind, original.keys() | active.keys())
            action("records.reorder", "collection", before["collection"]["id"],
                   {"recordType": kind, "recordIds": [row["id"] for row in current[group]]},
                   {"recordType": kind, "recordIds": [row["id"] for row in before[group]]})
    fmt.need(changes, "no_change", "There is no saved change to undo.")
    if include_content:
        fmt.need(len(fmt.json_bytes(changes)) <= MAX_REVIEW_BYTES, "limit", "The requested content review exceeds its bound.")
    return changes


def process_undo(before_archive_path, current_archive_path, operations_path, constraints_path, undo_path,
                 destination=None, *, sources_path=None, include_content=False, field_policy=None):
    """Authenticate both local snapshots, recompute the forward result, and publish only a new copy."""
    try:
        fmt.need(type(include_content) is bool, "arguments", "include_content must be a boolean.")
        raw_request, _, check_undo = _json_input(undo_path)
        request = _request(raw_request)
        forward, request_digest, check_forward = _json_input(operations_path)
        grant, grant_digest, check_grant = _json_input(constraints_path)
        fmt.need(request_digest == request["forwardRequestSha256"] and grant_digest == request["constraintsSha256"],
                 "digest", "The original request or scope does not match the Undo receipt.")
        before, before_payloads, check_before = _archive_input(before_archive_path, request["beforeArchiveSha256"], retain=True)
        current, _, check_current = _archive_input(current_archive_path, request["expectedArchiveSha256"])
        for manifest, revision in ((before, request["beforeRevision"]), (current, request["expectedRevision"])):
            fmt.need(manifest["formatVersion"] == request["formatVersion"], "version", "Undo cannot change archive formats.")
            fmt.need(manifest["collection"]["id"] == request["collectionId"], "scope", "Undo inputs belong to different collections.")
            fmt.need(manifest["collection"]["revision"] == revision, "revision", "The Undo snapshot revision changed.")
        planned, planned_payloads, forward_summary, check_sources = fmt._plan_operations(
            before_archive_path, operations_path, constraints_path, sources_path=sources_path, field_policy=field_policy)
        # Validation of the actual current archive verifies every payload against these exact hashes.
        fmt.need(current == planned, "conflict", "The current collection is not the exact saved forward result.")
        constraints = fmt._operation_constraints(grant, 2 if forward["protocolVersion"] == 4 else 1)
        changes = snapshot_changes(current, before, planned_payloads, before_payloads, constraints, include_content, field_policy=field_policy)
        del planned_payloads
        restored = copy.deepcopy(before)
        restored["collection"]["revision"] = request["expectedRevision"] + 1
        fmt._validate_snapshot(restored, before_payloads)
        def check_inputs():
            check_undo(); check_forward(); check_grant(); check_before(); check_current(); check_sources()
            if field_policy is not None:
                field_policy.check()
        check_inputs()
        result = {"ok": True, "validated": True, "applied": False, "undoVersion": UNDO_VERSION,
                  "undoId": request["undoId"], "operationId": forward_summary["operationId"],
                  "protocolVersion": forward_summary["protocolVersion"], "formatVersion": request["formatVersion"],
                  "collectionId": request["collectionId"], "fromRevision": request["expectedRevision"],
                  "toRevision": restored["collection"]["revision"], "inputArchiveSha256": request["expectedArchiveSha256"],
                  "restoredFromArchiveSha256": request["beforeArchiveSha256"], "changes": changes}
        if destination is not None:
            def write_payloads(archive):
                for row in restored["originals"] + restored["notes"]:
                    fmt._write_entry(archive, row["path"], before_payloads[row["path"]])
                return restored
            warnings = []
            fmt._publish_archive(destination, write_payloads, warnings, before_publish=check_inputs)
            result["applied"] = True
            if warnings:
                result["warnings"] = warnings
        return result
    except fmt.FormatError:
        raise
    except (OSError, ValueError, TypeError, KeyError, RecursionError, zipfile.BadZipFile):
        raise fmt.FormatError("undo", "Undo could not be completed; existing files were not changed.") from None


def preview_with_content(archive_path, operations_path, constraints_path, *, sources_path=None, field_policy=None):
    """Read-only, explicit authored before/after review of the validated forward delta."""
    try:
        forward, _, check_forward = _json_input(operations_path)
        grant, _, check_grant = _json_input(constraints_path)
        before, before_payloads, check_before = _archive_input(archive_path, None, retain=True)
        planned, planned_payloads, summary, check_sources = fmt._plan_operations(
            archive_path, operations_path, constraints_path, sources_path=sources_path, field_policy=field_policy)
        constraints = fmt._operation_constraints(grant, 2 if forward["protocolVersion"] == 4 else 1)
        summary["reviewChanges"] = snapshot_changes(
            before, planned, before_payloads, planned_payloads, constraints, True, require_actions=False, field_policy=field_policy)
        check_forward(); check_grant(); check_before(); check_sources()
        return summary
    except fmt.FormatError:
        raise
    except (OSError, ValueError, TypeError, KeyError, RecursionError, zipfile.BadZipFile):
        raise fmt.FormatError("operation", "The content preview could not be completed; existing files were not changed.") from None
