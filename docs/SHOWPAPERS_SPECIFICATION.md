# Specification 1.0.0

A `.showpapers` collection keeps original documents and the context built around them in one portable file. This is the first public specification.

Start with [Collection Format](SHOWPAPERS_FULL_FIDELITY.md) for record semantics, [AI Interchange](SHOWPAPERS_AI_INTERCHANGE.md) for naming and proposed changes, and [Protected Files](ENCRYPTED_COLLECTION_PROFILE.md) for encryption. The [Schema Reference](https://protocol.showpapers.app/reference) provides the complete machine-readable constraints.

## Start Here

- **Use a collection:** follow [Quick Start](https://protocol.showpapers.app/quick-start), then [Open Your File](https://protocol.showpapers.app/open).
- **Create one with an agent:** follow the [Agent Guide](SHOWPAPERS_AGENT_GUIDE.md).
- **Build a reader or writer:** read [Collection Format](SHOWPAPERS_FULL_FIDELITY.md) and use the [Schema Reference](https://protocol.showpapers.app/reference).
- **Return proposed edits:** read [AI Interchange](SHOWPAPERS_AI_INTERCHANGE.md) and [Hand-Back for AI Apps](https://protocol.showpapers.app/hand-back).
- **Test your implementation:** use the [example collections](https://protocol.showpapers.app/examples) and [validator](https://protocol.showpapers.app/validate).

## Archive Contents

| Entry | Contents |
| --- | --- |
| `manifest.json` | Collection identity, writer, payload inventory and optional organization |
| `originals/<id>.<pdf\|png\|jpg\|webp>` | Exact original file bytes |
| `papers/<id>.json` | Paper details, provenance, review state and organization |
| `readings/<id>.json` | On-device text recognition and reading evidence |
| `notes/<id>.json` | Formatted Note content, saved rows and paper references |

Only declared entries are allowed. Every payload has a canonical path, declared size and SHA-256. Stable IDs bind its references to the collection. See [Collection Format](SHOWPAPERS_FULL_FIDELITY.md) for required and optional manifest members, vocabulary, ordering and import rules.

## Practical Limits

A collection has no independent count ceiling for original files or Notes, with up to 64 people and 64 personal folders. The manifest is limited to 1 MiB and the ZIP central directory to 2 MiB. Entry counts must fit the directory bytes before readers allocate entry metadata. Each original can be up to 20 MiB. Original files and record payloads together are limited to 100 MiB; the complete readable archive is limited to 103 MiB.

A paper record can be up to 1 MiB and a reading up to 8 MiB. JSON nesting is limited to 32 levels. The [complete limits in JSON](https://protocol.showpapers.app/limits.json) give exact byte counts and the finer record limits used by the reference validator. The receiving app also needs enough library capacity for the selected records.

## Reader Obligations Beyond The Schemas

Schema validation alone never establishes that an archive is valid. A conforming reader also:

1. Refuses any format or payload version outside the contracts defined in the record reference, and refuses unknown keys in structured records and duplicate JSON keys everywhere. The reading’s bounded `sdkAnalysis` object carries reader-specific data under its documented rules.
2. Accepts integers only as canonical JSON decimal tokens: no fraction, exponent or leading zero. `1.0` is refused even though a JSON Schema validator accepts it as an integer. `NaN`, `Infinity` and a byte-order mark are refused; JSON must be valid UTF-8.
3. Requires the ZIP to contain exactly `manifest.json` plus every declared payload path and nothing else: no directories, duplicates, symlinks, encryption, extra fields, ZIP64, split archives, backslashes, colons, absolute paths, empty, `.` or `..` segments. Local headers must agree with the central directory. Stored and deflated entries, with or without data descriptors, are accepted.
4. Derives every payload path from the record ID and media type and refuses any other `path` value. Archive paths are never extracted to a filesystem.
5. Verifies for every payload the ZIP size, the manifest `size`, the actual byte count, the ZIP CRC and the manifest `sha256`, then the media signature or Note payload rules.
6. Requires collection and record UUIDs to be canonical lowercase and unique in the scopes defined by the collection contract. Reading-detail IDs use their own defined text pattern. Every reference must resolve inside the archive, and reference lists must not repeat an ID.
7. Fails closed as a whole. A truncated, corrupt or partly unsupported archive yields an error, never a partial collection, a dropped record or an empty replacement.

## Creating And Editing

Download the [reference tool](https://protocol.showpapers.app/downloads), then run:

```sh
python3 showpapers_format.py validate collection.showpapers
python3 showpapers_format.py contents collection.showpapers
python3 showpapers_format.py create --spec build.json --output new.showpapers
python3 showpapers_format.py edit collection.showpapers --output-directory work
python3 showpapers_format.py create --spec work/build.json --output edited.showpapers
```

For targeted proposals, write a [changes document](SHOWPAPERS_AI_INTERCHANGE.md#changes-documents), preview it against its exact base, then save a new copy:

```sh
python3 showpapers_format.py preview collection.showpapers --changes changes.json
python3 showpapers_format.py apply collection.showpapers --changes changes.json --output changed.showpapers
```

Inputs and existing destinations are never overwritten. A failed change refuses the whole proposal. Successful edits increment the collection revision and record its parent digest. Independent copies can diverge; a revision number is not a global latest-version service.

## Error Categories

A refusal includes a short code. For example, `archive` identifies a container problem, `digest` a payload mismatch, `reference` a broken link between records, and `limit` an exceeded bound. `changes` identifies an invalid proposal; `conflict` means its exact base does not match. `version` means the reader does not support an identifier in the file.

The [invalid examples](https://protocol.showpapers.app/examples#invalid) show the expected code for each broken rule. The reference tool reports an error object and exits with code 2. It does not include document content in error messages.

## Conformance

Implementations must preserve admitted records, IDs, references, order and original bytes, and refuse the complete input when any required check fails. Test against the [valid and invalid example corpus](https://protocol.showpapers.app/examples), including its expected error codes, then validate your output with the reference tool.

Schema checks alone do not establish complete conformance. Container structure, payload digests, cross-record references, media signatures and semantic rules must also pass. Validation establishes internal consistency, not document authenticity or the identity of a writer.

## Implementation Support

Use the exact field values and schema identifiers documented in the record reference. They identify file structures; specification **1.0.0** is the public release label.

A receiving application must implement the required contracts and have capacity for the chosen records. See [App Support](https://protocol.showpapers.app/changelog#app-support). Unknown structures must be refused rather than guessed or silently discarded.
