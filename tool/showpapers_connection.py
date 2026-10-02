"""Closed connection grant for ONE session, created by a trusted host. Never take it from an AI request.

The grant names the selected records, the classes of content that may be read (titles, text,
originals, links), six separate action permissions, the declared destination of the shared content,
an absolute expiry and a maximum idle time. This module validates the document and DERIVES the
existing scope-constraints v2 and field-policy v1 documents from it, so a connection session is
enforced by the same strict planner as every other caller. It supplies no signature, no
authentication and no phone connection: the host that writes the file is the authority.
"""
from __future__ import annotations

import copy

import showpapers_format as fmt
import showpapers_grants as grants

GRANT_VERSION = 1
SCHEMA_ID = "urn:showpapers:connection-grant:1"
PERMISSIONS = ("read", "add", "edit", "organize", "delete", "export")
READ_CLASSES = ("titles", "text", "originals", "links")
DESTINATIONS = ("local-only", "cloud-ai")
RECORD_KINDS = ("paper", "note", "folder")
MAX_LABEL = 80
MAX_LIFETIME_SECONDS = 24 * 60 * 60
MAX_IDLE_SECONDS = 60 * 60
MAX_CLOCK_SKEW_SECONDS = 300
MAX_EPOCH_SECONDS = 253402300799  # 9999-12-31T23:59:59Z

# Every permission listed for an action is required; nothing is implied by another permission.
ACTION_PERMISSIONS = {
    "paper.add": ("add",),
    "note.add": ("add",),
    "folder.add": ("add", "organize"),
    "note.edit": ("edit",),
    "record.rename": ("edit",),
    "collection.rename": ("edit",),
    "folder.rename": ("organize",),
    "folder.addMembers": ("organize",),
    "folder.removeMembers": ("organize",),
    "folder.reorderMembers": ("organize",),
    "records.reorder": ("organize",),
    "paper.remove": ("delete",),
    "note.remove": ("delete",),
    "folder.remove": ("delete", "organize"),
}
# The action a conditional Undo derives for each forward action; it needs its own permissions.
INVERSE_ACTIONS = {
    "paper.add": "paper.remove", "paper.remove": "paper.add", "note.add": "note.remove", "note.remove": "note.add",
    "folder.add": "folder.remove", "folder.remove": "folder.add", "note.edit": "note.edit",
    "record.rename": "record.rename", "collection.rename": "collection.rename", "folder.rename": "folder.rename",
    "folder.addMembers": "folder.removeMembers", "folder.removeMembers": "folder.addMembers",
    "folder.reorderMembers": "folder.reorderMembers", "records.reorder": "records.reorder",
}
# Read class -> record kind -> field-policy fields. Text needs links: native Note payloads carry them.
READ_FIELDS = {
    "titles": {"collection": ("title",), "paper": ("title",), "note": ("title",), "folder": ("name",)},
    "text": {"note": ("content",)},
    "originals": {"paper": ("original",)},
    "links": {"collection": ("order",), "note": ("paperIds",), "folder": ("paperIds", "noteIds")},
}
# Permission -> record kind -> fields of an EXISTING selected record that the permission may change.
WRITE_FIELDS = {
    "edit": {"collection": ("title",), "paper": ("title",), "note": ("title", "content", "paperIds")},
    "organize": {"collection": ("order",), "folder": ("name", "paperIds", "noteIds")},
}
# Closed error categories a connection session adds to the adapter's existing ones.
ERROR_CODES = (
    "approval", "connection_expired", "connection_field", "connection_grant", "connection_idle",
    "connection_replayed", "connection_revoked", "connection_scope", "connection_stopped",
    "connection_tool", "connection_unavailable",
    "permission_add", "permission_delete", "permission_edit", "permission_export",
    "permission_organize", "permission_read",
)
END_REASONS = {"stopped": "connection_stopped", "revoked": "connection_revoked",
               "authority-changed": "connection_revoked", "expired": "connection_expired",
               "idle": "connection_idle", "disconnected": "connection_stopped"}
_FIELDS = ("grantVersion", "grantId", "collectionId", "client", "sharing", "permissions", "read",
           "records", "newRecords", "issuedAt", "expiresAt", "maxIdleSeconds")


def refused(ok, message="The connection grant is invalid."):
    fmt.need(ok, "connection_grant", message)


