"""Explicit three-way conflict detection and resolution for readable collections.

A named base snapshot, the other editor's snapshot ("theirs") and the caller's own work
("mine": a saved copy, or an operation request planned against that base) are compared
field by field. Nothing is merged automatically when both sides changed the same field.
A resolution request names one explicit choice for every reported conflict, is bound to
the exact inputs and reviewed report, and is published only as a new copy. The caller's
scope and optional field policy must independently permit every change made to "theirs".
"""
from __future__ import annotations

import copy
import hashlib
import os
import zipfile

import showpapers_format as fmt
import showpapers_undo as undo

CONFLICT_VERSION = 1
CHOICES = ("keepMine", "keepTheirs", "keepBoth")
KINDS = ("edit-vs-edit", "add-vs-add", "edit-vs-delete", "delete-vs-edit",
         "reference-vs-delete", "delete-vs-reference")
MAX_CONFLICTS = 1 + fmt.MAX_ORIGINALS + fmt.MAX_NOTES + fmt.MAX_FOLDERS
ORDER_FIELDS = {"paper": "paperOrder", "note": "noteOrder", "folder": "folderOrder"}
# Report field names mapped onto the closed field-policy vocabulary.
_POLICY_FIELD = {"paperOrder": "order", "noteOrder": "order", "folderOrder": "order"}
_RESOLUTION_FIELDS = {"resolutionVersion", "resolutionId", "collectionId", "formatVersion",
                      "baseRevision", "baseArchiveSha256", "theirsRevision", "theirsArchiveSha256",
                      "mineKind", "mineSha256", "constraintsSha256", "reportSha256", "resolutions"}


class _Side:
    """One validated snapshot. Cell values are comparable, never returned to the caller."""
    def __init__(self, manifest, payloads):
        self.manifest, self.payloads = manifest, payloads
        self.version = manifest["formatVersion"]
        self.rows = {kind: {row["id"]: row for row in manifest[group]} for kind, group in fmt.RECORD_GROUPS.items()}
        self.order = {kind: [row["id"] for row in manifest[group]] for kind, group in fmt.RECORD_GROUPS.items()}
        self.ids = {manifest["collection"]["id"]} | {i for rows in self.rows.values() for i in rows}
        self._content = {}

    def fields(self, kind):
        if kind == "paper":
            return ("title", "original")
        if kind == "folder":
            return ("name", "paperIds", "noteIds")
        # A rich document or native record carries its own references: one indivisible cell.
        return ("title", "content", "paperIds") if self.version == 1 else ("title", "content")

    def cell(self, kind, record_id, field):
        row = self.rows[kind][record_id]
        if field == "original":
            return [row["sha256"], row["size"], row["mediaType"]]
        if field != "content":
            return row[field]
        if self.version == 1:
            return row["sha256"]
        if record_id not in self._content:
            # Two writers may serialize one equal document differently; compare its JSON value.
            value = fmt.parse_json(self.payloads[row["path"]])
            self._content[record_id] = hashlib.sha256(fmt.json_bytes(value)).hexdigest()
        return [self._content[record_id], row["paperIds"]]

    def references(self, kind, record_id):
        """(referencing kind, id, field) rows of this side that point at the record."""
        found = []
        for note in (self.manifest["notes"] if kind == "paper" else []):
            if record_id in note["paperIds"]:
                found.append(("note", note["id"], "paperIds" if self.version == 1 else "content"))
        for folder in self.manifest["folders"]:
            if record_id in folder.get(kind + "Ids", []):
                found.append(("folder", folder["id"], kind + "Ids"))
        return found


def _reordered(base, mine, theirs):
    """(only mine reordered the members all three share, both reordered them differently)."""
    common = set(base) & set(mine) & set(theirs)
    shared = [[item for item in side if item in common] for side in (base, mine, theirs)]
    return (shared[1] != shared[0] and shared[2] == shared[0],
            shared[1] != shared[0] and shared[2] != shared[0] and shared[1] != shared[2])


