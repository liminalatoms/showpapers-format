# Collection Format

A `.showpapers` collection keeps original documents together with the information and organization built around them. It can contain:

- papers, their titles and types;
- the on-device reading;
- details, with evidence, confidence, origin and review;
- custom details and removed suggestions;
- dates and reminders;
- people;
- personal and built-in folders with their rules;
- pins;
- notes;
- relationships with their decisions.

It also marks which values came from an **external AI agent**, so that the apps can ask the person to review them.

The [specification overview](SHOWPAPERS_SPECIFICATION.md) explains where to start and what a reader must check. The AI-app rules (media type, alternate names, companion, changes documents) are in [AI interchange](SHOWPAPERS_AI_INTERCHANGE.md). A step-by-step guide for agents is in the [agent guide](SHOWPAPERS_AGENT_GUIDE.md).

Collections are **readable ZIP archives**: anyone who receives one can read its records and unprotected document contents. An original PDF’s own password protection remains in place. The optional [protected envelope](ENCRYPTED_COLLECTION_PROFILE.md) encrypts the archive. A collection is a selected snapshot of records, not a full-vault backup.

A collection may preserve a password-protected PDF original unchanged. Keeping the file does not make its pages available for preview or reading. PDF passwords are not stored in the collection format. Archive checks verify its declared bytes and media signature; they do not prove that locked pages can be read.

## Container Rules

Every collection follows these rules:

- **One archive.** A single `manifest.json` plus the declared payloads. No other entries and no directories.
- **ZIP entries.** Stored or deflated only. No extra fields, encryption, ZIP64, symlinks, backslashes, colons, absolute paths, `.` or `..`.
- **Paths.** Each path is derived from its record ID. Paths are never extracted to a filesystem.
- **IDs.** The collection, originals, Notes, folders, people, agents and relationships use canonical lowercase UUIDs, unique across those records. Paper records and readings reuse their original’s ID. Detail IDs follow the origin-specific rules below.
- **Payload checks.** Every payload's size, CRC and SHA-256 are verified, and every original's media signature.
- **Strict JSON.** Valid UTF-8, no byte-order mark, no duplicate keys, no `NaN`. Integers are canonical tokens, so `1.0` is refused.
- **Fail closed.** A truncated, corrupt, oversized or partly unsupported archive is refused as a whole, never partially imported.

