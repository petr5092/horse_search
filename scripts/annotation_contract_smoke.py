#!/usr/bin/env python3
"""Offline fixtures by default; explicit --run exercises an existing local Go service."""
import argparse
import hashlib
import http.client
import json
import os
from pathlib import Path
import re
import struct
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request
import uuid
import zlib

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / 'docs/contracts/fixtures/annotation-v0.1'
MAX_CONTENT = 64 * 1024 * 1024


class SmokeError(Exception):
    pass


def require(condition, message):
    if not condition:
        raise SmokeError(message)


def read_json(path):
    return json.loads(path.read_text(encoding='utf-8'))


def validate(value, schema, schemas, path='$'):
    """Only the JSON Schema subset used by CreateTaskRequest (not a full validator)."""
    if '$ref' in schema:
        return validate(value, schemas[schema['$ref'].rsplit('/', 1)[1]], schemas, path)
    kind = schema.get('type')
    valid = {'object': isinstance(value, dict), 'array': isinstance(value, list),
             'string': isinstance(value, str),
             'integer': type(value) is int}.get(kind, False)
    require(valid, f'{path}: expected {kind}')
    if kind == 'object':
        props = schema.get('properties', {})
        require(set(schema.get('required', [])) <= value.keys(), f'{path}: missing fields')
        if schema.get('additionalProperties') is False:
            require(value.keys() <= props.keys(), f'{path}: unexpected fields')
        for key, item in value.items():
            if key in props:
                validate(item, props[key], schemas, f'{path}/{key}')
    elif kind == 'array':
        require(schema.get('minItems', 0) <= len(value) <= schema.get('maxItems', sys.maxsize),
                f'{path}: array length')
        if schema.get('uniqueItems'):
            require(len({json.dumps(x, sort_keys=True) for x in value}) == len(value),
                    f'{path}: duplicate entries')
        for index, item in enumerate(value):
            validate(item, schema['items'], schemas, f'{path}/{index}')
    elif kind == 'string':
        require(schema.get('minLength', 0) <= len(value) <= schema.get('maxLength', sys.maxsize),
                f'{path}: string length')
        if schema.get('format') == 'uuid':
            try:
                uuid.UUID(value)
            except ValueError:
                raise SmokeError(f'{path}: invalid UUID') from None
        if 'pattern' in schema:
            require(re.search(schema['pattern'], value) is not None, f'{path}: pattern')
    elif kind == 'integer':
        require(schema.get('minimum', -sys.maxsize) <= value <= schema.get('maximum', sys.maxsize),
                f'{path}: integer range')


def png_pixels(data):
    """Decode our RGB8/noninterlaced/filter-0 fixtures and check every chunk CRC."""
    require(data[:8] == b'\x89PNG\r\n\x1a\n', 'Invalid PNG signature')
    offset, compressed, header, ended = 8, bytearray(), None, False
    while offset < len(data):
        size = struct.unpack('>I', data[offset:offset + 4])[0]
        kind = data[offset + 4:offset + 8]
        payload = data[offset + 8:offset + 8 + size]
        crc = struct.unpack('>I', data[offset + 8 + size:offset + 12 + size])[0]
        require(zlib.crc32(kind + payload) == crc, 'PNG CRC mismatch')
        if kind == b'IHDR':
            header = struct.unpack('>IIBBBBB', payload)
        elif kind == b'IDAT':
            compressed.extend(payload)
        elif kind == b'IEND':
            ended = True
        offset += 12 + size
    require(ended and header == (640, 640, 8, 2, 0, 0, 0), 'Expected 640x640 RGB8 PNG')
    raw = zlib.decompress(compressed)
    require(len(raw) == 640 * 1921, 'Unexpected PNG pixel length')
    require(all(raw[y * 1921] == 0 for y in range(640)), 'Expected PNG filter 0')
    return raw


