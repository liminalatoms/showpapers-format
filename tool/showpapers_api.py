"""Supported local Python API for readable .showpapers snapshots (Python 3.10+).

This module delegates to the same strict reader, scoped operation planner and no-overwrite
writer as the CLI. It neither prints results nor sends them anywhere. Returned dictionaries
are JSON-compatible; authored content is omitted unless include_content=True is explicit.
The caller's scope file supplies constraints, not authenticated permission.
"""
from __future__ import annotations

from os import PathLike
from typing import Callable, Literal, TypeAlias, TypedDict, TypeVar, cast

import showpapers_format as _format
import showpapers_undo as _undo
import showpapers_grants as _grants
import showpapers_conflicts as _conflicts

API_VERSION = 1
SUPPORTED_FORMAT_VERSIONS = _format.SUPPORTED_FORMAT_VERSIONS
READABLE_FORMAT_VERSIONS = _format.READABLE_FORMAT_VERSIONS
FormatError = _format.FormatError

LocalPath: TypeAlias = str | PathLike[str]
JsonValue: TypeAlias = "None | bool | int | float | str | list[JsonValue] | dict[str, JsonValue]"


class Warning(TypedDict):
    code: str
    message: str


class PayloadSummary(TypedDict):
    id: str
    size: int
    sha256: str


class OriginalSummary(PayloadSummary):
    mediaType: str


class _InspectionOptions(TypedDict, total=False):
    warnings: list[Warning]
    # Only inspect(include_content=True) includes these fields. Exactly one note map is present.
    manifest: dict[str, JsonValue]
    noteText: dict[str, str]
    noteDocuments: dict[str, dict[str, JsonValue]]
    noteRecords: dict[str, dict[str, JsonValue]]
    paperRecords: dict[str, dict[str, JsonValue]]
    readings: dict[str, dict[str, JsonValue]]
    organization: dict[str, JsonValue]


class InspectionResult(_InspectionOptions):
    ok: Literal[True]
    format: Literal["showpapers"]
    formatVersion: int
    protection: Literal["readable"]
    collectionId: str
    revision: int
    originals: list[OriginalSummary]
    notes: list[PayloadSummary]
    folderCount: int
    totalPayloadBytes: int


class FieldInspectionResult(_InspectionOptions):
    """Permission projection; hidden fields and aggregate payload bytes are absent."""
    ok: Literal[True]
    format: Literal["showpapers"]
    formatVersion: int
    protection: Literal["readable"]
    collectionId: str
    revision: int
    fieldPolicyVersion: Literal[1]
    originals: list[dict[str, JsonValue]]
    notes: list[dict[str, JsonValue]]
    folderCount: int


class _ChangeOptions(TypedDict, total=False):
    recordType: Literal["paper", "note", "folder"]
    fields: list[str]
    paperIds: list[str]
    noteIds: list[str]
    addedPaperIds: list[str]
    removedPaperIds: list[str]
    recordIds: list[str]
    sourceId: str
    size: int
    sha256: str
    mediaType: str


class Change(_ChangeOptions):
    type: str
    id: str


class _OperationOptions(TypedDict, total=False):
    inputArchiveSha256: str
    formatVersion: int
    warnings: list[Warning]
    reviewChanges: list[dict[str, JsonValue]]


class OperationResult(_OperationOptions):
    ok: Literal[True]
    validated: Literal[True]
    applied: bool
    protocolVersion: int
    operationId: str
    collectionId: str
    fromRevision: int
    toRevision: int
    changes: list[Change]


__all__ = [
    "API_VERSION", "SUPPORTED_FORMAT_VERSIONS", "FormatError", "LocalPath", "JsonValue",
    "Warning", "PayloadSummary", "OriginalSummary", "InspectionResult", "FieldInspectionResult", "Change", "OperationResult",
    "create", "validate", "inspect", "contents", "preview", "save_copy", "preview_undo", "save_undo_copy",
    "conflicts", "preview_resolution", "save_resolved_copy",
    "READABLE_FORMAT_VERSIONS", "edit", "upgrade", "companion", "save_companion", "preview_changes", "apply_changes",
]

_Result = TypeVar("_Result")


def _call(action: Callable[[], _Result]) -> _Result:
    """Keep the CLI's sanitized fallback without hiding process interrupts or engine errors."""
    try:
        return action()
    except FormatError:
        raise
    except (OSError, TypeError, ValueError, KeyError, RecursionError):
        raise FormatError("invalid", "The input could not be processed safely.") from None


