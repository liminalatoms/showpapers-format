"""Content-free discovery for the implemented readable interchange profiles."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import showpapers_format as fmt

DISCOVERY_VERSION = 1


def _arguments(properties, required=()):
    return {"type": "object", "additionalProperties": False,
            "properties": properties, "required": list(required)}


def _local_mcp_adapter():
    """Optional installed interface; paths are relative to this format-tool directory."""
    entrypoint = fmt.local_resource("../mcp/showpapers_mcp.py")
    requirements = fmt.local_resource("../mcp/requirements.lock.txt")
    if entrypoint is None or requirements is None:
        return None
    adapter = {
        "id": "showpapers-local-stdio", "transport": "stdio", "optional": True,
        "entrypoint": entrypoint, "sourceFilesAvailable": True,
        "documentation": fmt.documentation_resource("SHOWPAPERS_MCP.md"),
        "requirements": requirements,
        "runtime": {"pythonMinimum": "3.10", "platform": "Linux with directory handles and hard links",
                    "sdk": {"package": "mcp", "version": "2.2.0"}, "separateDependenciesRequired": True},
        "launch": {"arguments": ["{entrypoint}", "--collection", "{archive}"],
                   "optionalArguments": {"grant": ["--grant", "{scope}"],
                                         "fieldPolicy": ["--field-policy", "{fieldPolicy}"],
                                         "sources": ["--sources", "{sourceManifest}"],
                                         "shareContent": ["--share-content"],
                                         "saveCopy": ["--allow-save-copy", "--output-directory", "{directory}"]},
                   "pathsChosenByOwner": True, "toolCallsAcceptFilesystemPaths": False},
        "tools": ["showpapers_capabilities", "showpapers_schema", "showpapers_validate", "showpapers_read", "showpapers_search",
                  "showpapers_preview", "showpapers_preview_undo", "showpapers_discard", "showpapers_save_copy", "showpapers_continue_saved_copy", "showpapers_revoke"],
        "search": {"contentSharingRequired": True, "literalCaseInsensitive": True,
                   "fields": ["paper-title", "note-title", "current-note-text"],
                   "hiddenReferenceNotesOmitted": True, "bodyExcerptsReturned": False,
                   "queryUtf16": 256, "queryBytes": 1024, "textBytes": 16777216,
                   "pageItems": 20, "cursors": 32, "cursorSeconds": 600},
        "defaultAuthority": {"selectedArchives": 1, "contentSharing": False, "saveCopy": False,
                             "metadata": "whole-selected-archive-or-explicit-narrower-grant"},
        "scope": {"format": "scope-constraints-v1", "supportedVersions": [1, 2], "fixedAtLaunch": True,
                  "authority": "fixed-local-launch-constraints", "authenticatedRemotePermissions": False,
                  "archiveContentGrantsAccess": False, "changedGrantRevokesSession": True,
                  "contentRequiresGrant": True, "readsFilterGrantedRecordIds": True,
                  "actions": sorted(fmt.OPERATION_FIELDS_V4),
                  "fieldPolicy": {"supportedVersions": [1], "optional": True, "requiresGrant": True,
                                  "chosenAtLaunch": True, "changedPolicyRevokesSession": True}},
        "saveCopy": {"requiresLaunchPermission": True, "completeReadableArchiveIncludingUntouchedRecords": True,
                     "inputArchiveModified": False, "existingOutputsOverwritten": False,
                     "requiresExactPreviewToken": True, "expectedArchiveDigestForEveryFormat": True,
                     "proposalSeconds": 600, "pendingProposals": 8, "savedCopiesPerLaunch": 8,
                     "durableReplayLedger": False},
        "continueSavedCopy": {"requiresLaunchSaveCopyPermission": True, "requiresSessionReceipt": True,
                              "receiptSeconds": 600, "onlyCurrentRevisionChild": True,
                              "permissionsAndRecordIdsUnchanged": True, "retiresProposalsAndSearchCursors": True,
                              "launchSourceStillGuarded": True, "adoptedOutputStillGuarded": True,
                              "writesExternalFiles": False, "acceptsFilesystemPaths": False},
        "conditionalUndo": {"supportedVersions": [1], "currentContinuedForwardReceiptOnly": True,
                            "inversePermissionsRequired": True, "newRevisionCopyOnly": True,
                            "contentSharingRequiredForAuthoredPreview": True,
                            "completeReferenceScopeForAuthoredPreview": True,
                            "redo": False, "survivesProcessExit": False},
        "boundaries": {"networkService": False, "livePhoneConnection": False, "cloudAccount": False,
                       "encryptedBackup": False, "originalBytesReturned": False,
                       "clientMayShareContentWithItsModelProvider": True},
    }
    return _with_note_actions(_with_connection_session(adapter))


def _with_connection_session(adapter):
    """Tools and launch options that exist only when the host launches with a connection grant."""
    import showpapers_connection as connection
    adapter["tools"] += ["showpapers_session_status", "showpapers_approve", "showpapers_stop"]
    adapter["launch"]["optionalArguments"]["connection"] = [
        "--connection-grant", "{connectionGrant}", "--connection-state-directory", "{connectionState}"]
    adapter["connectionSession"] = {
        "grantVersions": [connection.GRANT_VERSION], "schema": connection.SCHEMA_ID,
        "tools": ["showpapers_session_status", "showpapers_approve", "showpapers_stop"],
        "toolsRegisteredOnlyWithConnectionGrant": True,
        "replacesLaunchOptions": ["--grant", "--field-policy", "--share-content", "--allow-save-copy"],
        "outputDirectoryRequiredExactlyWithExport": True, "operationProtocolVersion": 4,
        "stateDirectory": {"sessionMarker": "{grantId}.session", "revocationFlag": "{grantId}.revoked",
                           "unavailableFlag": "{grantId}.paused", "endedMarker": "{grantId}.ended",
                           "oneSessionPerGrantId": True, "recheckedBeforeEveryToolCallAndPublication": True},
        "approval": {"requiredBeforeEverySaveCopy": True, "boundToExactPreviewDigest": True,
                     "mismatchRetiresProposal": True, "humanApprovalVerified": False},
        "status": {"scopeAsCountsOnly": True, "clientIdentityVerified": False, "destinationVerified": False,
                   "extendsIdleTime": False},
        "stop": {"retiresProposalsCursorsReceiptsAndApprovals": True, "recallsAlreadySharedContent": False,
                 "savedCopiesRemain": True, "sameGrantCanStartAgain": False},
        "unknownToolsRefused": True,
        "boundaries": {"livePhoneConnection": False, "authenticatedPeer": False, "signedGrant": False,
                       "lockedOrOfflinePhoneDetected": False, "hostControlledUnavailableFlagOnly": True}}
    return adapter


# Tool -> portable operations it may plan, and the connection permissions every call needs. The adapter's
# own table (showpapers_note_actions.ACTIONS), its connection rules and the specification are tested against this.
NOTE_ACTION_TOOLS = {
    "showpapers_note_create": (("note.add",), ("add",)),
    "showpapers_note_add_lines": (("note.edit",), ("read", "edit")),
    "showpapers_note_add_tasks": (("note.edit",), ("read", "edit")),
    "showpapers_note_set_task_checked": (("note.edit",), ("read", "edit")),
    "showpapers_note_rename_task": (("note.edit",), ("read", "edit")),
    "showpapers_note_remove_task": (("note.edit",), ("read", "edit")),
    "showpapers_note_remove_text": (("note.edit",), ("read", "edit")),
    "showpapers_note_replace_text": (("note.edit",), ("read", "edit")),
    "showpapers_note_move_task": (("note.edit",), ("read", "edit")),
    "showpapers_note_rename": (("record.rename", "note.edit"), ("read", "edit")),
    "showpapers_note_link_paper": (("note.edit",), ("read", "edit")),
    "showpapers_note_unlink_paper": (("note.edit",), ("read", "edit")),
    "showpapers_note_copy": (("note.add",), ("read", "add")),
}
HANDOFF_RECEIPT_FIELDS = (
    "handoffVersion", "collectionId", "formatVersion", "filename", "location", "size", "revision", "archiveSha256",
    "parentRevision", "parentArchiveSha256", "launchRevision", "launchArchiveSha256", "chain", "derivedFromLaunchArchive",
    "adoptedAsWorkingCopy", "undoCopy", "continuationSecondsRemaining", "sourceUnchanged", "appliedToConnectedCollection",
    "livePhoneConnection", "authenticated")


def _with_note_actions(adapter):
    """Exact Note actions, the handoff receipt and the client helpers of the optional adapter."""
    adapter["tools"] += [*NOTE_ACTION_TOOLS, "showpapers_handoff_receipt"]
    adapter["noteActions"] = {
        "documentation": fmt.documentation_resource("SHOWPAPERS_MCP_NOTE_ACTIONS.md"),
        "tools": {name: {"operations": list(operations), "connectionPermissions": list(permissions)}
                  for name, (operations, permissions) in NOTE_ACTION_TOOLS.items()},
        "targetsByStableId": True, "exactLabelOneMatchOrRefusal": True, "literalTextExactlyOnce": True,
        "contentSharingRequiredExceptCreate": True, "plansOrdinaryOperationsForPreview": True,
        "savesOnlyThroughSaveCopy": True, "conditionalUndoThroughExistingReceipt": True,
        "newTasksAfterLastTaskOnePerLine": True, "paperLinksIndivisible": True,
        "olderRowRecordsStayRowBased": True, "formatConversion": False, "timestampsChangedByEdits": False,
        "labelScalars": fmt.MAX_NOTE_RECORD_LABEL, "linesPerCall": fmt.MAX_OPERATIONS}
    adapter["handoff"] = {
        "documentation": fmt.documentation_resource("SHOWPAPERS_HANDOFF.md"), "tool": "showpapers_handoff_receipt",
        "connectionPermissions": ["export"], "handoffVersion": 1, "fields": list(HANDOFF_RECEIPT_FIELDS),
        "returnsUpdatedFileInLaunchOutputDirectory": True, "returnsFilesystemPaths": False,
        "savedFileReverifiedForEveryReceipt": True, "appliesToConnectedCollection": False,
        "androidApplyImplemented": False, "authenticated": False}
    adapter["clients"] = {
        "documentation": fmt.documentation_resource("SHOWPAPERS_MCP_CLIENTS.md"),
        "entrypoint": fmt.local_resource("../mcp/showpapers_clients.py"),
        "commands": ["config", "manifest", "doctor"],
        "configClients": ["claude-desktop", "claude-code", "codex", "openai-agents"],
        "functionManifest": fmt.local_resource("../mcp/clients/showpapers-tools.manifest.json"),
        "functionManifestGeneratedFromMcpToolList": True, "functionCallBridgeUsesRealStdioSession": True,
        "doctorLaunchesOnlyThisAdapter": True, "verifiedInsideThirdPartyApplications": False,
        "chatGptAppLocalStdio": False}
    return adapter


def _conflict_kinds():
    import showpapers_conflicts
    return showpapers_conflicts.KINDS


def _connection_grant():
    """One host-created session grant; enforced through the derived scope and field policy."""
    import showpapers_connection as connection
    return {"version": connection.GRANT_VERSION, "schema": connection.SCHEMA_ID,
            "oneSessionPerGrant": True, "createdByTrustedHost": True, "acceptedFromToolArguments": False,
            "permissions": list(connection.PERMISSIONS), "readClasses": list(connection.READ_CLASSES),
            "destinations": list(connection.DESTINATIONS), "destinationAcknowledgementRequired": True,
            "actionPermissions": {name: list(needed) for name, needed in sorted(connection.ACTION_PERMISSIONS.items())},
            "undoNeedsInverseActionPermissions": True,
            "readFields": {group: {kind: list(fields) for kind, fields in kinds.items()}
                           for group, kinds in connection.READ_FIELDS.items()},
            "textRequiresLinks": True, "originalBytesReturned": False,
            "derives": {"scopeVersion": 2, "fieldPolicyVersion": 1, "operationProtocolVersion": 4},
            "limits": {"lifetimeSeconds": connection.MAX_LIFETIME_SECONDS, "idleSeconds": connection.MAX_IDLE_SECONDS,
                       "clockSkewSeconds": connection.MAX_CLOCK_SKEW_SECONDS, "labelCharacters": connection.MAX_LABEL,
                       "idsPerRecordType": fmt.MAX_SCOPE_IDS, "sourceIds": fmt.MAX_SOURCES},
            "errorCodes": list(connection.ERROR_CODES),
            "signedOrAuthenticated": False, "livePhoneConnection": False}


def _local_workspace():
    """A separate Linux CLI; it does not make the stateless adapter durable."""
    path = {"type": "string", "minLength": 1, "description": "An owner-selected local path."}
    commands = []
    for name in ("init", "status", "open", "preview", "commit", "resume", "conflicts", "resolve"):
        properties = {"workspace": path}
        required = ["workspace"]
        cli = [name, "--workspace", "{workspace}"]
        optional = {}
        if name != "status":
            properties.update(grant=path, fieldPolicy=path)
            required.append("grant"); cli += ["--grant", "{grant}"]
            optional["fieldPolicy"] = ["--field-policy", "{fieldPolicy}"]
        if name == "init":
            properties["source"] = path; required.append("source"); cli += ["--source", "{source}"]
        if name in ("preview", "commit", "resume", "conflicts", "resolve"):
            properties.update(request=path, sources=path)
            optional["sources"] = ["--sources", "{sources}"]
            if name == "resume":
                properties["initSource"] = path
                properties["resolution"] = path
                optional.update(request=["--request", "{request}"], initSource=["--init-source", "{initSource}"],
                                resolution=["--resolution", "{resolution}"])
            else:
                required.append("request"); cli += ["--request", "{request}"]
        if name == "resolve":
            properties["resolution"] = path; required.append("resolution"); cli += ["--resolution", "{resolution}"]
        command = {"name": name, "arguments": _arguments(properties, required), "cli": cli,
                   "optionalArguments": optional, "writesWorkspace": name not in ("status", "open", "conflicts")}
        if name in ("open", "conflicts"):
            properties["includeContent"] = {"type": "boolean", "default": False}
            command["optionalFlags"] = {"includeContent": "--include-content"}
        commands.append(command)
    return {"workspaceVersion": 1, "schema": "urn:showpapers:workspace-receipt:1",
            "entrypoint": "showpapers_workspace.py", "apiModule": "showpapers_workspace",
            "documentation": fmt.local_resource("README_DELIVERY_WORKSPACE.md"),
            "commands": commands, "platform": "Linux with /proc/self/fd, flock and hard links",
            "successExitCode": 0, "failureExitCode": 1, "standardOutput": "one-json-value",
            "formatVersions": [1, 2, 3], "scopePinnedAtInitialization": True,
            "optionalFieldPolicyPinnedAtInitialization": True,
            "completeReadableSnapshotsIncludeUntouchedHiddenRecords": True,
            "cooperatingWritersSerialized": True, "immutableRevisionSnapshots": True,
            "exactOperationRetryReceipts": True, "explicitResumeRequiredAfterInterruptedCommit": True,
            "initializationRecoveryStartsAtDurableReservation": True,
            "oldRetryReturnsCurrentHeadAndSeparateCommittedResult": True,
            "explicitConflictResolution": {"version": 1, "baseMustBeCommittedWorkspaceRevision": True,
                                           "staleRequestReplannedAgainstItsOwnBase": True,
                                           "mergedRequestCannotBeCommittedAgain": True,
                                           "resumeRequiresSameResolutionFile": True},
            "limits": {"revisions": 32, "proposals": 64, "workspaceBytes": 512 * 1024 * 1024,
                       "directoryEntries": 512},
            "boundaries": {"networkService": False, "livePhoneConnection": False,
                           "authenticatedPermissions": False, "automaticConflictMerge": False,
                           "crossWorkspaceReplayLedger": False, "powerLossRecoveryVerified": False,
                           "encryptedBackup": False, "mcpUsesThisWorkspace": False}}


def _specification():
    """Every independently versioned contract; archive and operation versions are separate axes."""
    axes = [
        {"axis": "archive-format", "field": "formatVersion", "versions": list(fmt.READABLE_FORMAT_VERSIONS),
         "schemaKinds": ["manifest", "build-spec"]},
        {"axis": "operation-protocol", "field": "protocolVersion",
         "versions": list(fmt.SUPPORTED_OPERATION_PROTOCOL_VERSIONS), "schemaKinds": ["operations"]},
        {"axis": "scope-constraints", "field": "scopeVersion", "versions": [1, 2], "schemaKinds": ["scope"]},
        {"axis": "source-manifest", "field": "sourcesVersion", "versions": [1], "schemaKinds": ["sources"]},
        {"axis": "note-document", "field": "noteVersion", "versions": [1], "schemaKinds": ["note-document"]},
        {"axis": "note-record", "field": "recordVersion", "versions": [1], "schemaKinds": ["note-record"]},
        {"axis": "conditional-undo", "field": "undoVersion", "versions": [1], "schemaKinds": ["undo"]},
        {"axis": "field-policy", "field": "policyVersion", "versions": [1], "schemaKinds": ["field-policy"]},
        {"axis": "workspace-receipt", "field": "workspaceVersion", "versions": [1], "schemaKinds": ["workspace-receipt"]},
        {"axis": "contents-statement", "field": "contentsVersion", "versions": [1, 2], "schemaKinds": ["contents"]},
        {"axis": "conflict-resolution", "field": "resolutionVersion", "versions": [1], "schemaKinds": ["conflict-resolution"]},
        {"axis": "conflict-report", "field": "conflictVersion", "versions": [1], "schemaKinds": ["conflict-report"]},
        {"axis": "connection-grant", "field": "grantVersion", "versions": [1], "schemaKinds": ["connection-grant"]},
        {"axis": "paper-record", "field": "paperVersion", "versions": [1], "schemaKinds": ["paper-record"]},
        {"axis": "reading", "field": "readingVersion", "versions": [1], "schemaKinds": ["reading"]},
        {"axis": "discovery", "field": "discoveryVersion", "versions": [DISCOVERY_VERSION], "schemaKinds": []},
    ]
    def documents(names):
        return {key: path for key, name in names.items() if (path := fmt.documentation_resource(name)) is not None}

    return {"version": fmt.SPECIFICATION_VERSION, "document": fmt.documentation_resource("SHOWPAPERS_SPECIFICATION.md"),
            "profileDocuments": documents({"1": "SHOWPAPERS_FORMAT.md", "2": "SHOWPAPERS_RICH_NOTES.md",
                                           "3": "SHOWPAPERS_SCRATCH_RECORDS.md", "4": "SHOWPAPERS_FULL_FIDELITY.md"}),
            "operationProtocolDocuments": documents({"1": "SHOWPAPERS_FORMAT.md", "2": "SHOWPAPERS_RICH_NOTES.md",
                                                     "3": "SHOWPAPERS_SCRATCH_RECORDS.md", "4": "SHOWPAPERS_OPERATIONS_V4.md"}),
            "relatedDocuments": documents({"conflict-resolution": "SHOWPAPERS_CONFLICT_RESOLUTION.md",
                                           "conditional-undo": "SHOWPAPERS_CONDITIONAL_UNDO.md",
                                           "field-policy": "PORTABLE_FIELD_PERMISSIONS.md",
                                           "connection-grant": "SHOWPAPERS_CONNECTION_GRANT.md",
                                           "ai-interchange": "SHOWPAPERS_AI_INTERCHANGE.md",
                                           "agent-guide": "SHOWPAPERS_AGENT_GUIDE.md"}),
            "versionAxes": axes,
            "compatibility": {"olderSupportedVersionsRemainReadable": True, "unknownVersionsRefused": True,
                              "unknownFieldsRefused": True, "implicitMigration": False,
                              "newRecordCategoriesRequireNewFormatVersion": True,
                              "integersAreCanonicalDecimalTokens": True, "duplicateJsonKeysRefused": True},
            "conformance": {"goldenCorpus": fmt.local_resource("fixtures/interop/golden.json"),
                            "fullFidelityCorpus": fmt.local_resource("fixtures/full-fidelity/golden.json"),
                            "tests": [path for name in ("tests/test_spec_conformance.py", "tests/test_manifest_strict.py", "tests/test_interop.py",
                                                       "tests/test_full_fidelity.py", "tests/test_full_fidelity_conformance.py")
                                      if (path := fmt.local_resource(name)) is not None],
                            "publicExamples": "https://protocol.showpapers.app/examples"}}


def _limits():
    import showpapers_conflicts
    return {"conflicts": showpapers_conflicts.MAX_CONFLICTS, "originals": fmt.MAX_ORIGINALS, "notes": fmt.MAX_NOTES, "folders": fmt.MAX_FOLDERS,
            "originalBytes": fmt.MAX_ORIGINAL_BYTES, "plainNoteBytes": fmt.MAX_NOTE_BYTES,
            "richNoteBytes": fmt.MAX_RICH_NOTE_BYTES, "noteRecordBytes": fmt.MAX_NOTE_RECORD_BYTES,
            "noteUtf16CodeUnits": fmt.MAX_NOTE_UTF16, "noteMarks": fmt.MAX_NOTE_MARKS,
            "notePaperLinks": fmt.MAX_NOTE_PAPER_LINKS, "noteRecordItems": fmt.MAX_NOTE_RECORD_ITEMS,
            "noteRecordLabelScalars": fmt.MAX_NOTE_RECORD_LABEL, "noteItemPaperReferences": fmt.MAX_NOTE_ITEM_PAPERS,
            "titleScalars": fmt.MAX_TITLE, "folderNameScalars": fmt.MAX_FOLDER_NAME,
            "totalPayloadBytes": fmt.MAX_PAYLOAD_BYTES, "jsonDocumentBytes": fmt.MAX_MANIFEST_BYTES,
            "zipEntries": fmt.MAX_ENTRIES, "zipDirectoryBytes": fmt.MAX_ZIP_DIRECTORY_BYTES,
            "archiveBytes": fmt.MAX_ARCHIVE_BYTES, "expansionRatio": fmt.MAX_EXPANSION_RATIO,
            "imageChunks": fmt.MAX_IMAGE_CHUNKS, "imagePixels": fmt.MAX_IMAGE_PIXELS,
            "maximumRevision": fmt.MAX_REVISION, "maximumTimestamp": fmt.MAX_TIMESTAMP,
            "operationBatch": fmt.MAX_OPERATIONS, "scopeIdsPerRecordType": fmt.MAX_SCOPE_IDS,
            "scopeSourceIds": fmt.MAX_SOURCES, "sources": fmt.MAX_SOURCES,
            "sourcePathCharacters": fmt.MAX_SOURCE_PATH}


def _manifest_structure():
    return {"entry": "manifest.json", "closedFieldSets": True,
            "fields": {"manifest": list(fmt.MANIFEST_FIELDS), "collection": list(fmt.COLLECTION_FIELDS),
                       "original": list(fmt.ORIGINAL_FIELDS), "note": list(fmt.NOTE_FIELDS),
                       "folder": list(fmt.FOLDER_FIELDS)},
            "constants": {"format": "showpapers", "protection": "readable", "noteEncoding": "utf-8"},
            "mediaTypes": dict(fmt.MEDIA), "noteTemplates": list(fmt.NOTE_TEMPLATES), "noteIcons": list(fmt.NOTE_ICONS),
            "canonicalPaths": {"original": "originals/<id>.<extension-for-mediaType>", "note": "notes/<id>.<note-extension>"},
            "identity": {"canonicalLowercaseUuid": True, "uniqueAcrossCollectionAndAllRecords": True},
            "contentCoverage": [dict(row) for row in fmt.CONTENT_COVERAGE],
            "notIncluded": list(fmt.NOT_INCLUDED), "selectedCollection": True, "fullVaultBackup": False,
            "contentsStatement": {"command": "contents", "schema": "urn:showpapers:contents:1", "storedInsideArchive": False}}


def _fidelity_commands(path):
    """Top-level descriptors for the commands profile 4 added; they accept any readable profile as input."""
    return [
        {"name": "edit", "description": "Unpack any readable archive into a new directory with a profile 4 build.json (next revision, exact parent) and its payload files; edit them, then run create.",
         "arguments": _arguments({"archive": path, "outputDirectory": path}, ("archive", "outputDirectory")),
         "cli": ["edit", "{archive}", "--output-directory", "{outputDirectory}"], "writesNewFile": True},
        {"name": "companion", "description": "Write the plain JSON companion that describes an archive for AI apps that cannot open ZIP files.",
         "arguments": _arguments({"archive": path, "output": path, "archiveName": {"type": "string", "minLength": 1}},
                                 ("archive", "output")),
         "cli": ["companion", "{archive}", "--output", "{output}"],
         "optionalArguments": {"archiveName": ["--archive-name", "{archiveName}"]}, "writesNewFile": True},
        {"name": "upgrade", "description": "Write a profile 4 copy of a profile 1-3 archive at the next revision, refusing anything profile 4 cannot hold.",
         "arguments": _arguments({"archive": path, "output": path}, ("archive", "output")),
         "cli": ["upgrade", "{archive}", "--output", "{output}"], "writesNewFile": True},
    ]


def _fidelity_descriptors(path):
    """How to run every command on profile 4; preview and apply take a changes document instead of operations."""
    return [
        {"name": "create", "cli": ["create", "--spec", "{spec}", "--output", "{output}"],
         "arguments": _arguments({"spec": path, "output": path}, ("spec", "output")), "writesNewFile": True,
         "note": "A build spec with formatVersion 4 (urn:showpapers:format:build-spec:4)."},
        {"name": "validate", "cli": ["validate", "{archive}"], "arguments": _arguments({"archive": path}, ("archive",)),
         "writesNewFile": False},
        {"name": "inspect", "cli": ["inspect", "{archive}"],
         "arguments": _arguments({"archive": path, "includeContent": {"type": "boolean", "default": False}}, ("archive",)),
         "optionalFlags": {"includeContent": "--include-content"}, "writesNewFile": False,
         "note": "--include-content adds manifest, noteRecords, paperRecords, readings and a selected-collection organization inventory."},
        {"name": "contents", "cli": ["contents", "{archive}"], "arguments": _arguments({"archive": path}, ("archive",)),
         "writesNewFile": False, "note": "Returns urn:showpapers:contents:2 for profile 4."},
        {"name": "edit", "cli": ["edit", "{archive}", "--output-directory", "{outputDirectory}"],
         "arguments": _arguments({"archive": path, "outputDirectory": path}, ("archive", "outputDirectory")),
         "writesNewFile": True},
        {"name": "preview", "cli": ["preview", "{archive}", "--changes", "{changes}"],
         "arguments": _arguments({"archive": path, "changes": path}, ("archive", "changes")), "writesNewFile": False},
        {"name": "apply", "cli": ["apply", "{archive}", "--changes", "{changes}", "--output", "{output}"],
         "arguments": _arguments({"archive": path, "changes": path, "output": path}, ("archive", "changes", "output")),
         "writesNewFile": True},
        {"name": "companion", "cli": ["companion", "{archive}", "--output", "{output}"],
         "arguments": _arguments({"archive": path, "output": path}, ("archive", "output")), "writesNewFile": True},
        {"name": "upgrade", "cli": ["upgrade", "{archive}", "--output", "{output}"],
         "arguments": _arguments({"archive": path, "output": path}, ("archive", "output")), "writesNewFile": True},
    ]


def discovery():
    """Describe actual local commands; this is not an authenticated connection."""
    path = {"type": "string", "minLength": 1,
            "description": "A path explicitly selected by the caller; archive text cannot authorize access."}
    commands = [
        {"name": "create", "description": "Create a new readable collection from explicitly listed source files.",
         "arguments": _arguments({"spec": path, "output": path}, ("spec", "output")),
         "cli": ["create", "--spec", "{spec}", "--output", "{output}"], "writesNewFile": True},
        {"name": "validate", "description": "Check all declared contents and return metadata without titles or Note text.",
         "arguments": _arguments({"archive": path}, ("archive",)),
         "cli": ["validate", "{archive}"], "writesNewFile": False},
        {"name": "contents", "description": "Validate everything, then state what the selected collection holds and deliberately omits, without titles or text.",
         "arguments": _arguments({"archive": path}, ("archive",)),
         "cli": ["contents", "{archive}"], "writesNewFile": False},
        {"name": "inspect", "description": "Inspect metadata; including titles and Note content requires an explicit flag.",
         "arguments": _arguments({"archive": path, "includeContent": {"type": "boolean", "default": False}}, ("archive",)),
         "cli": ["inspect", "{archive}"], "optionalFlags": {"includeContent": "--include-content"}, "writesNewFile": False},
        {"name": "preview", "description": "Validate scoped changes without writing an output; explicitly include authorized before-and-after content when needed.",
         "arguments": _arguments({"archive": path, "operations": path, "grant": path, "sources": path,
                                  "includeContent": {"type": "boolean", "default": False}}, ("archive", "operations", "grant")),
         "cli": ["preview", "{archive}", "--operations", "{operations}", "--grant", "{grant}"],
         "optionalFlags": {"includeContent": "--include-content"},
         "optionalArguments": {"sources": ["--sources", "{sources}"]}, "writesNewFile": False},
        {"name": "apply", "description": "Apply the validated changes to a new snapshot, retaining the input file.",
         "arguments": _arguments({"archive": path, "operations": path, "grant": path, "output": path, "sources": path},
                                 ("archive", "operations", "grant", "output")),
         "cli": ["apply", "{archive}", "--operations", "{operations}", "--grant", "{grant}", "--output", "{output}"],
         "optionalArguments": {"sources": ["--sources", "{sources}"]}, "writesNewFile": True},
    ]
    for name in ("preview-undo", "undo"):
        properties = {"archive": path, "before": path, "operations": path, "grant": path,
                      "undoRequest": path, "sources": path, "includeContent": {"type": "boolean", "default": False}}
        required = ["archive", "before", "operations", "grant", "undoRequest"]
        cli = [name, "{archive}", "--before", "{before}", "--operations", "{operations}",
               "--grant", "{grant}", "--undo-request", "{undoRequest}"]
        if name == "undo":
            properties["output"] = path
            required.append("output")
            cli += ["--output", "{output}"]
        commands.append({"name": name,
                         "description": "Verify both selected snapshots and the exact forward request, then preview or save its permitted inverse at the current revision plus one.",
                         "arguments": _arguments(properties, required), "cli": cli,
                         "optionalFlags": {"includeContent": "--include-content"},
                         "optionalArguments": {"sources": ["--sources", "{sources}"]},
                         "writesNewFile": name == "undo"})
    for name in ("conflicts", "preview-resolve", "resolve"):
        properties = {"archive": path, "theirs": path, "mine": path, "operations": path, "grant": path, "sources": path}
        required = ["archive", "theirs", "grant"]
        cli = [name, "{archive}", "--theirs", "{theirs}", "--grant", "{grant}"]
        if name == "conflicts":
            properties["includeContent"] = {"type": "boolean", "default": False}
        else:
            properties["resolution"] = path; required.append("resolution"); cli += ["--resolution", "{resolution}"]
        if name == "resolve":
            properties["output"] = path; required.append("output"); cli += ["--output", "{output}"]
        command = {"name": name,
                   "description": "Compare the caller's own work and another snapshot against one named base; report every incompatible change, or apply one explicit choice per conflict to a new copy.",
                   "arguments": _arguments(properties, required), "cli": cli,
                   "optionalArguments": {"mine": ["--mine", "{mine}"], "operations": ["--operations", "{operations}"],
                                         "sources": ["--sources", "{sources}"]},
                   "writesNewFile": name == "resolve"}
        command["arguments"]["oneOf"] = [{"required": ["mine"]}, {"required": ["operations"]}]
        if name == "conflicts":
            command["optionalFlags"] = {"includeContent": "--include-content"}
        commands.append(command)
    commands += _fidelity_commands(path)
    for command in commands:
        if command["name"] in ("create", "contents", "edit", "companion", "upgrade"):
            continue
        command["arguments"]["properties"]["fieldPolicy"] = path
        command.setdefault("optionalArguments", {})["fieldPolicy"] = ["--field-policy", "{fieldPolicy}"]
        command["arguments"]["dependentRequired"] = {"fieldPolicy": ["grant"]}
        if command["name"] in ("validate", "inspect"):
            command["arguments"]["properties"]["grant"] = path
            command["optionalArguments"]["grant"] = ["--grant", "{grant}"]
    schemas = {}
    versions = {}
    for version in fmt.SUPPORTED_FORMAT_VERSIONS:
        schema_ids = {}
        for kind in ("manifest", "build-spec", "operations"):
            filename = f"{kind}{'.v' + str(version) if version > 1 else ''}.schema.json"
            data = Path(__file__).with_name(filename).read_bytes()
            schema = json.loads(data)
            schemas[schema["$id"]] = {"filename": filename, "sha256": hashlib.sha256(data).hexdigest(), "schema": schema}
            schema_ids[kind] = schema["$id"]
        fields = {1: fmt.OPERATION_FIELDS, 2: fmt.OPERATION_FIELDS_V2, 3: fmt.OPERATION_FIELDS_V3}[version]
        versions[str(version)] = {
            "formatVersion": version, "operationProtocolVersion": version, "schemas": schema_ids,
            "noteRepresentation": fmt.NOTE_PAYLOADS[version]["representation"],
            "notePayload": {"pathExtension": fmt.NOTE_PAYLOADS[version]["extension"],
                            "minimumBytes": fmt.NOTE_PAYLOADS[version]["minimumBytes"],
                            "maximumBytes": fmt.NOTE_PAYLOADS[version]["maximumBytes"],
                            "schema": {1: None, 2: "urn:showpapers:scratch-document:1",
                                       3: "urn:showpapers:scratch-record:1"}[version],
                            "titleScalars": fmt.NOTE_PAYLOADS[version]["titleScalars"],
                            "titleEdgeWhitespacePreserved": fmt.NOTE_PAYLOADS[version]["titleEdgeWhitespace"]},
            "operationProtocolVersions": [version, 4], "scopeVersions": {str(version): 1, "4": 2},
            "preconditions": ["collectionId", "expectedRevision"] + (["expectedArchiveSha256"] if version == 3 else []),
            "actions": {name: {"required": sorted(required | {"type"}), "optional": sorted(optional)}
                        for name, (required, optional) in sorted(fields.items())},
        }
    for kind in ("scope", "note-document", "note-record", "undo", "field-policy", "workspace-receipt", "contents",
                 "conflict-report", "conflict-resolution"):
        filename = f"{kind}.schema.json"
        data = Path(__file__).with_name(filename).read_bytes()
        schema = json.loads(data)
        schemas[schema["$id"]] = {"filename": filename, "sha256": hashlib.sha256(data).hexdigest(), "schema": schema}
    for kind in ("connection-grant", "full-fidelity-manifest", "full-fidelity-build-spec", "paper-record", "reading",
                 "contents.v2"):
        filename = f"{kind}.schema.json"
        data = Path(__file__).with_name(filename).read_bytes()
        schema = json.loads(data)
        schemas[schema["$id"]] = {"filename": filename, "sha256": hashlib.sha256(data).hexdigest(), "schema": schema}
    v4_schemas = {}
    for kind, filename in (("operations", "operations.v4.schema.json"),
                           ("scope", "scope.v2.schema.json"), ("sources", "sources.schema.json")):
        data = Path(__file__).with_name(filename).read_bytes()
        schema = json.loads(data)
        schemas[schema["$id"]] = {"filename": filename, "sha256": hashlib.sha256(data).hexdigest(), "schema": schema}
        v4_schemas[kind] = schema["$id"]
    v4_actions = {name: {"required": sorted(required | {"type"}), "optional": sorted(optional)}
                  for name, (required, optional) in sorted(fmt.OPERATION_FIELDS_V4.items())}
    for name in ("note.add", "note.edit"):
        v4_actions[name] = {"byFormatVersion": {
            str(version): {"required": sorted(fields[name][0] | {"type"}), "optional": sorted(fields[name][1])}
            for version, fields in ((1, fmt.OPERATION_FIELDS), (2, fmt.OPERATION_FIELDS_V2),
                                    (3, fmt.OPERATION_FIELDS_V3))}}
    protocols = {"4": {
        "operationProtocolVersion": 4, "supportedFormatVersions": [1, 2, 3],
        "scopeVersion": 2, "schemas": v4_schemas,
        "preconditions": ["collectionId", "expectedRevision", "expectedArchiveSha256", "formatVersion"],
        "actions": v4_actions,
        "notePayloadDeterminedByFormatVersion": True,
        "sources": {"ownerSelectedManifest": True, "operationPathsAllowed": False,
                    "maximumSources": 32, "maximumSourceBytes": 20 * 1024 * 1024,
                    "maximumTotalBytes": 100 * 1024 * 1024, "sizeAndSha256Required": True},
        "paperRemoval": {"remainingReferencesRefused": True, "implicitCascade": False},
        "ordering": {"exactPermutation": True, "archiveDisplayOrderOnly": True},
    }}
    # Protocols 1-3 are bound to the equally numbered archive format and scope v1.
    for version in fmt.SUPPORTED_FORMAT_VERSIONS:
        profile = versions[str(version)]
        protocols[str(version)] = {
            "operationProtocolVersion": version, "supportedFormatVersions": [version], "scopeVersion": 1,
            "schemas": {"operations": profile["schemas"]["operations"], "scope": "urn:showpapers:scope-constraints:1"},
            "preconditions": list(profile["preconditions"]), "actions": profile["actions"],
            "notePayloadDeterminedByFormatVersion": True}
    return {
        "ok": True, "discoveryVersion": DISCOVERY_VERSION, "format": "showpapers", "protection": "readable",
        "currentCollection": fmt.current_collection_guidance(),
        "specification": _specification(), "limits": _limits(), "manifest": _manifest_structure(),
        "commands": commands, "profiles": versions, "operationProtocols": protocols, "schemas": schemas,
        "conditionalUndo": {"version": 1, "schema": "urn:showpapers:undo:1",
                            "formatVersions": [1, 2, 3], "operationProtocolVersions": [1, 2, 3, 4],
                            "exactBeforeAndCurrentSnapshots": True, "replaysForwardRequest": True,
                            "inversePermissionsRequired": True, "newRevisionCopyOnly": True,
                            "callerConstraintsAreAuthentication": False, "globalLatestRevision": False},
        "conflictResolution": {"version": 1, "requestSchema": "urn:showpapers:conflict-resolution:1",
                               "reportSchema": "urn:showpapers:conflict-report:1",
                               "formatVersions": [1, 2, 3], "operationProtocolVersions": [1, 2, 3, 4],
                               "namedBaseSnapshotRequired": True, "baseAncestryVerified": False,
                               "mine": ["archive", "request"], "kinds": list(_conflict_kinds()),
                               "choices": ["keepMine", "keepTheirs", "keepBoth"],
                               "automaticMergeOfConflictingFields": False, "noteTextMergedWithinOneNote": False,
                               "everyConflictNeedsExplicitChoice": True, "keptCopyNeedsNewUnusedId": True,
                               "originalsReplacedUnderExistingId": False, "otherSideContentRewritten": False,
                               "derivedChangePermissionsRequired": True, "outOfScopeConflictsCountedNotNamed": True,
                               "contentIncludedByDefault": False, "newRevisionCopyOnly": True,
                               "exactRetryAcknowledged": True, "callerConstraintsAreAuthentication": False,
                               "peopleRecords": False},
        "fieldPermissions": {"version": 1, "schema": "urn:showpapers:field-policy:1",
                             "optional": True, "requiresExplicitScope": True,
                             "defaultGrant": False, "unknownFieldsRejected": True,
                             "noteContentIsWholeNativeRecord": True,
                             "hiddenFieldsOmittedFromReadsSearchAndPreviews": True,
                             "inversePermissionsRequired": True, "recheckedBeforePublication": True,
                             "callerConstraintsAreAuthentication": False,
                             "saveCopyRetainsUntouchedHiddenRecords": True},
        "connectionGrant": _connection_grant(),
        "localWorkspace": _local_workspace(),
        "adapters": [adapter] if (adapter := _local_mcp_adapter()) is not None else [],
        "optionalAdapterStatus": {"localMcpSourceFilesAvailable": adapter is not None,
                                  "runtimeDependenciesChecked": False,
                                  "description": "MCP is separate from the standard-library toolkit; only adapters whose source files are present are listed."},
        "execution": {"network": False, "existingOutputsOverwritten": False, "inputArchiveModified": False,
                      "standardOutput": "one-json-value", "successExitCode": 0, "failureExitCode": 2,
                      "invokeWithoutShellInterpolation": True, "platform": "POSIX with directory handles and hard links"},
        "authority": {"scope": "caller-supplied-constraints", "authenticatedGrants": False,
                      "archiveContentGrantsAccess": False, "cloudSharing": "caller must authorize content exposure to its AI client"},
        "privacy": {"contentIncludedByDefault": False, "idsAndDigestsAreSensitiveMetadata": True,
                    "analyticsPayload": False, "originalTextExtracted": False},
        "fullFidelity": {**fmt._fidelity().discovery(), "commandDescriptors": _fidelity_descriptors(path)},
        "unsupportedAppliesTo": "archive profiles 1-3 and operation protocols 1-4; profile 4 is described in fullFidelity",
        "unsupported": ["encrypted-backup", "full-vault-export", "live-phone-control", "remote-MCP-service",
                        "authenticated-permissions", "global-latest-revision", "cross-workspace-replay-prevention",
                        "automatic-merge", "OCR-details-and-review-history", "people-records"],
        "android": {"readableCollectionEditingSince": "0.45.0", "selectedImportSince": "0.46.0",
                    "stopUnfinishedImportSince": "0.47.0", "route": ["Add a Paper", "Open Collection", "Add To Library"],
                    "nativeLimitsApply": True, "noteContainingFoldersImported": False,
                    "extractDetails": "Read Again", "thisToolControlsPhone": False},
        "examples": {"create": fmt.local_resource("examples/ai_create_collection.py"),
                     "workflow": fmt.documentation_resource("SHOWPAPERS_AI_WORKFLOW.md"),
                     "currentCreate": fmt.local_resource("examples/create_checklist_collection.py")},
    }