def check_fixtures():
    manifest = read_json(FIXTURES / 'manifest.json')
    create = read_json(FIXTURES / 'create-task.json')
    contract = read_json(ROOT / 'docs/contracts/annotation-service.openapi.json')
    schemas = contract['components']['schemas']
    require(manifest['contract_version'] == contract['info']['version'],
            'Fixture/OpenAPI contract version mismatch')
    validate(create, schemas['CreateTaskRequest'], schemas)
    require(manifest['contract_version'] == '0.1.0' and manifest['synthetic'] is True,
            'Unexpected fixture contract')
    require(manifest['actors'] == dict(admin=1, manager=2, annotator=3, reviewer=4), 'Actor mismatch')
    uuid.UUID(manifest['project_id'])
    frames = manifest['frames']
    require(len(frames) == 3 and len({f['session_id'] for f in frames}) == 3, 'Need three sessions')
    require(create['items'] == [f['image'] for f in frames], 'CreateTask image mismatch')
    require(create['assignee_id'] == 3 and create['reviewer_id'] == 4, 'Assignment mismatch')
    require(len({f['image']['image_version_id'] for f in frames}) == 3, 'Duplicate ImageVersion')
    require(len({f['image']['sha256'] for f in frames}) == 3, 'Duplicate image bytes')
    require([f['result'] for f in frames] == ['has_objects', 'has_objects', 'negative'], 'Result mismatch')
    for frame in frames:
        uuid.UUID(frame['session_id'])
        filename = frame['file']
        require(Path(filename).name == filename and filename.endswith('.png'), 'Unsafe fixture path')
        data = (FIXTURES / filename).read_bytes()
        require(hashlib.sha256(data).hexdigest() == frame['image']['sha256'], f'{filename}: SHA-256')
        require(frame['image']['width'] == frame['image']['height'] == 640, 'Fixture dimensions')
        raw = png_pixels(data)
        require(len(frame['boxes']) == (frame['result'] == 'has_objects'), 'Fixture box count')
        for box in frame['boxes']:
            require(0 <= box['xmin'] < box['xmax'] <= 640 and
                    0 <= box['ymin'] < box['ymax'] <= 640, 'Fixture coordinates')
            require(box['label_id'] == 'horse' and not box['occluded'] and not box['truncated'],
                    'Fixture box attributes')
            uuid.UUID(box['id'])
        # Check actual colored region, including the explicitly empty negative image.
        for y in range(640):
            for x in range(640):
                inside = any(b['xmin'] <= x < b['xmax'] and b['ymin'] <= y < b['ymax']
                             for b in frame['boxes'])
                pixel = bytes(raw[y * 1921 + 1 + x * 3:y * 1921 + 4 + x * 3])
                require(pixel == (b'\x28\x78\xc8' if inside else b'\xf5\xf5\xf5'),
                        f'{filename}: pixels differ from known rectangles')
    print('OK: 3 synthetic PNGs, dimensions/pixels/CRC/SHA-256, sessions and CreateTask schema')
    return manifest, create


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None  # Never forward the service token to another location.