def _sequence(base, mine, theirs, final, prefer=None):
    """Deterministic ordered merge of the decided members; a conflict needs an explicit preference."""
    only_mine, conflict = _reordered(base, mine, theirs)
    if prefer is None:
        prefer = "mine" if only_mine else "theirs"
    first, second = (mine, theirs) if prefer == "mine" else (theirs, mine)
    result = [item for item in first if item in final]
    result += [item for item in second if item in final and item not in set(first)]
    return result + sorted(set(final) - set(result)), conflict


def _members(base, mine, theirs):
    """Three-way set merge: start from theirs, then apply only what mine added or removed."""
    return (set(theirs) | (set(mine) - set(base))) - (set(base) - set(mine))


class _Merge:
    def __init__(self, base, mine, theirs):
        self.base, self.mine, self.theirs = base, mine, theirs
        self.collection = base.manifest["collection"]["id"]
        self.conflicts = {}   # (recordType, id) -> conflict
        self.clean = []       # mine's non-conflicting changes
        self._detect()

    def _changed(self, side, kind, record_id):
        return [field for field in side.fields(kind)
                if side.cell(kind, record_id, field) != self.base.cell(kind, record_id, field)]

    def _conflict(self, kind, record_type, record_id, fields, resolutions, dependents=()):
        self.conflicts[(record_type, record_id)] = {
            "kind": kind, "recordType": record_type, "id": record_id, "fields": list(fields),
            "resolutions": list(resolutions), "dependents": [dict(zip(("recordType", "id", "field"), item)) for item in dependents]}

    def _detect(self):
        b, m, t = self.base, self.mine, self.theirs
        kinds = {}
        for side in (b, m, t):
            for kind, rows in side.rows.items():
                for record_id in rows:
                    fmt.need(kinds.setdefault(record_id, kind) == kind and record_id != self.collection,
                             "duplicate", "One record ID has different types in the compared snapshots.")
        fields, clean = [], []
        if m.manifest["collection"]["title"] != t.manifest["collection"]["title"]:
            if b.manifest["collection"]["title"] not in (m.manifest["collection"]["title"], t.manifest["collection"]["title"]):
                fields.append("title")
            elif m.manifest["collection"]["title"] != b.manifest["collection"]["title"]:
                clean.append("title")
        deleted_by_theirs, edited_deleted = [], set()
        for kind in fmt.RECORD_GROUPS:
            only_mine, conflict = _reordered(b.order[kind], m.order[kind], t.order[kind])
            if conflict:
                fields.append(ORDER_FIELDS[kind])
            elif only_mine:
                clean.append(ORDER_FIELDS[kind])
            for record_id in sorted(set(b.rows[kind]) | set(m.rows[kind]) | set(t.rows[kind])):
                in_b, in_m, in_t = (record_id in side.rows[kind] for side in (b, m, t))
                if in_m and in_t:
                    self._both(kind, record_id, in_b)
                elif in_m and not in_b:
                    self.clean.append({"recordType": kind, "id": record_id, "change": "add", "fields": list(m.fields(kind))})
                elif in_b and in_t and not in_m:
                    changed = self._changed(t, kind, record_id)
                    added = [r for r in t.references(kind, record_id) if self._newly(t, r, record_id)]
                    if changed:
                        self._conflict("delete-vs-edit", kind, record_id, changed, ("keepMine", "keepTheirs"))
                    elif added:
                        # The other side's new content is never rewritten to honor my deletion.
                        self._conflict("delete-vs-reference", kind, record_id, [], ("keepTheirs",), added)
                    else:
                        self.clean.append({"recordType": kind, "id": record_id, "change": "remove", "fields": []})
                elif in_b and in_m and not in_t:
                    changed = self._changed(m, kind, record_id)
                    if changed:
                        edited_deleted.add((kind, record_id))
                        self._conflict("edit-vs-delete", kind, record_id, changed,
                                       ("keepMine", "keepTheirs") + (("keepBoth",) if kind == "note" else ()))
                    else:
                        deleted_by_theirs.append((kind, record_id))
        for kind, record_id in deleted_by_theirs:
            # A restored folder simply omits absent members; a restored Note cannot omit a reference.
            added = [r for r in m.references(kind, record_id)
                     if self._newly(m, r, record_id) or (r[0] == "note" and (r[0], r[1]) in edited_deleted)]
            if added:
                self._conflict("reference-vs-delete", kind, record_id, [], ("keepMine", "keepTheirs"), added)
        if fields:
            self._conflict("edit-vs-edit", "collection", self.collection, fields, ("keepMine", "keepTheirs"))
        if clean:
            self.clean.append({"recordType": "collection", "id": self.collection, "change": "edit", "fields": clean})
        fmt.need(len(self.conflicts) <= MAX_CONFLICTS, "limit", "Too many conflicts.")

    def _newly(self, side, reference, target):
        """Did this side itself introduce the reference (new record, or a newly pointing field)?"""
        kind, record_id, field = reference
        if record_id not in self.base.rows[kind]:
            return True
        if kind == "folder":
            return target not in self.base.rows[kind][record_id][field]
        previous = self.base.rows[kind][record_id]["paperIds"]
        return target not in previous or side.cell(kind, record_id, field) != self.base.cell(kind, record_id, field)

    def _both(self, kind, record_id, in_base):
        b, m, t = self.base, self.mine, self.theirs
        conflicted, clean = [], []
        for field in m.fields(kind):
            mine, theirs = m.cell(kind, record_id, field), t.cell(kind, record_id, field)
            if mine == theirs:
                continue
            if not in_base:
                conflicted.append(field)
                continue
            base = b.cell(kind, record_id, field)
            fmt.need(field != "original", "digest", "An original changed under an existing record ID.")
            if kind == "folder" and field != "name":
                if _reordered(base, mine, theirs)[1]:
                    conflicted.append(field)
                elif mine != base:
                    clean.append(field)
            elif base not in (mine, theirs):
                conflicted.append(field)
            elif mine != base:
                clean.append(field)
        if conflicted:
            authored = kind == "note" and bool({"content", "paperIds"} & set(conflicted))
            if not in_base and kind == "paper" and "original" in conflicted:
                choices = ("keepTheirs", "keepBoth")  # Never replace an original under its existing ID.
            elif not in_base and kind == "note" or authored:
                choices = CHOICES
            else:
                choices = ("keepMine", "keepTheirs")
            self._conflict("edit-vs-edit" if in_base else "add-vs-add", kind, record_id, conflicted, choices)
        if clean:
            self.clean.append({"recordType": kind, "id": record_id, "change": "edit", "fields": clean})

    # ---- construction -------------------------------------------------------------------

    def build(self, choices):
        """choices: {(recordType, id): (choice, copyId or None)} for every conflict."""
        b, m, t = self.base, self.mine, self.theirs
        manifest = copy.deepcopy(t.manifest)
        payloads = dict(t.payloads)
        rows = {kind: {row["id"]: row for row in manifest[group]} for kind, group in fmt.RECORD_GROUPS.items()}
        copies = {kind: [] for kind in fmt.RECORD_GROUPS}

        def choice(kind, record_id):
            return choices.get((kind, record_id), (None, None))[0]

        def take(kind, record_id, new_id=None):
            row = copy.deepcopy(m.rows[kind][record_id])
            if kind != "folder":
                data = m.payloads[row["path"]]
                if new_id is not None:
                    row["path"] = row["path"].replace(record_id, new_id)
                payloads[row["path"]] = data
            if new_id is not None:
                row["id"] = new_id
                copies[kind].append(new_id)
            rows[kind][row["id"]] = row

        def drop(kind, record_id):
            row = rows[kind].pop(record_id)
            if kind != "folder":
                del payloads[row["path"]]

        def set_field(kind, record_id, field):
            row, source = rows[kind][record_id], m.rows[kind][record_id]
            if field in ("content", "original"):
                del payloads[row["path"]]
                for key in ("path", "size", "sha256", "mediaType", "paperIds"):
                    if key in source and (key != "paperIds" or m.version >= 2):
                        row[key] = copy.deepcopy(source[key])
                payloads[row["path"]] = m.payloads[source["path"]]
            else:
                row[field] = copy.deepcopy(source[field])

        # My referencing Note edits are withheld only by an explicit keepTheirs on the deleted target.
        withheld, reported = set(), []
        for key, conflict in sorted(self.conflicts.items()):
            if conflict["kind"] == "reference-vs-delete" and choice(*key) == "keepTheirs":
                for d in conflict["dependents"]:
                    if (d["recordType"], d["id"]) in self.conflicts:
                        continue  # Its own explicit choice governs; a contradiction is refused below.
                    if d["recordType"] == "note":
                        withheld.add((d["id"], d["field"]))
                    added = d["recordType"] == "note" and d["id"] not in b.rows["note"]
                    entry = {"recordType": d["recordType"], "id": d["id"], "change": "add" if added else "edit",
                             "fields": list(m.fields("note")) if added else [d["field"]]}
                    if entry not in reported:
                        reported.append(entry)

        def clean_fields(kind, record_id):
            return next((c["fields"] for c in self.clean if (c["recordType"], c["id"]) == (kind, record_id)), [])

        if self._field_choice("collection", self.collection, "title", choices, clean_fields("collection", self.collection)):
            manifest["collection"]["title"] = m.manifest["collection"]["title"]
        preferred = {}
        for kind in fmt.RECORD_GROUPS:
            for record_id in sorted(set(m.rows[kind]) | set(t.rows[kind])):
                conflict = self.conflicts.get((kind, record_id))
                picked, copy_id = choices.get((kind, record_id), (None, None))
                in_m, in_t = record_id in m.rows[kind], record_id in t.rows[kind]
                if in_m and not in_t:
                    if conflict is None:
                        if record_id not in b.rows[kind] and not any(item[0] == record_id for item in withheld):
                            take(kind, record_id)
                    elif picked == "keepMine":
                        take(kind, record_id)
                    elif picked == "keepBoth":
                        take(kind, record_id, copy_id)
                elif in_t and not in_m:
                    if (conflict is None and record_id in b.rows[kind]) or picked == "keepMine":
                        drop(kind, record_id)
                elif in_m and in_t:
                    clean = [field for field in clean_fields(kind, record_id) if (record_id, field) not in withheld]
                    for field in m.fields(kind):
                        mine_wins = self._field_choice(kind, record_id, field, choices, clean)
                        if kind == "folder" and field != "name":
                            preferred[(record_id, field)] = (
                                ("mine" if picked == "keepMine" else "theirs") if conflict and field in conflict["fields"] else None)
                        elif mine_wins:
                            set_field(kind, record_id, field)
                    if picked == "keepBoth":
                        take(kind, record_id, copy_id)
        # Membership of a record absent from the result has no meaning; an explicit deletion removes it.
        existing = {kind: set(rows[kind]) for kind in fmt.RECORD_GROUPS}
        for record_id, row in rows["folder"].items():
            for target in ("paper", "note"):
                field = target + "Ids"
                if record_id in m.rows["folder"] and record_id in t.rows["folder"]:
                    base = b.rows["folder"][record_id][field] if record_id in b.rows["folder"] else []
                    mine, theirs = m.rows["folder"][record_id][field], t.rows["folder"][record_id][field]
                    side = preferred.get((record_id, field))
                    if record_id not in b.rows["folder"] and side is not None:
                        final = set(mine if side == "mine" else theirs)  # add-vs-add: one whole list.
                    else:
                        final = _members(base, mine, theirs)
                    row[field] = _sequence(base, mine, theirs, final & existing[target], side)[0]
                else:
                    row[field] = [item for item in row[field] if item in existing[target]]
        for kind, group in fmt.RECORD_GROUPS.items():
            side = None
            if ("collection", self.collection) in self.conflicts and ORDER_FIELDS[kind] in self.conflicts[("collection", self.collection)]["fields"]:
                side = "mine" if choice("collection", self.collection) == "keepMine" else "theirs"
            final = set(rows[kind]) - set(copies[kind])
            order = _sequence(b.order[kind], m.order[kind], t.order[kind], final, side)[0] + sorted(copies[kind])
            manifest[group] = [rows[kind][record_id] for record_id in order]
        manifest["collection"]["revision"] = max(m.manifest["collection"]["revision"], t.manifest["collection"]["revision"]) + 1
        fmt._validate_snapshot(manifest, payloads)
        for row in manifest["originals"]:
            # No resolution may replace an original's bytes under an ID either side already holds.
            for side in (t, m, b):
                if row["id"] in side.rows["paper"]:
                    fmt.need(side.rows["paper"][row["id"]]["sha256"] == row["sha256"], "digest",
                             "A resolution cannot replace an existing original.")
                    break
        return manifest, payloads, reported

    def _field_choice(self, kind, record_id, field, choices, clean):
        conflict = self.conflicts.get((kind, record_id))
        if conflict is not None and field in conflict["fields"]:
            return choices[(kind, record_id)][0] == "keepMine"
        return field in clean