Specification **1.0.0** defines this collection contract. The collection is edited with `edit` + `create` or a [changes document](SHOWPAPERS_AI_INTERCHANGE.md#changes-documents). Readers refuse unknown file versions rather than guessing.

## Entries

| Entry | Content | Schema |
| --- | --- | --- |
| `manifest.json` | Collection, writer, agents, originals and the index of every payload, notes, folders, people, relationships, pins and category icons | `urn:showpapers:format:manifest:4` |
| `originals/<id>.<pdf\|png\|jpg\|webp>` | Original bytes, one file per paper | media signature |
| `papers/<id>.json` | Paper record: everything about one paper except its bytes and title | `urn:showpapers:paper-record:1` |
| `readings/<id>.json` | The app's on-device reading of that paper | `urn:showpapers:reading:1` |
| `notes/<id>.json` | Note record: rich content, saved rows and paper links; its title is in the manifest | `urn:showpapers:scratch-record:1` |

A paper record and a reading use the ID of the original they describe. Both are optional, and each paper has at most one of each. In the manifest, `papers[]` and `readings[]` follow the order of `originals[]`.

## Optional Sections And Granularity

Only `format`, `formatVersion`, `protection`, `generator`, `collection` and `originals` are required in the manifest. Every other section may be left out:

- An omitted **array** means empty.
- An omitted **`library`** means no pins and no category icons.
- An omitted **paper record** means the file says nothing about that paper beyond its original and title.

Inside a paper record only `paperVersion` is required. Every other member has two states:

- **Omitted:** the file does not describe it. Importing into an existing paper leaves the library's value unchanged. A new paper gets the app's default.
- **Present:** this is the complete value, and an empty list means "none".

This lets a writer describe only what the exchange needs: one detail, or a record with an existing reading, reviewed details, people, a reminder and folder rules. External agents preserve existing readings and review decisions; their own additions follow the agent-origin rules.

**Order.** These arrays are ordered, and readers keep their order:

- `originals`, `notes`, `folders` and `library.pinnedPaperIds`;
- everything inside a note record;
- reading pages, blocks, lines, words and polygon points;
- a detail's `evidence`.

Every other array is a set: `details`, `removedSuggestions`, `personIds`, `type.suggested`, folder `paperIds` and `excludedPaperIds`, `relationships`, `reasons`, `agents`, `people` and `categoryIcons`. Readers must not give their order a meaning, and writers should emit a stable order.

Structured records are closed: an unknown member invalidates the file. The reading’s `sdkAnalysis` is an exception: it carries the reader’s own bounded result, as described under [Readings](#readings). A member that is defined as an object (`type`, `reminder`, a detail) always carries all of its keys; a value that does not apply is `null` or `[]`.

## Manifest

| Member | Type | Rule |
| --- | --- | --- |
| `format` | string | `showpapers` |
| `formatVersion` | integer | `4` |
| `protection` | string | `readable` |
| `generator` | object | `name` (1–80), `platform` (1–40), `version` (1–40). The program that wrote the file, for example `{"name":"Example Writer","platform":"example","version":"1.0.0"}`. It is not a signature. |
| `collection` | object | `id`, `revision` (1–2⁵³−1), `title` (1–200, trimmed). Optional `parent`: `revision` (lower than `revision`) and `archiveSha256` of the exact archive this revision was edited from. |
| `agents` | array ≤32 | External AI agents that proposed values: `id`, `name` (1–80), `platform` (0–40), `version` (0–40) |
| `originals` | array | Each record declares `id`, `title`, `path`, `mediaType`, `size` and `sha256`. Titles are 1–100 characters, trimmed, no control characters, no `/` or `\` |
| `papers` | array | `id`, `path` = `papers/<id>.json`, `size` (1–1 MiB), `sha256` |
| `readings` | array | `id`, `path` = `readings/<id>.json`, `size` (1–8 MiB), `sha256` |
| `notes` | array | `id`, `title`, `path`, `size`, `sha256`, `encoding: "utf-8"` and `paperIds`; one note record per note. `paperIds` is its first-occurrence reference union, as defined in [Notes](#notes) |
| `folders` | array ≤64 | Personal folders, see [Folders](#folders) |
| `people` | array ≤64 | `id`, `name` (1–80, trimmed, unique ignoring case), `isMe` (at most one `true`) |
| `relationships` | array ≤5000 | See [Relationships](#relationships) |
| `library` | object | `pinnedPaperIds` (ordered pins) and `categoryIcons` (≤64: `purpose`, `label`, `icon`) |

Originals, paper records, readings and Notes have no independent item-count ceiling. The manifest is at most 1 MiB, the ZIP central directory at most 2 MiB, all payloads together at most 100 MiB and the archive at most 103 MiB. Before allocating ZIP entry metadata, readers require a nonzero classic entry count no greater than the directory byte length divided by 46 (rounded down). ZIP64, split archives, undeclared paths and exceeded byte budgets are rejected; no records are silently truncated.

Character counts are Unicode scalars unless a limit says UTF-16 units; the apps count folder names and purposes in UTF-16 units. Every collection JSON document (manifest, paper record, reading, changes document) nests at most 32 levels deep. Readers accept any JSON within the byte limits and this depth, with no separate count of values: an 8 MiB reading can hold hundreds of thousands of them.

## Notes

A Note keeps its title and ID in the manifest and its saved content in `notes/<id>.json`. It can carry formatted text, checklists and paper references, along with record metadata. The note record has exactly these members: `recordVersion`, `template`, `iconId`, `createdAt`, `updatedAt`, `automaticTitle`, `items` and `document`.

- `recordVersion` is `1`. `template` and `iconId` use the note-record schema's supported vocabulary.
- Creation and update times are decimal millisecond strings; the update cannot precede creation. `automaticTitle` is a Boolean.
- `items` holds ordered saved rows. Each row preserves its ID, label, `checked` and `checkable` flags, and ordered `paperIds`.
- `document` is `null` or a rich-text object with exactly `noteVersion`, `text`, `marks`, `paperIds` and `paperLinks`. A null document is distinct from an empty one. Rows and a document may coexist; readers preserve both.

In rich content, `noteVersion` is `1`. Marks use half-open UTF-16 ranges: style `1` means bold and `2` italic. Inline paper links use the same ranges and name an original in the collection. Ranges must be nonempty, within the text, and at Unicode scalar boundaries; paper links cannot overlap. `paperIds` also retains attachment-only references, in first-occurrence order. The manifest's references are the first-occurrence union of saved rows and rich content. A rich document allows 640 paper IDs and 640 inline links; saved rows allow 80 items with eight paper IDs each. Their combined reference union has no separate 100-item ceiling. Every referenced paper must exist in the collection.

Bullets and checklist lines use exact line-start text prefixes: `• `, `☐ ` and `☑ `, each ending with an ordinary U+0020 space. For a native checklist, start the task immediately after `☐ ` (unchecked, U+2610) or `☑ ` (checked, U+2611) at the beginning of the text or just after LF; do not indent it. Android and iOS render these storage markers as native rounded, tappable check controls. Markdown `- [ ]`, alternative squares and markers inside prose are ordinary text. Text preserves whitespace, Unicode and line feeds; rich content allows tabs, but refuses carriage returns and other control characters. There is no automatic Markdown conversion or separate checklist-state field for rich text. See [native checklist authoring](SHOWPAPERS_AGENT_GUIDE.md#write-native-checklists-and-preserve-formatting) for a complete record and UTF-16 formatting rules. Complete vocabularies, byte limits and closed-object constraints are defined by the note-record and note-document JSON Schemas in the [specification index](SHOWPAPERS_SPECIFICATION.md).

## Paper Records

| Member | Meaning |
| --- | --- |
| `paperVersion` | `1` |
| `createdAt` | When the paper was added, as a decimal string of milliseconds since 1970 (strings keep 64-bit values exact in JavaScript) |
| `automaticTitle` | `true` while the title is still the one the app generated |
| `type` | `detected` (the reading's type, `null` if never read), `suggested` (other types the reading considered), `selected` (the selected type, chosen by the person or accepted under the receiving app’s policy, `null` if none), `custom` (the person's own type name, only when `selected` is `generic`), `agentSuggestion` (an external agent's proposed type awaiting review, or `null`) |
| `details` | Every detail of the paper, see [Details](#details) |
| `removedSuggestions` | Suggestions the person removed, remembered so they do not come back |
| `personIds` | The people this paper belongs to |
| `builtInFolder` | The built-in folder the person placed it in, or `null` for automatic placement |
| `reminder` | The paper's reminder or `null`, see [Reminders](#reminders) |

Types use these ids: `generic`, `i20`, `i94`, `passport`, `visa`, `i797`, `employment-authorization`, `drivers-license`, `social-security`, `birth-certificate`, `marriage-certificate`, `tax`, `insurance`. Use the exact identifiers above. For another type, use `generic` with a descriptive custom name in the appropriate person-selection or agent-suggestion field.

## Details

Every detail has the same twelve members: `id`, `origin`, `key`, `label`, `value`, `normalizedValue`, `valueType`, `concerns`, `evidence`, `confidence`, `agentId`, `review`. `key` is one of 78 field keys (for example `passport-number`, `expiration-date`, `other`). `valueType` is `text`, `date` or `duration-of-status`.

| `origin` | Who produced it | Rules |
| --- | --- | --- |
| `reading` | The app's on-device reading (OCR and on-device AI) | `id` matches `[a-z][a-z0-9_]{0,95}`; 1–16 evidence items, each a reading line `{page, block, line}` that exists in `readings/<id>.json`; `label` only for key `other`; `confidence` and `agentId` are `null`; `review` required |
| `person` | A custom detail the person typed | key `other`, `label` (0–80), `value` (0–500 characters), `valueType` `text`; no evidence, concerns, confidence, agent or review |
| `agent` | An external AI agent | `id` is a UUID; `agentId` names `manifest.agents`; `value` not blank; a `label` when the key is `other`; empty `concerns`; optional `confidence` (0–1); 0–16 evidence items, either reading lines or quotes `{page, quote, region}`; `review` required |

`review` is `{"confirmed": true|false, "correctedValue": string|null}`:

- `correctedValue` is the person's correction. The effective value is the correction when present, otherwise `value`.
- `confirmed` records acceptance in the receiving app: a person can confirm a detail against the original, or the app can accept an agent detail under its disclosed confidence policy. It is not proof of accuracy or identity. A confirmed detail has a non-blank effective value.
- An agent writer leaves its own details `confirmed: false`. The receiving app decides whether to accept them automatically or keep them pending.

Values and normalized values are at most 4096 UTF-8 bytes. An agent's date detail with a `normalizedValue` uses ISO `YYYY-MM-DD`. Reading concerns are `invalid-date`, `ambiguous-date`, `conflicting-values` and `read-by-ai` (the on-device AI model read this value from the page image; the on-device text recognizer did not independently confirm it, so review it against the original before relying on it).

There are no separate date fields. Dates are details with `valueType: "date"`. A paper's end date is its earliest confirmed `expiration-date`, `valid-until`, `program-end-date`, `admit-until` or `coverage-end-date`. The apps derive it, so a file never states it separately.

### Removed Suggestions

- `{"origin": "reading", "id", "identity"}` remembers a reading suggestion the person removed. `identity` is `null` or holds:
  - `key` and `valueType`;
  - `pages`: 1–16, ascending;
  - `sdkFieldId` and `sdkDocumentType`: both `null` or both set.
- `{"origin": "agent", "id", "agentId", "key", "label", "value"}` remembers an agent suggestion the person rejected, so importing the same proposal again does not bring it back.

A removed suggestion never shares an ID with a current detail.

## Readings

`readings/<id>.json` holds:

- `readingVersion` `1`;
- `extractorVersion` (1–100,000);
- `concerns`: any of `no-text`, `unrecognized-document`, `conflicting-document-types`, `no-supported-fields`, `field-limit-reached`;
- `pages[]`;
- `sdkAnalysis`.

Each page has:

- `index`: 0–199, unique;
- `width` and `height`: 1–20,000;
- `engine`: printable ASCII, at most 128 bytes;
- `text`: at most 256 KiB;
- `blocks[]`: at most 2,000.

Each block has `text` (≤64 KiB), `region` and `lines[]` (≤10,000). Each line has `text` (≤8 KiB), `region`, `confidence` and `words[]` (≤2,048). Each word has `text` (≤2,048 bytes), `region` and `confidence`.

A region is `{"box": [left, top, right, bottom] | null, "polygon": [[x, y], ...]}`:

- All numbers are 0–1, normalized to the rendered page.
- The box has left ≤ right and top ≤ bottom.
- The polygon has at most 8 points.

Confidence is `null` or 0–1. Words are optional detail: writers may leave `words` empty.

`sdkAnalysis` is the reader's own result: an object with a `sdkVersion` string, at most 64 members and 512 KiB as canonical JSON. Agents preserve an existing value unchanged. When creating a collection, agents do not invent a reading or reader result.

A paper's reading plus its reading and custom details must fit the apps' analysis store: 50,000 nodes and 2 MiB. The reference validator computes both exactly as the apps do, and counts `sdkAnalysis` at twice its compact size. It refuses a paper that does not fit with `limit`.

## Folders

A personal folder has:

- `id`: a canonical lowercase UUID;
- `name`: 1–80 UTF-16 units, trimmed, unique ignoring case among siblings;
- `color`: `blue`, `teal`, `green`, `amber`, `coral`, `purple` or `gray`;
- `icon`: one of 13 ids, or `null`;
- `purpose`: 0–240 UTF-16 units, trimmed;
- `rule`: `null` or an automatic filing rule;
- `paperIds`;
- optional `parentId`: `null` or omitted for Other Folders, `root` for the library root, a canonical lowercase UUID for another personal folder, or a built-in parent ID listed below.

Array order is retained among siblings. Preserve each folder's ID, name and parent when editing. Folder memberships refer to papers; Notes do not belong to folders. Across all folders there are at most 2,000 memberships and 2,000 exclusions.

A rule has:

- `documentTypes`: up to 11 exact reader document type ids, for example `ice.sevp.issued.i-20`;
- `autoAdd`: needs at least one document type;
- `excludedPaperIds`: papers the person removed from the folder. They are never also members.
- `condition`: `null`, `{"caseReceipt": "EAC2512345678"}`, or `{"reviewedDetails": [1–3 clauses]}`.

Each clause has a distinct `field` (16 names, six of them dates), an `operator` (`equals`, `before`, `on-or-before`, `after`, `on-or-after`) and a `value` (1–160). Only date fields use ordering operators, and their values are ISO dates.

Built-in folders are not stored as folders. A paper's `builtInFolder` records the person's explicit placement, one of: `identity`, `visas`, `notices`, `applications`, `residency`, `work-authorization`, `travel-entry`, `study`, `home`, `finance`, `health`, `education`, `work`, `family`, `legal`, `other`. `null` means the app places the paper automatically from its type.

The current apps present these leaf destinations in the following public taxonomy. Labels are presentation text; the leaf IDs above are paper placement values and also valid personal-folder parents.

| Navigation group | Leaf IDs and English labels |
| --- | --- |
| Top level | `identity` — Personal IDs |
| Visas & Immigration | `visas` — Visas; `notices` — Notices & Letters; `applications` — Applications; `residency` — Residency; `work-authorization` — Work Authorization; `travel-entry` — Travel & Re-Entry; `study` — Study Documents |
| Other Folders | `home` — Home; `finance` — Finance; `health` — Health; `education` — Education; `work` — Work; `family` — Family; `legal` — Legal; `other` — Other Papers |

A personal folder's `parentId` can name any of the 16 built-in leaf IDs above, `permits` (Visas & Immigration), or `other-folders` (Other Folders). These built-in destinations are not personal-folder records; `permits` and `other-folders` are not paper `builtInFolder` placement values. Choose Folder, Reading and Needs Attention remain virtual views.

Personal parents must resolve within `folders`; unknown parents, self-parenting and cycles are rejected. Omitted `parentId`, `null` and `other-folders` share the same sibling namespace; `root` is distinct. Two folders under different parents may have the same name. The total remains at most 64 personal folders, including ancestors. Existing flat collections remain valid. Selected exports include every personal ancestor needed to resolve the selected tree, even an ancestor with no exported member papers.

An export contains selected papers and selected organization. Both app writers limit folder memberships and rule exclusions to exported papers; a requested folder selection can omit other folders entirely. Neither an empty `folders` array nor a missing folder proves that the person's library has no such folder. An omitted `builtInFolder` does not describe placement; it is distinct from explicit `null` (automatic). Automatic filing can follow a paper's type even when no built-in override exists.

Agents adding papers must inspect the existing originals, IDs, hashes, folder parents, purposes, rules, exclusions and placements first, then reuse suitable folders by stable ID. Match purpose and content, not names alone. Preserve existing personal folders even if their labels resemble built-ins; do not merge or remove them without a requested change. For standard filing in a first-time package, use this public taxonomy rather than invent redundant personal categories; unseen personal folders remain unknown. See the [ordered add-papers workflow](SHOWPAPERS_AGENT_GUIDE.md#add-papers-to-an-existing-collection).

`library.categoryIcons` records the icon the person chose for a category:

- `purpose`: `identity`, `admission`, `approvals-and-receipts`, `study`, `work-permission`, `personal-records`, `tax`, `insurance`, `custom` or `unknown`;
- `label`: the custom type name for `custom`, otherwise `null`.

## Reminders

A paper has at most one reminder: `{"source", "detailId", "date", "leadDays", "enabled"}`.

- `source` is `detail` or `date`. A `detail` reminder names a date detail of the same paper whose key is `expiration-date`, `valid-until`, `program-end-date`, `admit-until` or `coverage-end-date`. A `date` reminder is on a date the person picked and has `detailId: null`.
- `date` is a real ISO calendar date.
- `leadDays` is `7`, `14` or `30`.

Delivered notifications and the global reminder switch are device state and settings. They are not carried.

## Relationships

A relationship joins two papers. Its members are:

- `id`;
- `type`: one of `same-case`, `different-stage`, `same-person`, `same-record`, `same-organization`, `supporting-document`, `renewal`, `amendment`, `subsequent-filing`, `duplicate`, `alternate-version`;
- `paperIds`: two different papers, sorted ascending;
- `referringPaperId`: the paper that points at the other, for direction, or `null`;
- `status`: `suggested`, `confirmed` or `rejected`;
- `origin`: `reading` (the app's rules), `person` or `agent`;
- `agentId`: required exactly for agent relationships;
- `reasons`: up to 16 strings of 1–500 characters;
- `decision`: `null` or `{"action": "accept"|"reject", "reason": 0–500}`. An accepted relationship is confirmed and a rejected one is rejected.

Duplicates the person kept apart are a `duplicate` relationship rejected with a reason such as "Keep both Papers". Case history and renewal history are views the apps compute from confirmed relationships. They are not stored separately.

A relationship's `id` is **derived**, so every implementation computes the same one, and it is stored in the relationship record for validation and reference during exchange. It is Java's `UUID.nameUUIDFromBytes`, an MD5-based version 3 UUID, of the UTF-8 text:

```text
showpapers:relationship:1:<paperIds[0]>:<paperIds[1]>:<type>
```

The reference tool's `relationship_id` and the fixtures show worked values.

## Provenance And Review

- **`generator`** names the program that wrote the file. The reference tool always writes itself; the apps write `ShowPapers`.
- **`agents[]`** names every external AI agent whose proposals the file carries. An agent's `id` is any UUID. The reference tool derives it from the agent's name, platform and version (`showpapers:agent:1:<name>\n<platform>\n<version>`), so proposals from the same agent share one entry.
- **Agent-origin values:**
  - details with `origin: "agent"`;
  - `type.agentSuggestion`;
  - relationships with `origin: "agent"`;
  - removed agent suggestions.

  Each names its agent.
- A file cannot prove who reviewed what. Review marks (`confirmed`, decisions) are claims made by the writer. Apps apply the trust rules in [Import](#import).

## Import

This section is normative for apps that implement the collection format. It is written so that Android and iOS behave identically.

### Opening

1. Recognize the file by content (ZIP signature plus a root `manifest.json` whose `format` is `showpapers`), never by name. See [accepted names](SHOWPAPERS_AI_INTERCHANGE.md#names-and-types).
2. Validate the entire archive before showing anything. Any error refuses the whole file with a message that names no content.
3. Show a preview: counts of papers, notes, folders, people, relationships and details, and how many values came from each agent and are pending review.

### Matching Records To The Library

| Record | Matches an existing library record when |
| --- | --- |
| Paper | Same ID **and** the stored original has the same SHA-256. Same ID with different bytes is a conflict: the file's paper is offered as a separate copy with a new ID. |
| Note, folder, person | Same ID. A person or folder with a different ID but the same name (ignoring case) is offered as "use existing", because the apps require unique names. |
| Relationship | Same derived ID, after mapping paper IDs |
| Detail | Same paper and same detail ID. A removed suggestion with that ID keeps it removed. |

A file ID that is already used by a different kind of record in the library gets a new ID. Every reference is rewritten consistently, and relationship IDs are derived again.

### Create

Unmatched records are created with the **file's IDs** (stable IDs survive a round trip), each field exactly as in the file. Omitted paper-record members take the app's defaults:

| Member | Default |
| --- | --- |
| `createdAt` | Import time |
| `type` | Unread, generic |
| Details, people, reminder | None |
| `builtInFolder` | Automatic |

A paper that arrives with a reading or details is not queued for automatic reading.

### Merge

For each matched record, the app compares every member the file **describes** with the library's value. It never deletes or blanks anything the file does not describe:

- Equal values change nothing.
- In ShowPapers, importing a collection applies the incoming updates for matching records without a per-record **Keep Mine** / **Use File** choice. A matching Note keeps its ID and receives the file's title and complete saved record, including its rich body, marks, checked state and Paper links. This can replace local edits, so writers must preserve every part the person did not ask them to change.
- Incoming fields still pass the receiving app's validation, admission and confidence rules below. Applying an update does not make a writer-supplied review mark trustworthy or silently accept every AI suggestion.
- A detail or type suggestion from an agent follows the receiving app's acceptance policy. In ShowPapers, a declared confidence strictly greater than `0.5` accepts the detail or fills an unset form type when the person chooses **Add To Library**. Missing confidence and values at or below `0.5` stay pending. An agent suggestion does not by itself override a saved type, a confirmed/corrected detail or a removed suggestion; an explicit incoming record update is handled by the app's import policy. Relationships have no confidence member and stay pending unless the person accepts them.
- Folder membership, pins and people assignments merge as sets. Pins keep the library's order and append new pins in the file's order.
- A file never deletes a library record. Deletions come only from a [changes document](SHOWPAPERS_AI_INTERCHANGE.md#changes-documents) and each one is reviewed.

### Returning An Edited Revision

Keep the same collection and record IDs when returning an edited export. `collection.parent` records the exact source revision and whole-archive SHA-256; the tool's `edit` workflow writes this lineage. It describes ancestry, not authorship, factual correctness or authenticated authority.

ShowPapers applies the incoming represented updates when the person imports the collection. It does not require a matching stored parent or an unchanged local Note before applying a matching Note's replacement, and does not show a local-versus-file review prompt. Missing or older local history does not turn an intended Note update into a silent keep-local result. Other receivers may disclose a different merge policy.

The incoming title and complete Note record must persist under the matched ID before the app reports an update. Paper updates remain subject to validation, source identity and the existing trust/confidence admission rules. Same-ID originals with different bytes still follow the separate-copy rule above; absent records are not deleted. An import summary counts records actually added, changed or already present after persistence. Reimporting an already-applied revision must not create duplicates or report identical records as updated.

### Trusting Review Marks

A file is not signed, so review marks are the writer’s claim. A receiving app may explicitly choose a backend import policy that preserves recorded review and acceptance decisions. `generator.name` is a writer-supplied label, not authentication, and cannot establish that a person reviewed a value. Current ShowPapers imports reset incoming review marks: reading details arrive unconfirmed, and incoming relationship decisions are not trusted as accepted or rejected library decisions. An external writer’s `confirmed: true` alone cannot accept its agent detail. This trust policy is distinct from the application’s confidence policy below; the protocol does not require a visible review-mark switch.

ShowPapers separately applies its disclosed **above-50% confidence policy** when the person adds the collection: agent details with a finite confidence in `(0.5, 1]` are accepted; an agent form type with that confidence fills an unset type. A form type already selected in the library or file is kept. Confidence is the writer's estimate, not measured accuracy. The agent's name and evidence remain attached to each detail after acceptance. Automatic acceptance of an agent suggestion is distinct from applying an explicit incoming record update; it does not grant the writer permission to forge confirmations.

This is application behavior, not a rule that all protocol consumers must auto-accept. A receiving app may use manual review or a different disclosed policy. The serialized contract needs no additional fields. Agent writers always leave their own details unconfirmed and report honest confidence; they must not raise a score to bypass review. Fields without a confidence member, including Notes, folders, people and relationships, do not acquire one implicitly.

Pending agent details do not feed reminders, folder rules or end dates. Once accepted, they can feed those features under the same date and value rules as other accepted details; acceptance does not turn an ambiguous date into a calendar date. Case history continues to depend on accepted relationships. People can inspect the original and correct or remove an accepted detail later.

### Round-Trip Requirements

A reader that implements the current collection format must meet these four conformance requirements. Application builds should be checked against them before claiming support.

1. **Valid means importable.** Every file the reference validator accepts is accepted by both apps. Every file it refuses is refused by both, with the same category. The collection's per-record limits are the apps' native limits, so nothing is truncated or silently cleaned. For example, a paper title containing `/` is refused rather than stripped.
2. **Nothing is lost.** After **Add to Library** into an empty library, exporting the same records again produces an equivalent collection:
   - the same IDs, titles, bytes and member values;
   - the same order of originals, notes, folders and pins.

   Only these may differ:
   - `generator`, `collection.revision` and `parent`;
   - the reading's `sdkAnalysis` formatting;
   - acceptance-state transformations explicitly selected by the receiving app's disclosed import policy: accepting an agent detail changes `review.confirmed`, and accepting an unset type moves its `kind` and `custom` values into `type.selected` and `type.custom` and clears `type.agentSuggestion`. The latter does not retain separate agent/confidence attribution for the selected type. Disabling policy acceptance preserves the incoming suggestion representation;
   - review marks reset by the receiving app’s disclosed import trust policy;
   - derived views. Among them are relationships with `origin: "reading"` that have no decision: the app's rules recompute them on the importing device, so they may appear, change or disappear. Every relationship the person or an agent made, and every decision, round-trips exactly.
3. **Same result on both platforms.** Android and iOS given the same file and the same choices produce the same library records, and export the same file bytes apart from `generator`.
4. **Capacity is explicit.** If the library cannot hold everything, the app lists what does not fit and lets the person deselect items; it never drops or truncates. The apps hold at most 24 notes, 64 people and 64 folders, and a valid file within these limits always fits an empty library. `validate` warns with `native-note-capacity` when a file has more notes than the apps hold.

## Creating And Editing A Collection

| Goal | How |
| --- | --- |
| Create from papers | Write a build spec (`formatVersion: 4`) that lists the original files and any records, then `create --spec build.json --output new.showpapers` |
| Change anything | `edit old.showpapers --output-directory work/`, edit the JSON files in `work/`, then `create --spec work/build.json --output new.showpapers`. The build spec already carries the next revision and the exact parent archive. |
| Propose specific changes | Write a [changes document](SHOWPAPERS_AI_INTERCHANGE.md#changes-documents), then `preview old.showpapers --changes changes.json` and `apply old.showpapers --changes changes.json --output new.showpapers` |
| Describe it for an AI app | `companion old.showpapers --output old.showpapers.json` |

Each collection workflow above validates the complete collection. Commands that produce files write a **new** file or directory; existing inputs and destinations are never overwritten.

## Not Included

These are deliberately absent. The contents statement lists these exclusions:

- `derived-views-and-caches`: thumbnails, search indexes, end dates, case and renewal histories, organization records, the country filter;
- `device-state`: device-specific revision stamps and storage metadata;
- `reading-queue`: reading jobs and their status;
- `notification-history`: delivered reminders;
- `import-history`: import ledgers and lineage;
- `conversations-and-ai-history`;
- `app-settings`;
- `keys-and-credentials`;
- `native-vault-envelopes`.

## Protected Collections

[Protected Files](ENCRYPTED_COLLECTION_PROFILE.md) wraps the exact bytes of a readable collection and authenticates and validates the inner archive before returning anything. The size caps are unchanged: 103 MiB readable, 104 MiB protected. A protected source saves as a protected copy.

## Conformance

The [example corpus](https://protocol.showpapers.app/examples) contains:

- one archive per feature and a combined archive;
- a changes document with the archive it produces;
- a companion describing a collection;
- 30 invalid archives that each break one rule, with the expected error code.

The apps' reader and writer tests must read every valid case with every value intact, and refuse every invalid one with its code. They must also re-export the valid cases to equivalent files.

Download cases from the [corpus index](https://protocol.showpapers.app/fixtures/index.json). Each entry names its file, SHA-256 and expected result; invalid cases include the expected error code. Verify the downloaded digest, then exercise your own implementation against every case. For example, with the extracted reference tool:

```sh
python3 showpapers-format/showpapers_format.py validate everything.showpapers
python3 showpapers-format/showpapers_format.py validate invalid-two-owners.showpapers
```

The first command succeeds. The second refuses the file with the corpus's expected error code and exit status 2. A reader must retain every admitted value; a writer's output must also pass complete archive validation. Passing JSON Schema checks alone is insufficient.