class Client:
    def __init__(self, args, manifest, create):
        parts = urllib.parse.urlsplit(args.base_url)
        require(parts.scheme == 'http' and parts.hostname in ('localhost', '127.0.0.1', '::1')
                and not parts.username and not parts.password and not parts.query and not parts.fragment
                and parts.path in ('', '/'), '--base-url must be a local HTTP origin without credentials')
        self.base = args.base_url.rstrip('/')
        self.run_id = args.run_id
        self.token = os.environ.get('ANNOTATION_SERVICE_TOKEN')
        require(self.token and '\n' not in self.token and '\r' not in self.token,
                'Set ANNOTATION_SERVICE_TOKEN in the environment (not in .env)')
        self.actors = manifest['actors']
        self.prefix = f"/v1/projects/{manifest['project_id']}"
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
        digest = hashlib.sha256(json.dumps([manifest, create], sort_keys=True).encode()).hexdigest()
        self.journal_path = Path(args.state_dir) / f'{self.run_id}.json'
        self.journal = {'base_url': self.base, 'fixture_sha256': digest, 'operations': {}}
        if self.journal_path.exists():
            self.journal = read_json(self.journal_path)
            require(self.journal['base_url'] == self.base and self.journal['fixture_sha256'] == digest,
                    'Run journal belongs to another origin or fixture set; use a new --run-id')

    def key(self, name):
        return str(uuid.uuid5(self.run_id, name))

    def persist(self):
        self.journal_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.journal_path.with_suffix('.tmp')
        temporary.write_text(json.dumps(self.journal, sort_keys=True, indent=2) + '\n', encoding='utf-8')
        temporary.replace(self.journal_path)

    def request(self, method, path, role, body=None, status=200, raw=False):
        headers = {'Authorization': 'Bearer ' + self.token, 'X-Actor-ID': str(self.actors[role]),
                   'X-Actor-Role': role, 'X-Request-ID': self.key('trace:' + method + path),
                   'Accept-Encoding': 'identity', 'Accept': 'application/json'}
        encoded = None
        if body is not None:
            encoded = json.dumps(body, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode()
            headers['Content-Type'] = 'application/json'
        request = urllib.request.Request(self.base + self.prefix + path, encoded, headers, method=method)
        try:
            response = self.opener.open(request, timeout=15)
        except urllib.error.HTTPError as error:
            response = error
        except (urllib.error.URLError, TimeoutError, OSError):
            raise SmokeError(f'{method} {path}: network failure; repeat with the SAME --run-id and state directory') from None
        with response:
            require(response.status == status, f'{method} {path}: expected HTTP {status}, got {response.status}')
            require(response.headers.get('Content-Encoding', 'identity').lower() == 'identity',
                    f'{path}: compressed response despite identity request')
            try:
                data = response.read(MAX_CONTENT + 1)
            except (http.client.HTTPException, TimeoutError, OSError):
                raise SmokeError(f'{method} {path}: response interrupted; repeat with the SAME --run-id and state directory') from None
            require(len(data) <= MAX_CONTENT, f'{path}: response exceeds 64 MiB')
            require(response.headers.get_content_type() == 'application/json', f'{path}: expected JSON content type')
            if raw:
                return data, response.headers
            try:
                result = json.loads(data)
            except (ValueError, UnicodeError):
                raise SmokeError(f'{method} {path}: invalid JSON response') from None
            require(isinstance(result, dict), f'{path}: expected JSON object')
            return result

    def mutate(self, name, method, path, role, body, status):
        body = dict(body, request_id=self.key(name))
        entry = {'method': method, 'path': path, 'role': role, 'body': body}
        prior = self.journal['operations'].get(name)
        if prior:
            require(all(prior[k] == v for k, v in entry.items()), f'{name}: mutation body changed; stop')
        else:
            self.journal['operations'][name] = entry
            self.persist()  # Persist exact intent BEFORE sending a potentially committed operation.
        # A confirmed negative probe is skipped on resume: the task may already be frozen.
        if name == 'conflict' and prior and 'response' in prior:
            return prior['response']
        result = self.request(method, path, role, body, status)
        if prior and 'response' in prior:
            require(result == prior['response'], f'{name}: idempotent response changed')
        self.journal['operations'][name]['response'] = result
        self.persist()
        return result


def run(args, manifest, create):
    client = Client(args, manifest, create)
    print(f'Run ID: {client.run_id}; journal: {client.journal_path}')
    task = client.mutate('create', 'POST', '/tasks', 'manager', create, 201)
    task_id = task['id']
    uuid.UUID(task_id)
    require(task['project_id'] == manifest['project_id'] and task['task_revision'] == 1
            and task['state'] == 'in_progress' and task['item_count'] == 3, 'Initial task mismatch')
    task_path = '/tasks/' + task_id
    page = client.request('GET', task_path + '/items?limit=100', 'annotator')
    require(page['next_cursor'] is None and len(page['items']) == 3, 'Expected exactly three task items')
    mapping = {item['image_version_id']: item['task_item_id'] for item in page['items']}
    require(set(mapping) == {f['image']['image_version_id'] for f in manifest['frames']}
            and len(set(mapping.values())) == 3, 'Task item mapping mismatch')
    pins = []
    for frame in manifest['frames']:
        image_id = frame['image']['image_version_id']
        item_id = mapping[image_id]
        uuid.UUID(item_id)
        path = '/task-items/' + item_id
        context = client.request('GET', path + '/context', 'annotator')
        require(context['image'] == frame['image'] and context['label_schema'] == create['label_schema']
                and context['task_id'] == task_id and context['task_item_id'] == item_id
                and context['project_id'] == manifest['project_id'], 'Context mismatch')
        body = dict(expected_revision=0, result=frame['result'], boxes=frame['boxes'])
        name = 'save:' + image_id
        document = client.mutate(name, 'PUT', path + '/annotations', 'annotator', body, 200)
        repeated = client.mutate(name, 'PUT', path + '/annotations', 'annotator', body, 200)
        require(document == repeated and document['revision'] == 1
                and document['result'] == frame['result'] and document['boxes'] == frame['boxes']
                and document['task_item_id'] == item_id and document['image_version_id'] == image_id
                and document['created_by'] == 3 and document['issues'] == [], 'Saved document mismatch')
        pins.append(dict(task_item_id=item_id, annotation_revision=document['revision']))
    first = manifest['frames'][0]
    conflict = client.mutate('conflict', 'PUT', '/task-items/' + mapping[first['image']['image_version_id']]
                             + '/annotations', 'annotator',
                             dict(expected_revision=0, result=first['result'], boxes=first['boxes']), 409)
    require(conflict['error']['code'] == 'revision_conflict'
            and conflict['error']['details']['current_revision'] == 1, 'Expected revision_conflict')
    submission = client.mutate('submit', 'POST', task_path + '/submit', 'annotator',
                               dict(expected_task_revision=task['task_revision'], items=pins), 201)
    submitted_pins = [(item['task_item_id'], item['annotation_revision']) for item in submission['items']]
    expected_pins = {(item['task_item_id'], item['annotation_revision']) for item in pins}
    require(len(submitted_pins) == len(expected_pins) and set(submitted_pins) == expected_pins
            and submission['task_revision'] == 2
            and submission['state'] == 'submitted', 'Submission mismatch')
    submission_id = submission['id']
    uuid.UUID(submission_id)
    review = client.mutate('accept', 'POST', task_path + '/reviews', 'reviewer',
                           dict(expected_task_revision=submission['task_revision'], submission_id=submission_id,
                                decision='accept', warnings_acknowledged=True, comments=[]), 201)
    require(review['decision'] == 'accept' and review['reviewer_id'] == 4 and review['task_revision'] == 3
            and review['submission_id'] == submission_id, 'Review mismatch')
    uuid.UUID(review['id'])
    metadata = client.mutate('snapshot', 'POST', '/snapshots', 'manager',
                             dict(submission_ids=[submission_id]), 201)
    uuid.UUID(metadata['id'])
    content, headers = client.request('GET', '/snapshots/' + metadata['id'] + '/content', 'manager', raw=True)
    digest = hashlib.sha256(content).hexdigest()
    require(digest == metadata['sha256'] == headers.get('X-Content-SHA256'), 'Snapshot raw SHA-256 mismatch')
    snapshot = json.loads(content)
    require(snapshot['format_version'] == metadata['format_version'] == 1
            and snapshot['snapshot_id'] == metadata['id']
            and snapshot['project_id'] == metadata['project_id'] == manifest['project_id']
            and metadata['created_by'] == 2 and metadata['item_count'] == 3
            and metadata['box_count'] == 2 and len(snapshot['items']) == 3, 'Snapshot metadata mismatch')
    by_image = {item['image']['image_version_id']: item for item in snapshot['items']}
    require(set(by_image) == set(mapping), 'Snapshot image composition mismatch')
    require([i['image']['image_version_id'] for i in snapshot['items']] == sorted(mapping), 'Snapshot order')
    for frame in manifest['frames']:
        image_id = frame['image']['image_version_id']
        item = by_image[image_id]
        require(item['image'] == frame['image'] and item['label_schema'] == create['label_schema']
                and item['task_id'] == task_id and item['task_item_id'] == mapping[image_id]
                and item['submission_id'] == submission_id and item['review_id'] == review['id']
                and item['annotation_revision'] == 1 and item['result'] == frame['result']
                and item['boxes'] == frame['boxes'], 'Snapshot annotation composition mismatch')
    again, _ = client.request('GET', '/snapshots/' + metadata['id'] + '/content', 'manager', raw=True)
    require(again == content, 'Snapshot bytes changed between reads')
    print(f'OK: create, context, 3 saves/replays, conflict, submit, accept; snapshot 3 images/2 boxes/1 negative; SHA-256 {digest}')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--check-fixtures', action='store_true', help='Offline read-only check (default)')
    mode.add_argument('--run', action='store_true', help='Mutate an existing local Go service; requires --run-id')
    parser.add_argument('--base-url', default='http://127.0.0.1:8081', help='Local Go HTTP origin')
    parser.add_argument('--run-id', type=uuid.UUID, help='Stable UUID for the entire scenario and all retries')
    parser.add_argument('--state-dir', default=str(Path(tempfile.gettempdir()) / 'annotation-contract-smoke'),
                        help='Persistent run journals; retain for retries; no concurrent run with the same UUID')
    args = parser.parse_args()
    if args.run and args.run_id is None:
        parser.error('--run requires an explicit --run-id UUID; retain it for retries')
    try:
        manifest, create = check_fixtures()
        if args.run:
            run(args, manifest, create)
        return 0
    except SmokeError as error:
        print(f'ERROR: {error}', file=sys.stderr)
    except (KeyError, TypeError, ValueError, OSError, struct.error, zlib.error):
        print('ERROR: malformed fixture/response or local I/O failure; check contract and journal; secrets omitted',
              file=sys.stderr)
    return 1


if __name__ == '__main__':
    sys.exit(main())