def _archive(path):
    payloads = {}
    with undo._regular(path) as stream:
        identity = fmt._source_identity(stream)
        digest = fmt._archive_digest(stream)
        manifest, _ = fmt._read_archive_stream(stream, payloads=payloads)
        fmt.need(fmt._source_identity(stream) == identity and fmt._archive_digest(stream) == digest,
                 "source", "A compared snapshot changed while being read.")
    def check():
        with undo._regular(path) as current:
            fmt.need(fmt._source_identity(current) == identity and fmt._archive_digest(current) == digest,
                     "source", "A compared snapshot changed during the operation.")
    return manifest, payloads, digest, check


def _resolution(value):
    fmt.need(isinstance(value, dict) and type(value.get("resolutionVersion")) is int and
             value["resolutionVersion"] == CONFLICT_VERSION, "version", "This resolution-request version is not supported.")
    fmt.keys(value, _RESOLUTION_FIELDS)
    fmt.need(type(value["formatVersion"]) is int and value["formatVersion"] in fmt.SUPPORTED_FORMAT_VERSIONS,
             "version", "Expected a supported archive format version.")
    fmt.identifier(value["resolutionId"]); fmt.identifier(value["collectionId"])
    fmt.integer(value["baseRevision"], 1, fmt.MAX_REVISION - 1)
    fmt.integer(value["theirsRevision"], 1, fmt.MAX_REVISION - 1)
    fmt.need(isinstance(value["mineKind"], str) and value["mineKind"] in ("archive", "request"),
             "resolution", "Expected a supported description of the caller's own work.")
    for name in ("baseArchiveSha256", "theirsArchiveSha256", "mineSha256", "constraintsSha256", "reportSha256"):
        fmt.need(isinstance(value[name], str) and fmt.HEX.fullmatch(value[name]) is not None,
                 "digest", "Expected canonical resolution input digests.")
    rows = value["resolutions"]
    fmt.need(isinstance(rows, list) and len(rows) <= MAX_CONFLICTS, "resolution", "Invalid resolution list.")
    seen, copies = set(), set()
    for row in rows:
        fmt.need(isinstance(row, dict) and {"conflictId", "choice"} <= set(row) <= {"conflictId", "choice", "copyId"},
                 "resolution", "Invalid resolution entry.")
        fmt.need(isinstance(row["conflictId"], str) and fmt.HEX.fullmatch(row["conflictId"]) is not None and
                 row["conflictId"] not in seen, "resolution", "Each conflict needs exactly one resolution.")
        seen.add(row["conflictId"])
        fmt.need(isinstance(row["choice"], str) and row["choice"] in CHOICES, "resolution", "Unknown resolution choice.")
        fmt.need(("copyId" in row) == (row["choice"] == "keepBoth"), "resolution", "Only a kept copy names a new record ID.")
        if "copyId" in row:
            fmt.identifier(row["copyId"])
            fmt.need(row["copyId"] not in copies, "duplicate", "A new record must have an unused ID.")
            copies.add(row["copyId"])
    return value


