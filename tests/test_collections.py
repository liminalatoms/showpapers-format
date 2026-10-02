"""Self-contained collection tests using synthetic files; no network, app or account required."""
import base64
import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import zipfile

ROOT = Path(__file__).resolve().parents[1]
TOOL = ROOT / 'tool'
sys.path.insert(0, str(TOOL))
import showpapers_api as api
import showpapers_format as fmt

EXAMPLE_SPEC = importlib.util.spec_from_file_location('checklist_example', TOOL / 'examples/create_checklist_collection.py')
example = importlib.util.module_from_spec(EXAMPLE_SPEC)
EXAMPLE_SPEC.loader.exec_module(example)
CORPUS = json.loads((ROOT / 'examples/corpus.json').read_text())


class CollectionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='showpapers-format-test-')
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)

    def cli(self, *args):
        result = subprocess.run([sys.executable, str(TOOL / 'showpapers_format.py'), *map(str, args)],
                                cwd=self.base, capture_output=True, text=True, timeout=30)
        self.assertEqual('', result.stderr)
        return result.returncode, json.loads(result.stdout)

    def fixture(self, item):
        output = self.base / (item['name'] + '.showpapers')
        output.write_bytes(base64.b64decode(item['archiveBase64']))
        return output

    def checklist(self):
        output = self.base / 'checklist.showpapers'
        example.create_example(output)
        return output

    def test_all_valid_current_corpus_cases_validate_and_match_published_digests(self):
        for item in CORPUS['cases']:
            with self.subTest(case=item['name']):
                path = self.fixture(item)
                before = path.read_bytes()
                self.assertEqual(item['sha256'], hashlib.sha256(before).hexdigest())
                self.assertEqual(4, api.validate(path)['formatVersion'])
                self.assertEqual(2, api.contents(path)['contentsVersion'])
                self.assertEqual(before, path.read_bytes())

    def test_invalid_corpus_is_rejected_without_altering_input(self):
        for item in CORPUS['negative']:
            with self.subTest(case=item['name']):
                path = self.fixture(item)
                before = path.read_bytes()
                with self.assertRaises(fmt.FormatError) as error:
                    api.validate(path)
                self.assertEqual(item['expectedCode'], error.exception.code)
                self.assertEqual(before, path.read_bytes())

    def test_native_checklist_creation_preserves_rich_text_and_utf16_links(self):
        source = self.checklist()
        record = next(iter(api.inspect(source, include_content=True)['noteRecords'].values()))
        document = record['document']
        self.assertEqual(['☐ Pack water', '☑ Check tickets'], document['text'].splitlines()[1:3])
        self.assertEqual({1, 2}, {mark['style'] for mark in document['marks']})
        link = document['paperLinks'][0]
        encoded = document['text'].encode('utf-16-le')
        self.assertEqual('Open example paper', encoded[2 * link['start']:2 * link['end']].decode('utf-16-le'))
        self.assertGreater(link['start'], document['text'].index('Open example paper'))
        self.assertEqual([link['paperId']], document['paperIds'])

    def test_edit_create_updates_same_note_and_preserves_unedited_payloads(self):
        source = self.checklist()
        source_bytes = source.read_bytes()
        before = api.inspect(source, include_content=True)
        note_id, note = next(iter(before['noteRecords'].items()))
        work = self.base / 'work'
        api.edit(source, work)
        changed = copy.deepcopy(note)
        changed['document']['text'] = changed['document']['text'].replace('☐ Pack water', '☑ Pack water')
        changed['updatedAt'] = '1790900001000'
        (work / 'notes' / (note_id + '.json')).write_bytes(fmt.json_bytes(changed))
        output = self.base / 'updated.showpapers'
        api.create(work / 'build.json', output)
        after = api.inspect(output, include_content=True)
        self.assertEqual({note_id: changed}, after['noteRecords'])
        old_collection, collection = before['manifest']['collection'], after['manifest']['collection']
        self.assertEqual(old_collection['id'], collection['id'])
        self.assertEqual(old_collection['revision'] + 1, collection['revision'])
        self.assertEqual(hashlib.sha256(source_bytes).hexdigest(), collection['parent']['archiveSha256'])
        with zipfile.ZipFile(source) as old, zipfile.ZipFile(output) as new:
            for entry in old.namelist():
                if entry not in ('manifest.json', 'notes/' + note_id + '.json'):
                    self.assertEqual(old.read(entry), new.read(entry), entry)
        self.assertEqual(source_bytes, source.read_bytes())

    def test_changes_apply_and_stale_base_refusal(self):
        source = self.checklist()
        before = api.inspect(source, include_content=True)
        note_id, record = next(iter(before['noteRecords'].items()))
        changed = copy.deepcopy(record)
        changed['document']['text'] = changed['document']['text'].replace('☐ Pack water', '☑ Pack water')
        request = {'schema': 'urn:showpapers:changes:1',
                   'base': {'collectionId': before['manifest']['collection']['id'], 'revision': 1,
                            'archiveSha256': hashlib.sha256(source.read_bytes()).hexdigest()},
                   'generator': {'name': 'Synthetic Test', 'platform': 'local', 'version': '1'},
                   'summary': 'Mark water packed.',
                   'changes': [{'op': 'note.replace', 'noteId': note_id, 'title': 'Packing list', 'record': changed}]}
        changes = self.base / 'changes.json'
        changes.write_bytes(fmt.json_bytes(request))
        output = self.base / 'changed.showpapers'
        api.apply_changes(source, changes, output)
        self.assertEqual({note_id: changed}, api.inspect(output, include_content=True)['noteRecords'])
        request['base']['archiveSha256'] = '0' * 64
        changes.write_bytes(fmt.json_bytes(request))
        rejected = self.base / 'rejected.showpapers'
        with self.assertRaises(fmt.FormatError):
            api.apply_changes(source, changes, rejected)
        self.assertFalse(rejected.exists())

    def test_existing_output_is_never_overwritten(self):
        source = self.checklist()
        before = source.read_bytes()
        work = self.base / 'work'
        api.edit(source, work)
        with self.assertRaises(fmt.FormatError):
            api.create(work / 'build.json', source)
        self.assertEqual(before, source.read_bytes())

    def test_large_current_collection_preserves_every_record_and_payload(self):
        source = self.fixture(next(item for item in CORPUS['cases'] if item['name'] == 'many-complete-records'))
        before = api.inspect(source, include_content=True)
        self.assertEqual(121, len(before['manifest']['originals']))
        self.assertEqual(121, len(before['manifest']['notes']))
        work = self.base / 'work'
        api.edit(source, work)
        output = self.base / 'rebuilt.showpapers'
        api.create(work / 'build.json', output)
        with zipfile.ZipFile(source) as old, zipfile.ZipFile(output) as new:
            self.assertEqual(old.namelist(), new.namelist())
            for entry in old.namelist():
                if entry != 'manifest.json':
                    self.assertEqual(old.read(entry), new.read(entry), entry)

    def test_local_cli_and_current_schema_work_from_an_unrelated_directory(self):
        code, result = self.cli('validate', ROOT / 'examples/checklist.showpapers')
        self.assertEqual((0, True, 4), (code, result['ok'], result['formatVersion']))
        code, schema = self.cli('schema', '--kind', 'build-spec', '--version', '4')
        self.assertEqual(0, code)
        self.assertEqual('urn:showpapers:format:build-spec:4', schema['$id'])
        code, discovery = self.cli('discover')
        self.assertEqual(0, code)
        self.assertEqual(4, discovery['currentCollection']['formatVersion'])
        self.assertEqual([], discovery['adapters'])
        for field in ('documentation', 'agentGuide', 'example'):
            self.assertTrue((TOOL / discovery['currentCollection'][field]).is_file(), field)
        for entry in discovery['schemas'].values():
            self.assertEqual(entry['sha256'], hashlib.sha256((TOOL / entry['filename']).read_bytes()).hexdigest())


if __name__ == '__main__':
    unittest.main()
