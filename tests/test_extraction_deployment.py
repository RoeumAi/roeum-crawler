"""Guard against a later crawler deploy silently routing back to the chat service."""
import json
from pathlib import Path
import unittest


class ExtractionDeploymentTest(unittest.TestCase):
    def test_crawler_waits_for_isolated_sidecar_and_has_no_chat_route(self):
        root=Path(__file__).resolve().parents[1]
        task=json.loads((root/'ecs/task-definition.json').read_text())
        containers={c['name']:c for c in task['containerDefinitions']}
        crawler,worker=containers['crawler'],containers['extraction-worker']
        env={e['name']:e['value'] for e in crawler['environment']}
        self.assertEqual(env['DOCUMENT_EXTRACTION_URL'],'http://127.0.0.1:8000')
        self.assertEqual(env['CHAT_GENERATION_URL'],env['DOCUMENT_EXTRACTION_URL'])
        self.assertEqual(crawler['dependsOn'],[{'containerName':'extraction-worker','condition':'HEALTHY'}])
        self.assertGreaterEqual(int(task['memory']),sum(c['memory'] for c in containers.values()))
        self.assertFalse(worker['essential'])
        self.assertTrue(worker['restartPolicy']['enabled'])
        self.assertFalse(any('MONGO' in s['name'] for s in worker['secrets']))
        self.assertIn('__EXTRACTION_IMAGE__',(root/'.github/workflows/crawler-image.yml').read_text())