def create(spec_path: LocalPath, destination: LocalPath) -> InspectionResult:
    """Create a new archive from a bounded build-spec file and return metadata only.

    Sources must be explicitly listed relative files inside the build-spec directory. Core
    limits, version checks, strict references, safe publication and refusal to overwrite apply.
    A successful publication with incomplete temporary cleanup includes a warnings list.
    """
    def run() -> InspectionResult:
        warnings: list[Warning] = []
        manifest = _format.create_archive(spec_path, destination, warnings)
        result = cast(InspectionResult, _format.summary(manifest))
        if warnings:
            result["warnings"] = cast(list, result.get("warnings", [])) + warnings
        return result
    return _call(run)


def validate(archive_path: LocalPath, *, constraints_path: LocalPath | None = None,
             field_policy_path: LocalPath | None = None) -> InspectionResult | FieldInspectionResult:
    """Validate the entire archive and every payload; return the CLI's metadata-only inventory.

    Validation does not extract files, render images/PDFs, run OCR, or read an Android vault.
    It raises FormatError on any unsupported or invalid input, never a partial success.
    """
    return inspect(archive_path, constraints_path=constraints_path, field_policy_path=field_policy_path)


def inspect(archive_path: LocalPath, *, include_content: bool = False,
            constraints_path: LocalPath | None = None, field_policy_path: LocalPath | None = None) -> InspectionResult | FieldInspectionResult:
    """Validate and inspect an archive. Titles, paths and Note content are opt-in.

    Explicit True adds the manifest plus noteText (v1), noteDocuments (v2) or noteRecords (v3/4).
    Profile 4 also adds paperRecords, readings and a selected-collection organization inventory.
    It never returns original image/PDF bytes. IDs and digests in default results remain private
    metadata; none of these dictionaries is an analytics payload. No result is cached globally.
    A selected field policy requires constraints_path and returns FieldInspectionResult instead:
    its manifest is a projection, with hidden fields omitted, not a reusable archive manifest.
    """
    def run() -> InspectionResult | FieldInspectionResult:
        # Reject truthy strings/objects instead of accidentally exposing private Note content.
        _format.need(type(include_content) is bool, "arguments", "include_content must be a boolean.")
        _format.need(constraints_path is None or field_policy_path is not None, "permission",
                     "Scoped inspection requires explicit field permissions.")
        policy = _grants.load(field_policy_path, constraints_path)
        # Field policies are defined for profiles 1-3; a profile 4 archive under a policy is refused as a version.
        manifest, notes = _format.read_archive(archive_path, include_content=include_content or policy is not None,
                                               full_fidelity=policy is None)
        result = cast(InspectionResult, _format.summary(manifest))
        if include_content or policy is not None:
            result.update(cast(InspectionResult, _format.content_members(manifest, notes)))
        return policy.project_inspection(result, include_content) if policy is not None else result
    return _call(run)


def contents(archive_path: LocalPath) -> dict[str, JsonValue]:
    """Validate everything, then state what this selected collection holds and deliberately omits.

    The versioned statement (urn:showpapers:contents:1) lists each payload's path, size and
    SHA-256, link counts, usable protocol versions, the declared-absent record categories and
    fullVaultBackup: False. It has no titles, names or text and is never stored in the archive.
    """
    return _call(lambda: cast("dict[str, JsonValue]", _format.archive_contents(archive_path)))


def preview(archive_path: LocalPath, operations_path: LocalPath, constraints_path: LocalPath,
            *, sources_path: LocalPath | None = None, include_content: bool = False,
            field_policy_path: LocalPath | None = None) -> OperationResult:
    """Validate an explicit operation request and scope without writing a new snapshot.

    The default report contains actions, IDs, changed-field names and reference IDs. Explicit
    include_content=True adds bounded reviewChanges with before/after authored values only
    after complete old/new reference visibility; a denied review returns no partial content.
    Protocols 1–3 match their archive format; protocol 4 explicitly names format 1, 2 or 3.
    Protocols 3/4 bind the complete archive digest. Optional source manifests are separately
    caller-selected v4 inputs, never paths supplied by an operation. Preview is not a lock.
    """
    def run():
        _format.need(type(include_content) is bool, "arguments", "include_content must be a boolean.")
        policy = _grants.load(field_policy_path, constraints_path)
        if include_content:
            return cast(OperationResult, _undo.preview_with_content(
                archive_path, operations_path, constraints_path, sources_path=sources_path, field_policy=policy))
        return cast(OperationResult, _format.process_operations(
            archive_path, operations_path, constraints_path, sources_path=sources_path, field_policy=policy))
    return _call(run)


