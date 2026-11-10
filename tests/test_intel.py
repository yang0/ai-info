import asyncio
import json
import os
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, patch
import httpx
from fastapi.testclient import TestClient
from intel.models import Post, Settings, utcnow
from intel.store import Store
from intel.sources import (Batch, SourceError, demo_posts, parse_reach, safe_x_url,
                           parse_opencli, opencli_search, reach_search, run_json, executable_argv)
from intel.analysis import trend_topics, assess, llm_role, make_brief
from intel.runner import Runner
from intel.app import create_app

NOW = datetime(2026, 11, 10, 7, 40, tzinfo=timezone.utc)


def post(i, minute=1, tag='TrendTest', author=None, text=None):
    return Post(id=str(i), text=text or f'#{tag} unique evidence {i}',
                author=author or f'author{i}', created_at=NOW-timedelta(minutes=minute),
                url=f'https://x.com/author{i}/status/{i}')


class TrendTests(unittest.TestCase):
    def test_acceleration_requires_baseline_and_authors(self):
        posts=[]
        for bucket, count in [(0,12),(1,6),(2,4)]:
            posts += [post(bucket*100+i+1, bucket*15+1) for i in range(count)]
        topic=trend_topics(posts,NOW)[0]
        self.assertEqual((topic['current'],topic['previous'],topic['acceleration']),(12,6,4))
        self.assertEqual(topic['state'],'加速崛起')
        self.assertEqual(topic['growth'],100)
        self.assertEqual(topic['bins'][-3:],[4,6,12])
        one_author=[p.model_copy(update={'author':'one'}) for p in posts]
        self.assertNotEqual(trend_topics(one_author,NOW)[0]['state'],'加速崛起')

    def test_zero_baseline_is_cold_not_infinite_growth(self):
        topic=trend_topics([post(i) for i in range(1,10)],NOW)[0]
        self.assertTrue(topic['cold_start'])
        self.assertEqual(topic['state'],'冷启动')
        self.assertTrue(topic['score']<=100)

    def test_duplicate_id_and_copy_paste_do_not_amplify(self):
        posts=[post(1),post(1),post(2,text=post(1).text),post(3)]
        self.assertEqual(trend_topics(posts,NOW)[0]['total'],2)

    def test_window_boundaries_and_future_records(self):
        topic=trend_topics([post(1,0),post(2,15),post(3,120),post(4,-1)],NOW)[0]
        self.assertEqual(topic['current'],1)
        self.assertEqual(topic['previous'],1)
        self.assertEqual(topic['total'],2)

    def test_demo_is_deterministic_and_labelled(self):
        a,b=demo_posts(NOW),demo_posts(NOW)
        self.assertEqual(a,b)
        self.assertTrue(all(not p.url and p.id.startswith('demo-') for p in a))
        topics=trend_topics(a,NOW)
        self.assertEqual(len(topics),6)
        self.assertTrue(any(t['state']=='加速崛起' for t in topics))

    def test_settings_scope_separates_modes_and_queries(self):
        a=Settings(mode='demo')
        self.assertNotEqual(a.dataset_key(),a.model_copy(update={'mode':'agent_reach'}).dataset_key())
        self.assertNotEqual(a.dataset_key(),a.model_copy(update={'queries':['other']}).dataset_key())
        self.assertEqual(a.dataset_key(),a.model_copy(update={'auto_scan':True}).dataset_key())


