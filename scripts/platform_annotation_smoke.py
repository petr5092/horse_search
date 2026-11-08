#!/usr/bin/env python3
"""Public FastAPI S1 smoke: offline fixtures by default, --run explicitly mutates localhost.

No app imports, service tokens, cookies, or cloud URLs. Keep --run-id and journals
for retries after uncertain outcomes. The real Go implementation is required for --run.
"""
import argparse
import hashlib
import http.client
import json
import os
from pathlib import Path
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request
import uuid

import annotation_contract_smoke as direct


class Client(direct.Client):
    def __init__(self, args, manifest, schema):
        parts = urllib.parse.urlsplit(args.base_url)
        direct.require(parts.scheme == 'http' and parts.hostname in ('localhost', '127.0.0.1', '::1')
                       and not parts.username and not parts.password and not parts.query and not parts.fragment
                       and parts.path in ('', '/'), '--base-url must be a local HTTP origin')
        self.base = args.base_url.rstrip('/')
        self.prefix = '/api/projects/' + manifest['project_id'] + '/annotation'
        self.run_id = args.run_id
        self.actors = manifest['actors']
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), direct.NoRedirect())
        self.tokens = {}
        password = args.password or os.environ.get('S1_SEED_PASSWORD', 's1-local-password-change-me')
        for role in ('manager', 'annotator', 'reviewer'):
            request = urllib.request.Request(self.base + '/auth/login',
                json.dumps({'email':role + '@s1.example', 'password':password}).encode(),
                {'Content-Type':'application/json'}, method='POST')
            with self.opener.open(request, timeout=15) as response:
                login = json.loads(response.read(direct.MAX_CONTENT + 1))
            direct.require(login['user']['id'] == self.actors[role], 'Seed actor does not match manifest')
            self.tokens[role] = login['access_token']
        digest = hashlib.sha256(json.dumps([manifest, schema], sort_keys=True).encode()).hexdigest()
        self.journal_path = Path(args.state_dir) / f'{self.run_id}.json'
        self.journal = {'base_url':self.base, 'fixture_sha256':digest, 'operations':{}}
        if self.journal_path.exists():
            self.journal = direct.read_json(self.journal_path)
            direct.require(self.journal['base_url'] == self.base and self.journal['fixture_sha256'] == digest,
                           'Journal belongs to another origin/fixtures; use a new --run-id')

    def request(self, method, path, role, body=None, status=200, raw=False):
        # Reuse the direct helper's bounded, no-redirect HTTP and journal semantics;
        # its request layer is overridden completely to send only a user JWT.
        headers = {'Authorization':'Bearer ' + self.tokens[role], 'Accept-Encoding':'identity',
                   'Accept':'application/json'}
        data = None
        if body is not None:
            data = json.dumps(body, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode()
            headers['Content-Type'] = 'application/json'
        request = urllib.request.Request(self.base + self.prefix + path, data, headers, method=method)
        try:
            response = self.opener.open(request, timeout=30)
        except urllib.error.HTTPError as error:
            response = error
        with response:
            direct.require(response.status == status, f'{method} {path}: expected {status}, got {response.status}')
            direct.require(response.headers.get('Content-Encoding', 'identity').lower() == 'identity', 'Unexpected coding')
            data = response.read(direct.MAX_CONTENT + 1)
            direct.require(len(data) <= direct.MAX_CONTENT, 'Response exceeds 64 MiB')
            direct.require(response.headers.get_content_type() == 'application/json', 'Expected JSON')
            if raw:
                return data, response.headers
            return json.loads(data)

    def download_image(self, url, frame):
        parts = urllib.parse.urlsplit(url)
        direct.require(parts.scheme == 'http' and parts.hostname in ('localhost', '127.0.0.1', '::1')
                       and not parts.username and not parts.password, 'Image URL must be local HTTP')
        # Never attach the platform JWT to an S3 URL.
        with self.opener.open(urllib.request.Request(url, headers={'Accept-Encoding':'identity'}), timeout=30) as response:
            direct.require(response.headers.get_content_type() == 'image/png', 'Expected editor PNG')
            direct.require(response.headers.get('Content-Encoding', 'identity').lower() == 'identity', 'Unexpected image coding')
            data = response.read(direct.MAX_CONTENT + 1)
        direct.require(len(data) <= direct.MAX_CONTENT and hashlib.sha256(data).hexdigest() == frame['image']['sha256'],
                       'Editor image SHA-256 mismatch')
        direct.png_pixels(data)


def run(args, manifest, schema):
    client = Client(args, manifest, schema)
    print(f'Run ID: {client.run_id}; journal: {client.journal_path}')
    create = dict(title='Platform S1 smoke', assignee_id=manifest['actors']['annotator'],
                  reviewer_id=manifest['actors']['reviewer'], label_schema_version=schema['version'],
                  image_version_ids=[f['image']['image_version_id'] for f in manifest['frames']])
    task = client.mutate('create', 'POST', '/tasks', 'manager', create, 201)
    task_path = '/tasks/' + task['id']
    items = client.request('GET', task_path + '/items?limit=100', 'annotator')
    mapping = {item['image_version_id']:item['task_item_id'] for item in items['items']}
    direct.require(set(mapping) == set(create['image_version_ids']) and items['next_cursor'] is None, 'Task image composition')
    pins = []
    for frame in manifest['frames']:
        image_id = frame['image']['image_version_id']
        path = '/task-items/' + mapping[image_id]
        context = client.request('GET', path + '/context', 'annotator')
        direct.require(context['image'] == frame['image'] and context['label_schema'] == schema, 'Context metadata mismatch')
        client.download_image(context['image_url'], frame)
        body = dict(expected_revision=0, result=frame['result'], boxes=frame['boxes'])
        name = 'save:' + image_id
        saved = client.mutate(name, 'PUT', path + '/annotations', 'annotator', body, 200)
        repeated = client.mutate(name, 'PUT', path + '/annotations', 'annotator', body, 200)
        direct.require(saved == repeated and saved['revision'] == 1 and saved['boxes'] == frame['boxes']
                       and saved['created_by'] == manifest['actors']['annotator'], 'Save/replay mismatch')
        pins.append({'task_item_id':mapping[image_id], 'annotation_revision':saved['revision']})
    frame = manifest['frames'][0]
    conflict = client.mutate('conflict', 'PUT', '/task-items/' + mapping[frame['image']['image_version_id']] + '/annotations',
        'annotator', dict(expected_revision=0, result=frame['result'], boxes=frame['boxes']), 409)
    direct.require(conflict['error']['code'] == 'revision_conflict'
                   and conflict['error']['details']['current_revision'] == 1, 'Expected revision conflict')
    submission = client.mutate('submit', 'POST', task_path + '/submit', 'annotator',
        dict(expected_task_revision=task['task_revision'], items=pins), 201)
    review = client.mutate('accept', 'POST', task_path + '/reviews', 'reviewer',
        dict(expected_task_revision=submission['task_revision'], submission_id=submission['id'],
             decision='accept', warnings_acknowledged=True, comments=[]), 201)
    direct.require(review['reviewer_id'] == manifest['actors']['reviewer'] and review['decision'] == 'accept', 'Review mismatch')
    metadata = client.mutate('snapshot', 'POST', '/snapshots', 'manager', dict(submission_ids=[submission['id']]), 201)
    content, headers = client.request('GET', '/snapshots/' + metadata['id'] + '/content', 'manager', raw=True)
    direct.require(hashlib.sha256(content).hexdigest() == metadata['sha256'] == headers.get('X-Content-SHA256'), 'Raw snapshot hash')
    snapshot = json.loads(content)
    direct.require(snapshot['snapshot_id'] == metadata['id'] and snapshot['project_id'] == manifest['project_id'], 'Snapshot identity')
    snapshot_items = snapshot['items']
    direct.require(len(snapshot_items) == metadata['item_count'] == len(mapping), 'Snapshot item count mismatch')
    direct.require(sum(len(item['boxes']) for item in snapshot_items) == metadata['box_count'], 'Snapshot box count mismatch')
    by_image = {item['image']['image_version_id']:item for item in snapshot_items}
    direct.require(len(by_image) == len(snapshot_items) and set(by_image) == set(mapping), 'Snapshot image IDs must be unique and complete')
    pinned_revisions = {pin['task_item_id']:pin['annotation_revision'] for pin in pins}
    for frame in manifest['frames']:
        item = by_image[frame['image']['image_version_id']]
        direct.require(item['image'] == frame['image'] and item['boxes'] == frame['boxes']
                       and item['result'] == frame['result'] and item['label_schema'] == schema
                       and item['task_id'] == task['id']
                       and item['task_item_id'] == mapping[frame['image']['image_version_id']]
                       and item['annotation_revision'] == pinned_revisions[item['task_item_id']]
                       and item['submission_id'] == submission['id'] and item['review_id'] == review['id'], 'Snapshot content mismatch')
    again, _ = client.request('GET', '/snapshots/' + metadata['id'] + '/content', 'manager', raw=True)
    direct.require(content == again, 'Snapshot bytes changed')
    print('OK: public login/create/context/PNG/save/replay/conflict/submit/review/raw snapshot through FastAPI')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--run', action='store_true')
    mode.add_argument('--check-fixtures', action='store_true')
    parser.add_argument('--base-url', default='http://127.0.0.1:8000')
    parser.add_argument('--manifest', type=Path, default=direct.FIXTURES / 'manifest.json')
    parser.add_argument('--password', help='Local seed password (prefer S1_SEED_PASSWORD environment)')
    parser.add_argument('--run-id', type=uuid.UUID)
    parser.add_argument('--state-dir', default=str(Path(tempfile.gettempdir()) / 'platform-annotation-smoke'))
    args = parser.parse_args()
    if args.run and args.run_id is None:
        parser.error('--run requires --run-id; retain it and the journal for retries')
    try:
        direct.check_fixtures()
        manifest = direct.read_json(args.manifest)
        schema = manifest.get('label_schema') or direct.read_json(args.manifest.with_name('create-task.json'))['label_schema']
        if args.run:
            run(args, manifest, schema)
        else:
            print('OK: public smoke inputs checked offline; no HTTP requests sent')
        return 0
    except (direct.SmokeError, KeyError, TypeError, ValueError, OSError, http.client.HTTPException):
        # Login errors may contain request/credentials; never print arbitrary exception text.
        print('ERROR: public smoke failed; check local services, fixtures and journal; repeat with SAME --run-id. Secrets omitted.', file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
