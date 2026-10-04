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

The published whitepaper lives in `whitepaper/whitepaper.pdf` and `whitepaper/whitepaper-source.zip`. Submit manuscript or diagram changes with the corresponding regenerated PDF and complete source bundle. The bundle must compile independently using its included figure PDFs. Website maintainers then regenerate the HTML/SVG presentation from those same inputs and synchronize the reviewed commit. Do not update only the website's PDF or only the repository's manuscript. A packaging-only change to the source bundle's README does not require changing the paper's content or PDF.

Contributions use this repository's existing [Apache License 2.0](LICENSE).