def save_copy(archive_path: LocalPath, operations_path: LocalPath, constraints_path: LocalPath,
              destination: LocalPath, *, sources_path: LocalPath | None = None,
              field_policy_path: LocalPath | None = None) -> OperationResult:
    """Revalidate the request/scope and write its changed revision to a new .showpapers file.

    This is the CLI's apply operation: no in-place edit, unconditional copy, scope inference or
    automatic conversion. Existing destinations are never replaced. The input stays unchanged.
    It preserves untouched payload bytes within the supported readable profile, not all vault
    metadata. There is no durable replay ledger or knowledge of the globally latest revision.
    """
    def run() -> OperationResult:
        _format.need(destination is not None, "arguments", "A new output path is required.")
        policy = _grants.load(field_policy_path, constraints_path)
        return cast(OperationResult,
            _format.process_operations(archive_path, operations_path, constraints_path, destination, sources_path=sources_path, field_policy=policy))
    return _call(run)


def preview_undo(before_archive_path: LocalPath, current_archive_path: LocalPath, operations_path: LocalPath,
                 constraints_path: LocalPath, undo_path: LocalPath, *, sources_path: LocalPath | None = None,
                 include_content: bool = False, field_policy_path: LocalPath | None = None) -> dict[str, JsonValue]:
    """Review a derived one-step inverse of the exact saved forward result, without writing.

    The separately chosen Undo request binds both archives and the exact original request/grant.
    Inverse rights are checked independently. Authored values require include_content=True;
    the default report contains only operation/record IDs, types and bounded metadata.
    """
    return _call(lambda: _undo.process_undo(before_archive_path, current_archive_path, operations_path,
        constraints_path, undo_path, sources_path=sources_path, include_content=include_content,
        field_policy=_grants.load(field_policy_path, constraints_path)))


def save_undo_copy(before_archive_path: LocalPath, current_archive_path: LocalPath, operations_path: LocalPath,
                   constraints_path: LocalPath, undo_path: LocalPath, destination: LocalPath, *,
                   sources_path: LocalPath | None = None, include_content: bool = False, field_policy_path: LocalPath | None = None) -> dict[str, JsonValue]:
    """Revalidate Undo and publish restored content at current revision+1 to a new path only.

    Existing files stay unchanged. This is not a global history, merge, redo stack or in-place rollback.
    Restored removed IDs come only from the exact verified before snapshot, never caller payloads.
    """
    def run():
        _format.need(destination is not None, "arguments", "A new output path is required.")
        return _undo.process_undo(before_archive_path, current_archive_path, operations_path, constraints_path,
            undo_path, destination, sources_path=sources_path, include_content=include_content,
            field_policy=_grants.load(field_policy_path, constraints_path))
    return _call(run)


def conflicts(base_archive_path: LocalPath, theirs_archive_path: LocalPath, constraints_path: LocalPath, *,
              mine_archive_path: LocalPath | None = None, mine_operations_path: LocalPath | None = None,
              sources_path: LocalPath | None = None, include_content: bool = False,
              field_policy_path: LocalPath | None = None) -> dict[str, JsonValue]:
    """Compare the caller's work and another snapshot against one named base, without writing.

    Name exactly one of mine_archive_path (a saved copy) or mine_operations_path (a request
    that targets the base). The closed report lists each record both sides changed
    incompatibly, the choices available for it, and the caller's changes that apply cleanly.
    It contains IDs, conflict kinds and field names only; records outside the scope are
    counted, never named. include_content=True adds base/mine/theirs values only with complete
    reference visibility and after field-policy projection. Nothing is merged or reserved.
    """
    return _call(lambda: _conflicts.process_conflicts(
        base_archive_path, theirs_archive_path, constraints_path, mine_archive=mine_archive_path,
        mine_request=mine_operations_path, sources_path=sources_path, include_content=include_content,
        field_policy=_grants.load(field_policy_path, constraints_path)))


