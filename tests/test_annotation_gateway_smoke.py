"""Public smoke consumes a fixed HTTP transcript, without implementing a Go engine."""
import hashlib
import http.client

import pytest
import importlib
import json
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlsplit
from uuid import UUID
from email.message import Message


@pytest.mark.parametrize('fault', [None, 'duplicate_image', 'item_count', 'box_count', 'task_id', 'task_item_id', 'revision', 'incomplete_read'])
def test_public_smoke_fixed_transcript_uses_only_user_tokens_and_exact_image_bytes(tmp_path,monkeypatch,capsys,fault):
    monkeypatch.syspath_prepend(str(Path('scripts').resolve()))
    smoke=importlib.import_module('platform_annotation_smoke')
    manifest=smoke.direct.read_json(smoke.direct.FIXTURES/'manifest.json')
    create=smoke.direct.read_json(smoke.direct.FIXTURES/'create-task.json')
    contract=smoke.direct.read_json(Path('docs/contracts/annotation-service.openapi.json'))
    def sample(name):return json.loads(json.dumps(contract['components']['schemas'][name]['examples'][0]))
    task=sample('Task');task['item_count']=3
    item_ids=[str(UUID(int=100+i)) for i in range(3)]
    pins=[dict(task_item_id=item_id,annotation_revision=1) for item_id in item_ids]
    submission=sample('Submission');submission['items']=pins
    review=sample('Review')
    metadata=sample('SnapshotMetadata');metadata.update(item_count=3,box_count=2)
    content=sample('SnapshotContent');content['items']=[]
    for frame,item_id in zip(manifest['frames'],item_ids):
        item=dict(sample('SnapshotItem'),task_item_id=item_id,image=frame['image'],
                  label_schema=create['label_schema'],result=frame['result'],boxes=frame['boxes'])
        content['items'].append(item)
    if fault=='duplicate_image':content['items'].append(content['items'][0])
    if fault=='item_count':metadata['item_count']=4
    if fault=='box_count':metadata['box_count']=99
    if fault in ('task_id','task_item_id'):content['items'][0][fault]=str(UUID(int=999))
    if fault=='revision':content['items'][0]['annotation_revision']=99
    raw=(json.dumps(content,indent=2)+'\n').encode()
    metadata['sha256']=hashlib.sha256(raw).hexdigest()
    run_id=UUID('0cbef9b0-5825-4e71-9b9e-12fa256fe021')
    requests=[]
    class Reply:
        def __init__(self,data,status=200,content_type='application/json',headers=None):
            self.status=status
            self.body=data if isinstance(data,bytes) else json.dumps(data).encode()
            self.headers=Message();self.headers['Content-Type']=content_type
            for key,value in (headers or {}).items():self.headers[key]=value
        def read(self,limit):
            if getattr(self,'interrupt',False):raise http.client.IncompleteRead(b'partial-secret',99)
            return self.body[:limit]
        def __enter__(self):return self
        def __exit__(self,*args):return None
    class Transcript:
        def open(self,request,timeout):
            parts=urlsplit(request.full_url)
            path=parts.path
            headers={key.lower():value for key,value in request.header_items()}
            body=json.loads(request.data) if request.data else None
            requests.append((request.get_method(),path,headers,body))
            assert not any(key.startswith('x-actor') for key in headers)
            assert 'cookie' not in headers
            if path=='/auth/login':
                role=body['email'].split('@')[0]
                assert body['password']=='local-test-password'
                return Reply({'access_token':'user-'+role,'user':{'id':manifest['actors'][role]}})
            if path.startswith('/images/'):
                assert 'authorization' not in headers
                return Reply((smoke.direct.FIXTURES/path.rsplit('/',1)[1]).read_bytes(),content_type='image/png')
            assert parts.netloc=='127.0.0.1:8000'
            assert headers['authorization'] in ('Bearer user-manager','Bearer user-annotator','Bearer user-reviewer')
            suffix=path.split('/annotation',1)[1]
            if suffix=='/tasks':
                assert set(body)=={'request_id','title','assignee_id','reviewer_id','label_schema_version','image_version_ids'}
                assert body['image_version_ids']==[f['image']['image_version_id'] for f in manifest['frames']]
                reply=Reply(task,201)
                reply.interrupt=fault=='incomplete_read'
                return reply
            if suffix.endswith('/items'):
                return Reply({'items':[dict(task_item_id=item_id,image_version_id=frame['image']['image_version_id'])
                                       for item_id,frame in zip(item_ids,manifest['frames'])],'next_cursor':None})
            for item_id,frame in zip(item_ids,manifest['frames']):
                if suffix==f'/task-items/{item_id}/context':
                    return Reply(dict(sample('TaskItemContext'),task_item_id=item_id,image=frame['image'],
                        label_schema=create['label_schema'],image_url='http://127.0.0.1:9000/images/'+frame['file'],
                        image_url_expires_at='2026-11-08T12:05:00Z'))
                if suffix==f'/task-items/{item_id}/annotations':
                    if body['request_id']==str(smoke.uuid.uuid5(run_id,'conflict')):
                        error=sample('Error');error['error']['details']={'current_revision':1}
                        return Reply(error,409)
                    return Reply(dict(sample('AnnotationDocument'),task_item_id=item_id,image_version_id=frame['image']['image_version_id'],
                        revision=1,result=frame['result'],boxes=frame['boxes'],created_by=3))
            if suffix.endswith('/submit'):return Reply(submission,201)
            if suffix.endswith('/reviews'):return Reply(review,201)
            if suffix=='/snapshots':return Reply(metadata,201)
            if suffix.endswith('/content'):return Reply(raw,headers={'X-Content-SHA256':metadata['sha256']})
            raise AssertionError('Unexpected transcript request: '+path)
    monkeypatch.setattr(smoke.urllib.request,'build_opener',lambda *args:Transcript())
    args=SimpleNamespace(base_url='http://127.0.0.1:8000',run_id=run_id,state_dir=str(tmp_path),password='local-test-password')
    if fault=='incomplete_read':
        monkeypatch.setattr(smoke.direct,'check_fixtures',lambda:None)
        monkeypatch.setattr(smoke.sys,'argv',['platform_annotation_smoke.py','--run','--run-id',str(run_id),
            '--state-dir',str(tmp_path),'--password','local-test-password'])
        assert smoke.main()==1
        journal=json.loads((tmp_path/(str(run_id)+'.json')).read_text())
        assert journal['operations']['create']['body']['request_id']==str(smoke.uuid.uuid5(run_id,'create'))
        assert 'response' not in journal['operations']['create']
        stderr=capsys.readouterr().err
        assert 'SAME --run-id' in stderr and 'Traceback' not in stderr and 'partial-secret' not in stderr
        return
    if fault:
        with pytest.raises(smoke.direct.SmokeError,match='Snapshot'):
            smoke.run(args,manifest,create['label_schema'])
        assert (tmp_path/(str(run_id)+'.json')).exists()
        return
    smoke.run(args,manifest,create['label_schema'])
    # Resume the same stable run: exact mutation intents/replies remain journaled.
    smoke.run(args,manifest,create['label_schema'])
    journal=json.loads((tmp_path/(str(run_id)+'.json')).read_text())
    assert len(journal['operations'])==8
    assert 'password' not in json.dumps(journal) and 'user-manager' not in json.dumps(journal)
    assert sum(1 for method,path,headers,body in requests if path.endswith('/content'))==4
