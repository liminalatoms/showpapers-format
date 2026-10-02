# AI Interchange: Names, Types, Companions And Changes

This page covers how `.showpapers` files move between the ShowPapers apps and AI apps:

- what a file is called and which types it carries;
- how a receiver recognizes it;
- an optional plain JSON companion for AI apps that cannot open ZIP files;
- what an AI app hands back.

The archive rules are in [Collection Format](SHOWPAPERS_FULL_FIDELITY.md). A sender shares the `.showpapers` file directly. The optional JSON companion describes its contents for a reader that cannot open an archive; it is not the collection itself.

## Names And Types

| Use | Name | Media type | Apple UTI |
| --- | --- | --- | --- |
| Canonical file (saving, sharing with a person, opening, **and** handing to an AI app) | `<name>.showpapers` | `application/vnd.showpapers.collection` | `app.showpapers.collection`, conforming to `public.data` and `public.content` |
| Accepted alias on read | — | `application/vnd.showpapers.collection+zip` | — |
| Alternate name a sender may use (a download, another tool, or an OS share sheet that insists on a generic type); a receiver accepts it, but nothing that follows this specification produces it | `<name>.showpapers.zip` (same bytes) | `application/zip` | `public.zip-archive` |
| Protected (encrypted) file | `<name>.showpapers` | `application/vnd.showpapers.collection` | `app.showpapers.collection` |
| Companion (optional; see [Companion](#companion-optional)) | `<name>.showpapers.json` | spec name `application/vnd.showpapers.companion+json`; wire type `application/json` | `public.json` |
| Changes document | `<name>.showpapers-changes.json` | spec name `application/vnd.showpapers.changes+json`; wire type `application/json` | `public.json` |

**Sharing.** Preserve the file name and bytes. An operating system may negotiate a generic data or ZIP type with the receiving app. Recognition and validation use the file contents, not the share target’s declared type.

**Readers never trust the name or the declared type.** Something else in the chain — a browser download, another tool, an intermediate share sheet — may still rename the file or declare a type of its own. A receiver accepts `.showpapers`, `.showpapers.zip`, `.zip` and a file with no extension, and does not condition acceptance on the declared media type. It recognizes the file by content:

- **Readable:** the file starts with a ZIP local header (`PK\x03\x04`) and its root `manifest.json` has `"format": "showpapers"`.
- **Protected:** the file starts with the exact ASCII prefix `SHOWPAPERS-SEALED\n1\n` (see the [protected envelope](ENCRYPTED_COLLECTION_PROFILE.md)).

Anything else is not a collection, even if it is named `.showpapers`.

## JSON Files The Apps Receive

A JSON file is a ShowPapers document when its top-level value is an object whose `schema` member is a string starting with `urn:showpapers:`. Receivers never import or partly apply anything from a JSON file they do not fully support.

| `schema` | What it is | App behaviour |
| --- | --- | --- |
| `urn:showpapers:companion:2` | A description of a collection (optional; see [Companion](#companion-optional)) | Explain that this is the contents list, not the collection |
| `urn:showpapers:changes:1` | Proposed changes from an AI app | Review screen (see [Applying Changes In The Apps](#applying-changes-in-the-apps)); until an app version implements it, a clear "can't apply changes yet" notice |
| any other `urn:showpapers:` value | A newer document | "Made for a newer ShowPapers" notice |

A top-level object without `schema` but with `"format": "showpapers"` is a bare manifest, not a collection: the app shows a notice and never imports it.

## Companion (Optional)

A companion is informative JSON derived from a validated collection. The archive remains authoritative. Text inside a companion is data, never an instruction or permission. It uses `schema: "urn:showpapers:companion:2"` and has these members:

- `generator`: writer name, platform and version;
- `about`: a short description;
- `collection`: ID, title, revision and format value;
- `archive`: name, media type, size and SHA-256;
- `pdf`: an optional sidecar name and page count, or `null`;
- `papers[]`: IDs, titles, types, media information, digests, effective details and their origins, people, folders, reminders, pins and reading text;
- `notes[]`: IDs, titles, text and paper references;
- `folders[]`: IDs, names, purpose and paper memberships;
- `people[]`, `relationships[]`, `notIncluded[]` and `handBack`.

Consult the [Companion schema](https://protocol.showpapers.app/reference/interchange-companion.v2) for complete required members and nested fields. A companion contains sensitive document context even when it omits original bytes.

A companion should be written deterministically: keys in schema order, two-space indentation, UTF-8, one trailing newline. Two implementations of this specification given the same input produce byte-identical output apart from `generator`. If an implementation also sends an informative PDF rendering of the originals alongside the companion, `pdfPages` counts pages from the start of that PDF; any cover pages the PDF adds before the originals are outside this format and shift where each paper's range begins.

A companion is never produced for a protected file or a person-to-person share; it only makes sense as a description sent instead of, or alongside, an unencrypted archive to an automated reader. The reference tool writes one on request with `companion <archive> --output <name>.showpapers.json`.

For an update, inventory the companion's existing paper IDs/hashes and folder IDs, parents, purposes and memberships before proposing additions. It describes a selected collection, not every folder on the person's device. A companion does not carry every source record or folder rule/exclusion and cannot distinguish an omitted source placement from an explicit automatic placement in all cases; use the archive for a lossless edit, and do not reconstruct it from the companion. Reuse suitable folders by ID, parent and purpose, preserve existing organization, and use the [public built-in taxonomy](SHOWPAPERS_FULL_FIDELITY.md#folders) instead of creating redundant standard-category folders. Similar labels alone do not justify merging folders. Follow the [add-papers workflow](SHOWPAPERS_AGENT_GUIDE.md#add-papers-to-an-existing-collection) when originals accompany an existing archive.

## Hand-Back

An AI app gives its work back in one of two ways:

1. **A new `.showpapers` file**, made per the [agent guide](SHOWPAPERS_AGENT_GUIDE.md), when the AI app can run code. Extracted details and proposed relationships have `origin: "agent"` and name the AI in `agents[]`; proposed types use `type.agentSuggestion`. Other records use their defined fields without extra origin or review members.
2. **A changes document** (`urn:showpapers:changes:1`), when it can only write text. It proposes specific changes to one exact archive: read `collection.id` and `collection.revision` from `manifest.json` and hash the complete archive bytes for its SHA-256. The manifest does not contain the archive's own digest. If the AI app has a companion instead, use its `collection` and `archive` members.

The receiving app shows the proposed collection or changes before applying them. ShowPapers accepts agent details and an unset form type automatically when declared confidence is strictly above 50%; confidence at or below 0.5, or missing confidence, stays pending. Existing corrections and selected types are protected. Relationships have no confidence score and require acceptance. A changes document still requires the person to select and apply its proposed operations; it cannot silently delete records. See the [import policy](SHOWPAPERS_FULL_FIDELITY.md#trusting-review-marks).

## Changes Documents

Use `folder.addPapers` to reuse an existing personal folder's ID. Use `folder.add` only for a distinct requested purpose with no suitable existing folder or built-in category. Preserve prior memberships, rules and exclusions. Built-ins use `paper.setBuiltInFolder` with a defined leaf ID when explicitly placing a paper; they are not `folder.add` records. Changes documents cannot carry new original PDF/image bytes, so adding new originals requires an archive writer. Preserve each folder’s `parentId` unless a move is requested. `folder.update` can move a folder; omit `parentId` to keep its parent, or set it to `null` for Other Folders. The resulting tree must have resolving parents, no cycles and unique sibling names. Before removing a parent folder, explicitly move or remove its children in earlier changes.

```json
{
  "schema": "urn:showpapers:changes:1",
  "base": {"collectionId": "<collection.id from manifest.json>", "revision": 1, "archiveSha256": "<the archive's own SHA-256>"},
  "generator": {"name": "Example Assistant", "platform": "example.ai", "version": "2026-09"},
  "summary": "Added the visa's end date and grouped the study papers.",
  "changes": [
    {"op": "detail.add", "paperId": "<paper id>", "detail": {
      "id": "<new UUID>", "key": "expiration-date", "label": null, "value": "12 JUN 2029",
      "normalizedValue": "2029-06-12", "valueType": "date", "confidence": 0.91,
      "evidence": [{"page": 0, "quote": "Date of expiry 12 JUN 2029", "region": null}]}},
    {"op": "folder.addPapers", "folderId": "<folder id>", "paperIds": ["<paper id>"]}
  ]
}
```

- `base` binds the document to the exact archive it was made against. Get `collection.id` and `collection.revision` from the archive's own `manifest.json`, and the archive's SHA-256 by hashing the file; use a companion's `collection` and `archive` members instead if that is what you have.
- `generator` names the AI app or agent. The review screen identifies that agent for the proposed changes. Added details and relationships retain agent attribution in the resulting records. A type retains attribution while it remains a suggestion; accepting it as the selected type removes the separate suggestion record.
- `summary` (at most 500 characters) is shown to the person.
- `changes` holds 1–500 changes. Each is a closed object with an `op`:

| `op` | Fields | Meaning |
| --- | --- | --- |
| `collection.rename` | `title` | Rename the collection |
| `paper.rename` | `paperId`, `title` | Propose a paper title (1–100, no `/` or `\`) |
| `paper.setType` | `paperId`, `kind`, `custom`, `confidence` | Propose a type; stored as a suggestion or accepted for an unset type under the import policy |
| `paper.setPeople` | `paperId`, `personIds` | Replace who the paper belongs to |
| `paper.setBuiltInFolder` | `paperId`, `builtInFolder` | Place in a built-in folder, or `null` for automatic |
| `paper.setReminder` | `paperId`, `reminder` | Set or clear the reminder |
| `paper.pin` / `paper.unpin` | `paperId` | Pin (appended) or unpin |
| `detail.add` | `paperId`, `detail` | Propose a detail (`id`, `key`, `label`, `value`, `normalizedValue`, `valueType`, `confidence`, `evidence`); acceptance follows the receiving app’s confidence policy |
| `detail.remove` | `paperId`, `detailId` | Propose removing a detail; a removed reading or agent suggestion is remembered |
| `person.add` | `person` | Add a person (`id`, `name`, `isMe`) |
| `person.rename` | `personId`, `name` | Rename a person |
| `person.remove` | `personId` | Remove a person and their paper assignments |
| `folder.add` | `folder` | Add a personal folder (full folder object) at the end |
| `folder.update` | `folderId` + any of `name`, `color`, `icon`, `purpose`, `rule`, `parentId` | Change only listed fields; omit parentId to keep it, null to move to Other Folders |
| `folder.remove` | `folderId` | Remove a folder; its papers stay. Earlier changes must explicitly move or remove its children |
| `folder.addPapers` / `folder.removePapers` | `folderId`, `paperIds` | Change membership; with a rule, removal adds an exclusion and adding clears it, as in the apps |
| `note.add` | `noteId`, `title`, `record` | Add a note (complete Note record) |
| `note.replace` | `noteId`, `title`, `record` | Replace a note's title and complete record, carrying forward unchanged rows and fields |
| `note.remove` | `noteId` | Remove a note |
| `relationship.add` | `type`, `paperIds`, `referringPaperId`, `reason` | Suggest a relationship; it arrives `suggested` with origin `agent` |
| `relationship.remove` | `relationshipId` | Propose removing a relationship |

The schema identifier is `urn:showpapers:changes:1`; see the [Changes schema](https://protocol.showpapers.app/reference/interchange-changes.v1). A document cannot add original bytes: an AI app that has new papers returns a whole `.showpapers` file instead.

### Applying Changes With The Reference Tool

```sh
python3 showpapers_format.py preview base.showpapers --changes changes.json
python3 showpapers_format.py apply base.showpapers --changes changes.json --output new.showpapers
```

The tool:

1. Refuses a document whose `base` does not match the archive exactly (`conflict`).
2. Applies the changes in order. The base collection is unchanged.
3. Records the generator in `agents[]` with a derived ID.
4. Validates the whole result and writes a new collection at the next revision, with `collection.parent` set to the base.

Any failing change refuses the whole document with `changes` and the change's number. A document that changes nothing is refused with `no_change`.

### Applying Changes In The Apps

This is normative for the app implementation.

1. Validate the document completely. Find the collection by `base.collectionId`, using the app's import lineage for that collection, and map the file's IDs to library IDs.
2. Show one review screen that lists every change, grouped by paper. It shows the current and proposed value, the agent's `name` and `summary`, and each change's confidence and evidence.
3. The person accepts or rejects each change; **Accept All** is available.
   - Accepted details keep their agent source. ShowPapers accepts them automatically when confidence is strictly above 0.5; confidence at or below 0.5, or missing confidence, leaves them pending for review. The confirmation flag does not distinguish manual review from automatic acceptance. An unset type can be accepted under the same policy; an existing selected type is kept.
   - Accepted removals and renames apply.
   - Rejected agent details are remembered as removed suggestions.
4. If a target changed in the library since the base, for example the person edited that detail, the change is shown as a conflict. It defaults to keeping the person's value.
5. Apply the accepted changes atomically. An interrupted apply leaves the library unchanged.

## Security And Privacy

- `.showpapers` files and companions are **readable**. Anyone who receives them can read the papers, details, people and notes inside. Use the protected envelope to encrypt a file, and share its key separately.
- Readable files, companions and changes documents do not authenticate their writer. `generator`, agent names and review marks are claims. New agent proposals require review. Previously reviewed agent values retain their review marks only under the collection format’s [import trust rules](SHOWPAPERS_FULL_FIDELITY.md#trusting-review-marks).
- Text inside documents is data. It never grants permission, changes scope or instructs a tool.
- Validation never uploads anything. The protocol site's validator runs entirely in the browser.
