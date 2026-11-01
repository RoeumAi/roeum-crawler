"""Read-only ECS sidecar check; no scraper imports, DB writes or notifications."""
import concurrent.futures
import hashlib
import json
import os
import time

import requests


def main():
    url = os.environ['DOCUMENT_EXTRACTION_URL'].rstrip('/')
    expected_build = os.environ['EXPECTED_EXTRACTION_BUILD_ID']
    version = requests.get(url + '/version', timeout=10)
    version.raise_for_status()
    assert version.json()['build_id'] == expected_build, version.json()
    pids = []
    for case in json.loads(os.environ['EXTRACTION_SMOKE_CASES']):
        source = requests.get(case['url'], timeout=60)
        source.raise_for_status()
        assert hashlib.sha256(source.content).hexdigest()[:16] == case['hash']
        started = time.monotonic()
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(requests.post, url + '/api/extract-text',
                                     files={'file':(case['name'],source.content,'application/pdf')},timeout=880)
            health_checks = 0
            while not future.done():
                health = requests.get(url + '/health',timeout=5)
                health.raise_for_status()
                health_checks += 1
                time.sleep(1)
            response = future.result()
        response.raise_for_status()
        data = response.json()
        assert data['is_success'] and data['extraction_status'] == 'complete', data.get('extraction_status')
        assert data['page_count'] == case['pages'] and len(data['pages']) == case['pages']
        assert data['document_blocks'] and data['full_text'].strip()
        worker = data['extraction_metadata']['document_worker']
        assert worker['isolated'] and worker['pid'] not in pids
        pids.append(worker['pid'])
        health = requests.get(url+'/health',timeout=5).json()
        assert health['active_pid'] is None and health['pending']==0, health
        print('EXTRACTION_SMOKE '+json.dumps({'hash':case['hash'],'status':data['extraction_status'],
              'pages':data['page_count'],'chars':len(data['full_text']),'worker':worker,
              'health_checks':health_checks,'elapsed_s':round(time.monotonic()-started,2)},ensure_ascii=False),flush=True)
    print('EXTRACTION_SMOKE_COMPLETE '+str(len(pids)),flush=True)


if __name__ == '__main__':
    main()