def _label(value):
    refused(type(value) is str and 0 < len(value) <= MAX_LABEL and value.strip() == value and
            not any(ord(c) < 32 or 127 <= ord(c) <= 159 or 0xD800 <= ord(c) <= 0xDFFF for c in value),
            "Connection labels must be short trimmed text without control characters.")


def _flags(value, names):
    refused(type(value) is dict and set(value) == set(names) and all(type(value[name]) is bool for name in names),
            "Every permission and content class must be stated explicitly as true or false.")


def _ids(value, maximum):
    refused(type(value) is list and len(value) <= maximum and all(type(item) is str for item in value))
    for item in value:
        try:
            fmt.identifier(item)
        except fmt.FormatError:
            refused(False, "Connection grants need canonical lowercase record IDs.")
    refused(len(value) == len(set(value)), "A connection grant lists each record once.")
    return value


def validate(value):
    """Validate the closed document shape and every cross-field rule. Time is checked by `check_time`."""
    refused(type(value) is dict and type(value.get("grantVersion")) is int, "A versioned connection grant is required.")
    fmt.need(value["grantVersion"] == GRANT_VERSION, "version", "This connection grant version is not supported.")
    refused(set(value) == set(_FIELDS))
    for key in ("grantId", "collectionId"):
        _ids([value[key]] if type(value[key]) is str else None, 1)
    refused(type(value["client"]) is dict and set(value["client"]) == {"label"})
    _label(value["client"]["label"])

    sharing = value["sharing"]
    refused(type(sharing) is dict and set(sharing) == {"destination", "acknowledgedDestination", "serviceLabel"})
    refused(type(sharing["destination"]) is str and sharing["destination"] in DESTINATIONS,
            "State whether this session is local-only or a cloud AI session.")
    refused(sharing["acknowledgedDestination"] == sharing["destination"],
            "The person must acknowledge the exact destination of the shared content.")
    if sharing["destination"] == "cloud-ai":
        _label(sharing["serviceLabel"])
    else:
        refused(sharing["serviceLabel"] is None, "A local-only session names no cloud service.")

    _flags(value["permissions"], PERMISSIONS)
    _flags(value["read"], READ_CLASSES)
    permissions, read = value["permissions"], value["read"]
    refused(any(permissions.values()), "A connection grant must allow something.")
    refused(permissions["read"] or not any(read.values()), "Content classes need the read permission.")
    refused(read["links"] or not read["text"], "Note text carries its Paper links; share links with text.")

    seen = {value["collectionId"]}
    for key, extra in (("records", ()), ("newRecords", ("sourceIds",))):
        group = value[key]
        refused(type(group) is dict and set(group) == {kind + "Ids" for kind in RECORD_KINDS} | set(extra))
        for kind in RECORD_KINDS:
            ids = set(_ids(group[kind + "Ids"], fmt.MAX_SCOPE_IDS))
            refused(not (seen & ids), "A record ID has exactly one place in a connection grant.")
            seen |= ids
    sources = set(_ids(value["newRecords"]["sourceIds"], fmt.MAX_SOURCES))
    refused(not (seen & sources), "A source ID cannot also be a record ID.")
    for kind in RECORD_KINDS:
        refused(len(value["records"][kind + "Ids"]) + len(value["newRecords"][kind + "Ids"]) <= fmt.MAX_SCOPE_IDS)
    refused(permissions["add"] or not any(value["newRecords"].values()), "New record IDs need the add permission.")

    for key in ("issuedAt", "expiresAt"):
        refused(type(value[key]) is int and 0 < value[key] <= MAX_EPOCH_SECONDS, "Use whole epoch seconds.")
    refused(0 < value["expiresAt"] - value["issuedAt"] <= MAX_LIFETIME_SECONDS,
            "A connection lasts at most one day.")
    refused(type(value["maxIdleSeconds"]) is int and 1 <= value["maxIdleSeconds"] <= MAX_IDLE_SECONDS,
            "Choose a maximum idle time from one second through one hour.")
    return copy.deepcopy(value)


def check_time(grant, now):
    """Refuse a grant that has expired or is dated in the future. `now` is epoch seconds."""
    fmt.need(now < grant["expiresAt"], "connection_expired", "This connection has expired. Ask for a new one.")
    refused(grant["issuedAt"] <= now + MAX_CLOCK_SKEW_SECONDS, "This connection grant is not valid yet.")


