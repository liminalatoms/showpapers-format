# Contributing

Issues and pull requests are welcome for the format, tools, documentation, examples and agent skill in this repository.

## Report an Issue

Include the command or steps, expected and actual results, Python version, operating system and repository revision. Attach a minimal reproducer made from **synthetic documents only**. Do not include personal papers, passwords or credentials.

## Make a Change

Keep the change focused and explain the behavior it fixes. Add a regression test when behavior changes, and run:

```sh
python3 -m unittest discover -s tests -v
```

- Preserve stable IDs, unchanged original bytes, existing folder structure and native checklist formatting, including checked state and rich-text ranges.
- Keep supported older collections readable and preserve compatibility contracts. Update schemas, runtime behavior, documentation, `SKILL.md` and examples together when a change affects them.
- Validate generated collections with the reference tool. State which checks passed and any remaining limitations in the pull request.

## Publishing

The protocol website publishes a pinned, reviewed revision separately. Merging a pull request does not deploy the site; maintainers synchronize its documentation, tools, examples and downloads before publication. This repository's scope does not imply that the mobile apps or other projects are open source.

Contributions use this repository's existing [Apache License 2.0](LICENSE).
