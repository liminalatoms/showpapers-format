# ShowPapers Format

`.showpapers` is a portable collection of original files, rich Notes, folders and related records. This repository contains the format documentation, a Python reader/writer, JSON Schemas, synthetic examples and an AI-agent skill.

[Protocol & Guides](https://protocol.showpapers.app/) · [ShowPapers App](https://showpapers.app/) · [Technical Paper](whitepaper/whitepaper.pdf)

## Try It

Use **Python 3.10 or later**. The tool and tests use only the standard library. Creating and editing files requires Linux or macOS with directory handles and hard links. Run these commands from this folder:

```sh
# Check a supplied synthetic collection.
python3 tool/showpapers_format.py validate examples/checklist.showpapers

# Create a new collection with native checklists, rich formatting and a paper link.
python3 tool/examples/create_checklist_collection.py --output new.showpapers

# Inspect the contents explicitly, then make an editable working copy.
python3 tool/showpapers_format.py inspect new.showpapers --include-content
python3 tool/showpapers_format.py edit new.showpapers --output-directory work

# Edit work/build.json or its payloads, then build and validate a new file.
python3 tool/showpapers_format.py create --spec work/build.json --output updated.showpapers
python3 tool/showpapers_format.py validate updated.showpapers
```

Outputs must be new paths. For a collection made from your own files, follow the [Agent Guide](docs/SHOWPAPERS_AGENT_GUIDE.md). When updating a collection, preserve IDs and original bytes, inspect its existing organization, and reuse suitable folders before creating new ones.

Readable collections expose their included contents to recipients. These tools validate structure, not document authenticity or factual accuracy. They do not connect to a phone or upload documents.

## For Developers

- [Specification](docs/SHOWPAPERS_SPECIFICATION.md), [Collection Format](docs/SHOWPAPERS_FULL_FIDELITY.md) and [AI Interchange](docs/SHOWPAPERS_AI_INTERCHANGE.md)
- [Schemas and runtime](tool/): use `formatVersion: 4` for current collections. Compatibility modules remain bundled for supported older files.
- [Synthetic test corpus](examples/corpus.json): valid and invalid current-format collections with expected outcomes.
- [Published whitepaper source](whitepaper/whitepaper-source.zip)

```sh
python3 tool/showpapers_format.py schema --kind build-spec --version 4
python3 tool/showpapers_format.py discover
python3 -m unittest discover -s tests -v
```

## For AI Agents

Use the root [SKILL.md](SKILL.md) to create, edit or validate collections. Its documentation and executable tools are included, so the folder is self-contained. You can ask an agent to read and use this local skill without installing it.

## License

[Apache License 2.0](LICENSE).