def parse(raw: bytes):
    value = fmt.parse_json(raw)
    try:
        return validate(value)
    except (KeyError, TypeError, AttributeError):
        raise fmt.FormatError("connection_grant", "The connection grant is invalid.") from None


def required_permissions(action):
    return ACTION_PERMISSIONS.get(action)


def permitted_actions(grant):
    """The operation names whose every required permission is granted, in stable order."""
    allowed = grant["permissions"]
    return sorted(name for name, needed in ACTION_PERMISSIONS.items() if all(allowed[p] for p in needed))


def derive_scope(grant):
    """Scope-constraints v2: selected and preauthorized new IDs. A connection uses operation protocol 4."""
    return {"scopeVersion": 2, "collectionId": grant["collectionId"], "actions": permitted_actions(grant),
            **{kind + "Ids": grant["records"][kind + "Ids"] + grant["newRecords"][kind + "Ids"] for kind in RECORD_KINDS},
            "sourceIds": list(grant["newRecords"]["sourceIds"])}


def derive_field_policy(grant):
    """Field-policy v1 with no wildcard: each record lists exactly the fields its grant allows."""
    allowed, classes = grant["permissions"], grant["read"]

    def readable(kind):
        return [name for group in READ_CLASSES if allowed["read"] and classes[group]
                for name in READ_FIELDS[group].get(kind, ())]

    def writable(kind, new):
        names = set()
        for permission, fields in WRITE_FIELDS.items():
            if allowed[permission] and not new:
                names.update(fields.get(kind, ()))
        whole = "add" if new else "delete"  # Creating or removing a record needs every one of its fields.
        if kind != "collection" and allowed[whole] and (kind != "folder" or allowed["organize"]):
            names.update(grants.FIELDS[kind])
        return sorted(names)

    actions = (["read"] if allowed["read"] else []) + (
        ["search"] if allowed["read"] and (classes["titles"] or classes["text"]) else []) + permitted_actions(grant)
    policy = {"policyVersion": 1, "collectionId": grant["collectionId"], "actions": actions,
              "collection": {"read": readable("collection"), "write": writable("collection", False)}}
    for kind in RECORD_KINDS:
        rows = {record: {"read": readable(kind), "write": writable(kind, False)} for record in grant["records"][kind + "Ids"]}
        rows.update({record: {"read": readable(kind), "write": writable(kind, True)}
                     for record in grant["newRecords"][kind + "Ids"]})
        policy[kind + "s"] = rows
    return policy


def shares_content(grant):
    """Whether any authored value (title, text or link) may reach the session's destination."""
    return grant["permissions"]["read"] and any(grant["read"][name] for name in ("titles", "text", "links"))


def check_archive(grant, manifest):
    """Selected records must exist with their stated type; preauthorized new IDs must be unused."""
    refused(manifest["collection"]["id"] == grant["collectionId"], "The connection grant names another collection.")
    present = {kind: {row["id"] for row in manifest[fmt.RECORD_GROUPS[kind]]} for kind in RECORD_KINDS}
    everything = set().union(*present.values())
    for kind in RECORD_KINDS:
        refused(set(grant["records"][kind + "Ids"]) <= present[kind],
                "A selected record is not in this collection.")
        refused(not (set(grant["newRecords"][kind + "Ids"]) & everything), "A new record ID is already in use.")


def scope_counts(grant, manifest=None):
    """Counts only. With a manifest, also how many selected records are withheld by unselected links."""
    counts = {kind + "s": len(grant["records"][kind + "Ids"]) for kind in RECORD_KINDS}
    counts["newRecordSlots"] = sum(len(grant["newRecords"][kind + "Ids"]) for kind in RECORD_KINDS)
    counts["sources"] = len(grant["newRecords"]["sourceIds"])
    if manifest is not None:
        ids = {kind: set(grant["records"][kind + "Ids"]) | set(grant["newRecords"][kind + "Ids"]) for kind in RECORD_KINDS}
        withheld = 0
        for kind, group in (("note", "notes"), ("folder", "folders")):
            for row in manifest[group]:
                if row["id"] in ids[kind] and not (set(row.get("paperIds", [])) <= ids["paper"] and
                                                   set(row.get("noteIds", [])) <= ids["note"]):
                    withheld += 1
        counts["withheldForUnselectedLinks"] = withheld
    return counts