def preview_resolution(base_archive_path: LocalPath, theirs_archive_path: LocalPath, constraints_path: LocalPath,
                       resolution_path: LocalPath, *, mine_archive_path: LocalPath | None = None,
                       mine_operations_path: LocalPath | None = None, sources_path: LocalPath | None = None,
                       field_policy_path: LocalPath | None = None) -> dict[str, JsonValue]:
    """Validate one explicit choice per reported conflict and return the exact receipt, without writing.

    The request binds all three inputs, the scope and the reviewed report digest. The derived
    change to the other snapshot needs its own action, record and field authority.
    """
    return _call(lambda: _conflicts.process_conflicts(
        base_archive_path, theirs_archive_path, constraints_path, mine_archive=mine_archive_path,
        mine_request=mine_operations_path, resolution_path=resolution_path, sources_path=sources_path,
        field_policy=_grants.load(field_policy_path, constraints_path)))


def save_resolved_copy(base_archive_path: LocalPath, theirs_archive_path: LocalPath, constraints_path: LocalPath,
                       resolution_path: LocalPath, destination: LocalPath, *,
                       mine_archive_path: LocalPath | None = None, mine_operations_path: LocalPath | None = None,
                       sources_path: LocalPath | None = None,
                       field_policy_path: LocalPath | None = None) -> dict[str, JsonValue]:
    """Revalidate the resolution and publish the merged collection to a new path only.

    No input is changed. An exact retry onto its own saved result is acknowledged with
    alreadyApplied; any other existing destination is refused. Originals are never replaced
    under an existing ID, and a kept copy always receives the caller's new unused ID.
    """
    def run():
        _format.need(destination is not None, "arguments", "A new output path is required.")
        return _conflicts.process_conflicts(
            base_archive_path, theirs_archive_path, constraints_path, mine_archive=mine_archive_path,
            mine_request=mine_operations_path, resolution_path=resolution_path, destination=destination,
            sources_path=sources_path, field_policy=_grants.load(field_policy_path, constraints_path))
    return _call(run)


# ----- Archive profile 4 (full fidelity) ------------------------------------------------------------------------


def edit(archive_path: LocalPath, directory: LocalPath) -> dict[str, JsonValue]:
    """Unpack any readable archive into a new directory holding a profile 4 build.json and its payload files.

    The build specification already names the next revision and the exact parent archive. Edit the JSON files, then
    call create(directory / "build.json", new_destination). The input is never changed; the directory must be new.
    """
    return _call(lambda: _format._fidelity().edit_archive(archive_path, directory))


def upgrade(archive_path: LocalPath, destination: LocalPath) -> dict[str, JsonValue]:
    """Write a profile 4 copy of a profile 1-3 archive at the next revision, refusing anything profile 4 cannot hold."""
    def run():
        warnings: list[Warning] = []
        result = _format._fidelity().upgrade_archive(archive_path, destination, warnings)
        if warnings:
            result["warnings"] = warnings
        return result
    return _call(run)


def companion(archive_path: LocalPath, *, archive_name: str | None = None) -> dict[str, JsonValue]:
    """Return the AI companion (urn:showpapers:companion:1 for profiles 1-3, :2 for profile 4) without writing."""
    return _call(lambda: _format._fidelity().companion(archive_path, archive_name))


def save_companion(archive_path: LocalPath, destination: LocalPath, *, archive_name: str | None = None) -> dict[str, JsonValue]:
    """Write the companion to a new <name>.showpapers.json file; existing files are never replaced."""
    return _call(lambda: _format._fidelity().write_companion(archive_path, destination, archive_name))


def preview_changes(archive_path: LocalPath, changes_path: LocalPath) -> dict[str, JsonValue]:
    """Check a urn:showpapers:changes:1 document against its exact base archive without writing anything."""
    return _call(lambda: _format._fidelity().apply_changes(archive_path, changes_path))


def apply_changes(archive_path: LocalPath, changes_path: LocalPath, destination: LocalPath) -> dict[str, JsonValue]:
    """Apply a changes document to its exact base archive and write a new profile 4 file at the next revision.

    Agent-provided values keep the changes document's generator as their agent and stay pending for the person's
    review in the apps. The input archive is never changed and an existing destination is never replaced.
    """
    def run():
        _format.need(destination is not None, "arguments", "A new output path is required.")
        warnings: list[Warning] = []
        result = _format._fidelity().apply_changes(archive_path, changes_path, destination, warnings)
        if warnings:
            result["warnings"] = warnings
        return result
    return _call(run)