class SourceTests(unittest.IsolatedAsyncioTestCase):
    def test_opencli_real_shape_rfc_date_and_i_status_url(self):
        row={'url':'https://x.com/i/status/123','author':'actual_author','text':'#AI original',
             'created_at':utcnow().strftime('%a %b %d %H:%M:%S +0000 %Y')}
        batch=parse_opencli({'ok':True,'data':[row]},'AI')
        self.assertEqual(batch.posts[0].author,'actual_author')
        self.assertEqual(batch.posts[0].url,'https://x.com/actual_author/status/123')
        self.assertEqual(batch.posts[0].created_at.tzinfo,timezone.utc)

    async def test_latest_adapter_uses_readonly_argv_without_shell(self):
        with patch('intel.sources.executable_argv',return_value=['opencli']), patch(
            'intel.sources.run_json',AsyncMock(return_value={'ok':True,'data':[]})) as execute:
            batch=await opencli_search('AI OR LLM',25)
        argv=execute.call_args.args[0]
        self.assertEqual(argv[1:3],['twitter','search'])
        self.assertEqual(argv[argv.index('--product')+1],'live')
        self.assertEqual(argv[argv.index('--exclude')+1],'retweets')
        self.assertIn('since:',argv[3])
        self.assertEqual(batch.coverage,'sampled')

    async def test_reach_marks_ranked_sample(self):
        with patch('intel.sources.executable_argv',return_value=['agent-reach']), patch(
            'intel.sources.run_json',AsyncMock(return_value={'ok':True,'schema_version':1,'items':[]})):
            batch=await reach_search('AI',10)
        self.assertEqual(batch.coverage,'ranked_sampled')

    def test_opencli_rate_limit_and_login_are_explicit(self):
        with self.assertRaises(SourceError) as limit:
            parse_opencli({'ok':False,'error':{'code':'RATE_LIMITED','message':'secret'}},'AI')
        self.assertEqual(limit.exception.retry_after,3600)
        self.assertNotIn('secret',str(limit.exception))
        with self.assertRaises(SourceError) as login:
            parse_opencli({'ok':False,'error':{'code':'AUTH_REQUIRED'}},'AI')
        self.assertTrue(login.exception.pause)

    def test_invalid_time_never_becomes_current_time(self):
        row={'url':'https://x.com/i/status/123','author':'actual_author','text':'hello','created_at':'2 hours ago'}
        with self.assertRaises(SourceError):parse_opencli({'ok':True,'data':[row]},'AI')

    def test_windows_npm_shim_uses_node_entry(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)/'node_modules'/'@jackwener'/'opencli'
            root.mkdir(parents=True);(root/'bin.js').write_text('// test')
            (root/'package.json').write_text('{"bin":{"opencli":"bin.js"}}')
            def which(name):return str(Path(tmp)/'opencli.cmd') if name=='opencli' else 'node'
            with patch('intel.sources.shutil.which',side_effect=which):
                argv=executable_argv('opencli')
            self.assertEqual(argv,['node',str((root/'bin.js').resolve())])

    def test_reach_v1_missing_date_or_untrusted_url_rejected(self):
        good={'url':'https://x.com/a/status/123','text':'#AI hello', 'published_at':utcnow().isoformat()}
        bad={**good,'url':'https://x.com.evil.test/a/status/123'}
        missing={**good,'published_at':None}
        batch=parse_reach({'ok':True,'schema_version':1,'items':[good,bad,missing]},'AI')
        self.assertEqual((len(batch.posts),batch.rejected),(1,2))
        self.assertEqual(batch.coverage,'sampled')
        self.assertFalse(safe_x_url('javascript:alert(1)'))

    def test_reach_error_is_not_empty_success(self):
        with self.assertRaises(SourceError): parse_reach({'ok':False,'error':{'code':'NEED_LOGIN'}},'AI')


class RunnerTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.store=Store(Path(self.tmp.name));self.runner=Runner(self.store)

    def tearDown(self): self.tmp.cleanup()

    def test_database_connection_is_closed_after_transaction(self):
        with self.store.connect() as db:
            db.execute('SELECT 1')
        with self.assertRaises(sqlite3.ProgrammingError):
            db.execute('SELECT 1')

    async def test_demo_full_pipeline_persists_events_and_brief(self):
        self.store.save_settings(Settings(mode='demo'))
        self.runner.start();await self.runner.task
        self.assertEqual(self.store.runs()[0]['state'],'done')
        snap=self.store.snapshot(Settings(mode='demo').dataset_key())
        self.assertEqual(len(snap['topics']),6)
        self.assertEqual({ev['agent'] for ev in self.store.events()},
                         {'scout','cluster','trend','verify','analyst','editor'})
        self.assertIn('演示样本',make_brief(snap))

    async def test_failed_source_keeps_previous_snapshot(self):
        settings=Settings(mode='agent_reach');self.store.save_settings(settings)
        self.store.publish(settings.dataset_key(),{'run_id':'old','topics':[]})
        with patch('intel.runner.reach_search',AsyncMock(side_effect=SourceError('no credentials'))):
            self.runner.start();await self.runner.task
        self.assertEqual(self.store.runs()[0]['state'],'failed')
        self.assertEqual(self.store.snapshot(settings.dataset_key())['run_id'],'old')

    async def test_cancel_before_task_enters_does_not_stick(self):
        self.runner.start();await self.runner.cancel()
        self.assertIsNone(self.runner.active)
        self.assertEqual(self.store.runs()[0]['state'],'cancelled')

    async def test_cancel_in_flight_preserves_snapshot_and_excludes_overlap(self):
        settings=Settings(mode='agent_reach');self.store.save_settings(settings)
        entered=asyncio.Event()
        async def waiting(*args):
            entered.set();await asyncio.Event().wait()
        with patch('intel.runner.reach_search',waiting):
            self.runner.start();await entered.wait()
            with self.assertRaises(ValueError): self.runner.start()
            await self.runner.cancel()
        self.assertIsNone(self.runner.active)
        self.assertIsNone(self.store.snapshot(settings.dataset_key()))
        self.assertEqual(self.store.runs()[0]['state'],'cancelled')

    async def test_limit_cooldown_blocks_next_scan(self):
        self.store.save_settings(Settings(mode='agent_reach'))
        with patch('intel.runner.reach_search',AsyncMock(side_effect=SourceError('limited',retry_after=300))):
            self.runner.start();await self.runner.task
        with self.assertRaises(ValueError): self.runner.start()

    async def test_restart_marks_incomplete_runs_interrupted(self):
        self.store.run({'id':'incomplete','started':NOW.isoformat(),'state':'running'})
        reopened=Store(Path(self.tmp.name))
        self.assertEqual(reopened.runs()[0]['state'],'interrupted')


class ApiTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.app=create_app(self.tmp.name)
        self.client=TestClient(self.app)
        self.client.__enter__()

    def tearDown(self):
        self.client.__exit__(None,None,None);self.tmp.cleanup()

    def login(self):
        return self.client.get('/api/session').json()['token']

    def test_auth_origin_settings_and_isolated_empty_state(self):
        self.assertEqual(self.client.get('/api/state').status_code,401)
        token=self.login()
        self.assertEqual(self.client.get('/api/state').status_code,200)
        self.assertEqual(self.client.post('/api/runs',headers={'Origin':'https://evil.test'}).status_code,403)
        self.assertEqual(self.client.put('/api/settings',json={'queries':[]}).status_code,422)
        self.assertEqual(self.client.put('/api/settings',json={'mode':'x_api'}).status_code,422)
        self.assertEqual(self.client.put('/api/settings',json={'mode':'agent_reach','queries':['AI']}).status_code,200)
        self.assertIsNone(self.client.get('/api/state').json()['snapshot'])
        self.assertEqual(self.client.get('/api/brief').status_code,404)
        self.assertNotIn('api_key',self.client.get('/api/state').json()['model'])

    def test_static_assets_and_csp(self):
        response=self.client.get('/')
        self.assertIn('情报作战台',response.text)
        self.assertIn("frame-ancestors 'none'",response.headers['content-security-policy'])
        self.assertEqual(self.client.get('/static/app.js').status_code,200)


if __name__=='__main__': unittest.main()