def _values(side, kind, record_id, constraints):
    if kind == "collection":
        # An order is shown only when every record of that type is inside the caller's scope.
        return {"title": side.manifest["collection"]["title"],
                **{name: list(side.order[k]) for k, name in ORDER_FIELDS.items() if set(side.order[k]) <= constraints[k + "Ids"]}}
    row = side.rows[kind].get(record_id)
    if row is None:
        return None
    result = {key: copy.deepcopy(row[key]) for key in ("size", "sha256", "mediaType", "paperIds", "noteIds") if key in row}
    result["name" if kind == "folder" else "title"] = row["name" if kind == "folder" else "title"]
    if kind == "note":
        raw = side.payloads[row["path"]]
        result[{1: "text", 2: "document", 3: "record"}[side.version]] = fmt.note_text(raw) if side.version == 1 else fmt.parse_json(raw)
    return result


def process_conflicts(base_path, theirs_path, constraints_path, *, mine_archive=None, mine_request=None,
                      resolution_path=None, destination=None, sources_path=None, include_content=False,
                      field_policy=None):
    """Report conflicts, or with a resolution request validate and optionally publish a new merged copy."""
    try:
        fmt.need(type(include_content) is bool, "arguments", "include_content must be a boolean.")
        fmt.need((mine_archive is None) != (mine_request is None), "arguments",
                 "Name exactly one of the caller's saved copy or its operation request.")
        fmt.need(destination is None or resolution_path is not None, "arguments", "Saving requires a resolution request.")
        fmt.need(sources_path is None or mine_request is not None, "arguments", "Sources accompany an operation request.")
        grant, grant_digest, check_grant = undo._json_input(constraints_path)
        version = grant.get("scopeVersion") if isinstance(grant, dict) else None
        fmt.need(type(version) is int and version in (1, 2), "scope", "Explicit versioned scope constraints are required.")
        constraints = fmt._operation_constraints(grant, version)
        base_manifest, base_payloads, base_digest, check_base = _archive(base_path)
        theirs_manifest, theirs_payloads, theirs_digest, check_theirs = _archive(theirs_path)
        if mine_archive is not None:
            mine_manifest, mine_payloads, mine_digest, check_mine = _archive(mine_archive)
            kind = "archive"
        else:
            _, mine_digest, check_request = undo._json_input(mine_request)
            mine_manifest, mine_payloads, _, check_plan = fmt._plan_operations(
                base_path, mine_request, constraints_path, sources_path=sources_path, field_policy=field_policy)
            kind = "request"
            def check_mine():
                check_request(); check_plan()
        collection = base_manifest["collection"]
        fmt.need(collection["id"] == constraints["collectionId"], "scope", "The collection is outside the supplied scope.")
        for manifest in (mine_manifest, theirs_manifest):
            fmt.need(manifest["collection"]["id"] == collection["id"], "scope", "The compared snapshots belong to different collections.")
            fmt.need(manifest["formatVersion"] == base_manifest["formatVersion"], "version", "Conflict handling cannot change archive formats.")
            fmt.need(manifest["collection"]["revision"] >= collection["revision"], "revision",
                     "The named base revision is newer than a compared snapshot.")
        fmt.need(max(mine_manifest["collection"]["revision"], theirs_manifest["collection"]["revision"]) < fmt.MAX_REVISION,
                 "revision", "The collection revision cannot be incremented safely.")
        merge = _Merge(_Side(base_manifest, base_payloads), _Side(mine_manifest, mine_payloads),
                       _Side(theirs_manifest, theirs_payloads))

        def in_scope(record_type, record_id):
            return record_type == "collection" or record_id in constraints[record_type + "Ids"]

        def readable(record_type, record_id, fields):
            if field_policy is None:
                return list(fields)
            return [f for f in fields if field_policy.allowed(record_type, record_id, _POLICY_FIELD.get(f, f))]

        identity = {"conflictVersion": CONFLICT_VERSION, "collectionId": collection["id"],
                    "formatVersion": base_manifest["formatVersion"],
                    "base": {"revision": collection["revision"], "archiveSha256": base_digest},
                    "theirs": {"revision": theirs_manifest["collection"]["revision"], "archiveSha256": theirs_digest},
                    "mine": {"kind": kind, "revision": mine_manifest["collection"]["revision"], "sha256": mine_digest}}
        visible, keys_by_id, hidden = [], {}, 0
        for key in sorted(merge.conflicts):
            conflict = merge.conflicts[key]
            if not in_scope(*key):
                hidden += 1
                continue
            dependents = [d for d in conflict["dependents"] if in_scope(d["recordType"], d["id"])]
            entry = {"kind": conflict["kind"], "recordType": key[0], "id": key[1],
                     "fields": readable(key[0], key[1], conflict["fields"]), "resolutions": conflict["resolutions"],
                     "dependents": [{"recordType": d["recordType"], "id": d["id"]} for d in dependents],
                     "hiddenDependentCount": len(conflict["dependents"]) - len(dependents)}
            entry = {"conflictId": hashlib.sha256(fmt.json_bytes([identity, entry])).hexdigest(), **entry}
            keys_by_id[entry["conflictId"]] = key
            visible.append(entry)
        mergeable = [{**change, "fields": readable(change["recordType"], change["id"], change["fields"])}
                     for change in merge.clean if in_scope(change["recordType"], change["id"])]
        report = {**identity, "conflicts": visible, "conflictCount": len(visible), "hiddenConflictCount": hidden,
                  "mergeable": mergeable, "hiddenChangeCount": len(merge.clean) - len(mergeable)}
        report_digest = hashlib.sha256(fmt.json_bytes(report)).hexdigest()

        def check_inputs():
            check_grant(); check_base(); check_theirs(); check_mine()
            if field_policy is not None:
                field_policy.check()

        if resolution_path is None:
            if field_policy is not None:
                field_policy.action("read")
            if include_content:
                for entry in visible:
                    values = {}
                    for name, side in (("base", merge.base), ("mine", merge.mine), ("theirs", merge.theirs)):
                        value = _values(side, entry["recordType"], entry["id"], constraints)
                        if value is not None and entry["recordType"] != "collection":
                            # No authored value is revealed through a reference outside the scope.
                            fmt._require_scope(constraints, "paper", value.get("paperIds", []))
                            fmt._require_scope(constraints, "note", value.get("noteIds", []))
                        if value is not None and field_policy is not None:
                            row = side.rows.get(entry["recordType"], {}).get(entry["id"])
                            if row is not None and not field_policy.visible_refs(row, entry["recordType"]):
                                value = {}
                            value = field_policy.project_values(entry["recordType"], entry["id"], value)
                        values[name] = value
                    entry["values"] = values
                fmt.need(len(fmt.json_bytes(visible)) <= undo.MAX_REVIEW_BYTES, "limit", "The requested content review exceeds its bound.")
            check_inputs()
            return {"ok": True, **report, "reportSha256": report_digest}

        raw_resolution, _, check_resolution = undo._json_input(resolution_path)
        request = _resolution(raw_resolution)
        fmt.need(request["collectionId"] == collection["id"], "scope", "The resolution targets a different collection.")
        fmt.need(request["formatVersion"] == base_manifest["formatVersion"], "version", "Conflict handling cannot change archive formats.")
        fmt.need(request["baseRevision"] == identity["base"]["revision"] and
                 request["theirsRevision"] == identity["theirs"]["revision"], "revision",
                 "A compared revision does not match the resolution request.")
        fmt.need(request["baseArchiveSha256"] == base_digest and request["theirsArchiveSha256"] == theirs_digest and
                 request["mineKind"] == kind and request["mineSha256"] == mine_digest and
                 request["constraintsSha256"] == grant_digest, "digest",
                 "A compared input does not match the resolution request.")
        fmt.need(request["reportSha256"] == report_digest, "conflict", "The reviewed conflict report no longer matches these inputs.")
        fmt.need(hidden == 0, "scope", "A conflict is outside the supplied record scope.")
        choices = {}
        used = merge.base.ids | merge.mine.ids | merge.theirs.ids
        for row in request["resolutions"]:
            fmt.need(row["conflictId"] in keys_by_id, "resolution", "A resolution names an unknown conflict.")
            key = keys_by_id[row["conflictId"]]
            fmt.need(row["choice"] in merge.conflicts[key]["resolutions"], "resolution", "That choice is not available for this conflict.")
            if "copyId" in row:
                fmt.need(row["copyId"] not in used, "duplicate", "A new record must have an unused ID.")
            choices[key] = (row["choice"], row.get("copyId"))
        fmt.need(set(choices) == set(merge.conflicts), "unresolved", "Every reported conflict needs an explicit resolution.")
        manifest, payloads, withheld = merge.build(choices)
        shown = [{**item, "fields": readable(item["recordType"], item["id"], item["fields"])}
                 for item in withheld if in_scope(item["recordType"], item["id"])]
        unchanged = copy.deepcopy(manifest)
        unchanged["collection"]["revision"] = theirs_manifest["collection"]["revision"]
        no_change = unchanged == theirs_manifest
        # The result is a change to their snapshot: each derived action needs its own authority.
        changes = [] if no_change else undo.snapshot_changes(
            theirs_manifest, manifest, theirs_payloads, payloads, constraints, False, field_policy=field_policy)
        check_inputs(); check_resolution()
        result = {"ok": True, "validated": True, "applied": False, **identity, "resolutionId": request["resolutionId"],
                  "reportSha256": report_digest, "fromRevision": identity["theirs"]["revision"],
                  "toRevision": identity["theirs"]["revision"] if no_change else manifest["collection"]["revision"],
                  "resolved": [dict(row) for row in sorted(request["resolutions"], key=lambda row: row["conflictId"])],
                  "mergedChangeCount": len(merge.clean), "withheld": shown,
                  "hiddenWithheldCount": len(withheld) - len(shown), "noChange": no_change, "changes": changes}
        if destination is None or no_change:
            return result
        try:
            os.stat(destination, follow_symlinks=False)
            exists = True
        except FileNotFoundError:
            exists = False
        if exists:
            # An exact retry acknowledges the saved copy; anything else is never replaced.
            try:
                saved, _, saved_digest, _ = _archive(destination)
            except (fmt.FormatError, OSError):
                saved = None
            fmt.need(saved == manifest, "exists", "The output already exists; choose a new file.")
            return {**result, "applied": True, "alreadyApplied": True, "resultArchiveSha256": saved_digest}
        def write_payloads(archive):
            for row in manifest["originals"] + manifest["notes"]:
                fmt._write_entry(archive, row["path"], payloads[row["path"]])
            return manifest
        warnings = []
        fmt._publish_archive(destination, write_payloads, warnings,
                             before_publish=lambda: (check_inputs(), check_resolution()))
        result.update(applied=True, alreadyApplied=False, resultArchiveSha256=_archive(destination)[2])
        if warnings:
            result["warnings"] = warnings
        return result
    except fmt.FormatError:
        raise
    except (OSError, ValueError, TypeError, KeyError, RecursionError, zipfile.BadZipFile):
        raise fmt.FormatError("conflict", "Conflict handling could not be completed; existing files were not changed.") from None
