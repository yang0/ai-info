import asyncio
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx
from fastapi.testclient import TestClient
from pydantic import ValidationError

from intel.app import create_app
from intel.analysis import assess, llm_role, trend_topics
from intel.llm import ModelConfig, ModelUpdate, ModelError, completion, parse_analysis, test_connection
from intel.models import Settings, utcnow
from intel.runner import Runner
from intel.sources import demo_posts
from intel.store import Store

KEY = 'test-only-not-a-real-api-key'
BASE = 'https://relay.example/custom/v1'


def config(**kwargs):
    return ModelConfig(base_url=BASE, model='relay-model', api_key=KEY, **kwargs)


def response(content):
    return {'choices': [{'message': {'content': content}}]}


class ConfigTests(unittest.TestCase):
    def test_endpoint_preserves_custom_prefix_and_full_endpoint(self):
        for base, expected in [
            ('https://api.deepseek.com/', 'https://api.deepseek.com/chat/completions'),
            (BASE, BASE+'/chat/completions'),
            (BASE+'/chat/completions', BASE+'/chat/completions'),
        ]:
            self.assertEqual(ModelConfig(base_url=base).endpoint, expected)
        self.assertNotIn(KEY, str(config()))
        self.assertNotIn(KEY, json.dumps(config().public()))

    def test_remote_compatible_api_is_supported_but_invalid_url_rejected(self):
        self.assertTrue(config().ready)
        self.assertTrue(ModelConfig(base_url='http://127.0.0.1:8000/v1', model='local').ready)
        for base in ['http://relay.example/v1', 'https://name:secret@relay.example/v1',
                     'https://relay.example/v1?key=secret', 'https://relay.example:bad/v1']:
            with self.assertRaises(ValidationError): ModelConfig(base_url=base)

    def test_prompt_json_parser_accepts_fences_but_not_prose_or_arrays(self):
        self.assertEqual(parse_analysis('```json\n{"text":"ok"}\n```'), {'text':'ok'})
        for value in ['not json', '[]']:
            with self.assertRaises(ModelError):parse_analysis(value)


class TransportTests(unittest.IsolatedAsyncioTestCase):
    async def test_chat_completion_contract_and_citation_validation(self):
        topic=trend_topics(demo_posts(),utcnow())[0]
        requests=[]
        def handle(request):
            requests.append(request)
            return httpx.Response(200,json=response(json.dumps({
                'text':'根据原帖观察，值得继续验证。', 'evidence_ids':[topic['evidence'][0]['id']]})))
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle),trust_env=False) as client:
            result=await llm_role(topic,'机会分析员',client,config())
        request=requests[0];body=json.loads(request.content)
        self.assertEqual(str(request.url),BASE+'/chat/completions')
        self.assertEqual(request.headers['authorization'],'Bearer '+KEY)
        self.assertEqual(body['model'],'relay-model')
        self.assertFalse(body['stream'])
        self.assertEqual(body['max_tokens'],800)
        self.assertNotIn('response_format',body)
        self.assertEqual(len(body['messages']),2)
        self.assertTrue(result['evidence_ids'])

    async def test_foreign_citations_are_rejected(self):
        topic=trend_topics(demo_posts(),utcnow())[0]
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _:httpx.Response(
            200,json=response('{"text":"claim","evidence_ids":["made-up-id"]}'))),trust_env=False) as client:
            with self.assertRaises(ModelError):await llm_role(topic,'质疑分析员',client,config())

    async def test_json_mode_unsupported_has_one_bounded_retry(self):
        bodies=[]
        def handle(request):
            body=json.loads(request.content);bodies.append(body)
            if 'response_format' in body:
                return httpx.Response(400,json={'error':{'message':'response_format unsupported'}})
            return httpx.Response(200,json=response('{"text":"ok"}'))
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle),trust_env=False) as client:
            result=await completion(config(json_mode=True),[{'role':'user','content':'JSON please'}],client)
        self.assertEqual(len(bodies),2)
        self.assertIn('response_format',bodies[0])
        self.assertNotIn('response_format',bodies[1])
        self.assertIn('ok',result)

    async def test_auth_limit_and_server_errors_are_redacted_and_not_retried(self):
        for status in [401,403,404,429,502]:
            requests=[]
            def handle(request):
                requests.append(request)
                return httpx.Response(status,text=KEY)
            async with httpx.AsyncClient(transport=httpx.MockTransport(handle),trust_env=False) as client:
                with self.assertRaises(ModelError) as exc:
                    await completion(config(json_mode=True),[],client)
            self.assertEqual(len(requests),1)
            self.assertIn(str(status),str(exc.exception))
            self.assertNotIn(KEY,str(exc.exception))

    async def test_bad_json_envelope_and_timeout_have_clear_errors(self):
        for reply in [httpx.Response(200,text=KEY),httpx.Response(200,json={'message':KEY}),
                      httpx.Response(200,json=response(''))]:
            async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _:reply),trust_env=False) as client:
                with self.assertRaises(ModelError) as exc:await completion(config(),[],client)
                self.assertNotIn(KEY,str(exc.exception))
        def timeout(request):raise httpx.ReadTimeout(KEY,request=request)
        async with httpx.AsyncClient(transport=httpx.MockTransport(timeout),trust_env=False) as client:
            with self.assertRaises(ModelError) as exc:await completion(config(),[],client)
            self.assertIn('超时',str(exc.exception))
            self.assertNotIn(KEY,str(exc.exception))

    async def test_failure_falls_back_and_cancels_sibling_role(self):
        topic=trend_topics(demo_posts(),utcnow())[0]
        entered=asyncio.Event();cancelled=asyncio.Event()
        async def role(topic,role,client,cfg):
            if role=='机会分析员':
                await entered.wait();raise ModelError('接口返回格式不兼容')
            entered.set()
            try:await asyncio.Event().wait()
            finally:cancelled.set()
        with patch('intel.analysis.llm_role',side_effect=role):result=await assess(topic,True,config())
        self.assertEqual(result['assessment']['engine'],'rules')
        self.assertIn('接口返回格式不兼容',result['assessment']['fallback'])
        self.assertTrue(cancelled.is_set())

    async def test_dual_roles_use_same_frozen_config(self):
        topic=trend_topics(demo_posts(),utcnow())[0]
        called=[]
        async def role(topic,role,client,cfg):
            called.append((role,cfg))
            return {'text':role,'evidence_ids':[topic['evidence'][0]['id']]}
        cfg=config()
        with patch('intel.analysis.llm_role',side_effect=role):result=await assess(topic,True,cfg)
        self.assertEqual(result['assessment']['engine'],'llm')
        self.assertEqual(result['assessment']['model'],'relay-model')
        self.assertTrue(all(c[1] is cfg for c in called))


class StorageTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.store=Store(Path(self.tmp.name))
    def tearDown(self):self.tmp.cleanup()

    def test_key_persistence_blank_preserves_and_explicit_clear_removes(self):
        saved=self.store.save_model(ModelUpdate(base_url=BASE,model='relay-model',api_key=KEY,enabled=True))
        self.assertNotIn(KEY,json.dumps(saved.public()))
        reopened=Store(Path(self.tmp.name))
        self.assertEqual(reopened.model_config().api_key.get_secret_value(),KEY)
        reopened.save_model(ModelUpdate(base_url=BASE,model='other',api_key=''))
        self.assertEqual(reopened.model_config().api_key.get_secret_value(),KEY)
        reopened.save_model(ModelUpdate(base_url=BASE,model='other',clear_api_key=True))
        self.assertFalse(reopened.model_config().public()['api_key_set'])

    def test_changing_provider_never_forwards_previous_key(self):
        self.store.save_model(ModelUpdate(base_url=BASE,model='m',api_key=KEY))
        with self.assertRaises(ValueError):
            self.store.resolve_model(ModelUpdate(base_url='https://other.example/v1',model='m'))
        changed=self.store.resolve_model(ModelUpdate(base_url='https://other.example/v1',model='m',clear_api_key=True))
        self.assertEqual(changed.api_key.get_secret_value(),'')

    def test_model_revision_isolated_only_when_analysis_enabled(self):
        before=self.store.settings().dataset_key()
        self.store.save_model(ModelUpdate(base_url=BASE,model='m',api_key=KEY))
        self.assertEqual(before,self.store.settings().dataset_key())
        self.store.save_model(ModelUpdate(base_url=BASE,model='m',enabled=True))
        enabled=self.store.settings().dataset_key()
        self.store.save_model(ModelUpdate(base_url=BASE,model='other',enabled=True))
        self.assertNotEqual(enabled,self.store.settings().dataset_key())


class ModelApiTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.app=create_app(self.tmp.name)
        self.client=TestClient(self.app);self.client.__enter__();self.client.get('/api/session')
    def tearDown(self):
        self.client.__exit__(None,None,None);self.tmp.cleanup()

    def body(self):return {'base_url':BASE,'model':'relay-model','api_key':KEY,'enabled':True}

    def test_api_never_returns_secret_and_can_save_enable_together(self):
        r=self.client.put('/api/model',json=self.body())
        self.assertEqual(r.status_code,200,r.text)
        self.assertTrue(r.json()['api_key_set'])
        for path in ['/api/model','/api/state']:
            response=self.client.get(path)
            self.assertNotIn(KEY,response.text)
            self.assertNotIn('"api_key":',response.text)
        self.assertTrue(self.client.get('/api/state').json()['settings']['llm_enabled'])
        revision=self.client.get('/api/state').json()['settings']['model_revision']
        self.client.put('/api/settings',json={'mode':'demo'})
        self.assertEqual(self.app.state.store.settings().model_revision,revision)

    def test_validation_does_not_reflect_invalid_secret(self):
        secret=KEY+'\ninvalid'
        r=self.client.put('/api/model',json={**self.body(),'api_key':secret})
        self.assertEqual(r.status_code,422)
        self.assertNotIn(KEY,r.text)
        self.assertNotIn('input',r.text)

    def test_unsaved_connection_test_is_not_persisted(self):
        async def check(cfg):
            self.assertEqual(cfg.api_key.get_secret_value(),KEY)
            return {'ok':True,'message':'连接成功','model':cfg.model}
        with patch('intel.app.test_connection',side_effect=check):
            r=self.client.post('/api/model/test',json=self.body())
        self.assertEqual(r.status_code,200,r.text)
        self.assertFalse(self.app.state.store.model_config().ready)
        self.assertFalse(self.app.state.store.settings().llm_enabled)

    def test_connection_error_is_visible_and_model_change_blocked_during_scan(self):
        with patch('intel.app.test_connection',side_effect=ModelError('模型接口鉴权失败 (401)，请检查 API Key')):
            r=self.client.post('/api/model/test',json=self.body())
        self.assertEqual(r.status_code,400)
        self.assertIn('401',r.text)
        self.app.state.runner.active={'id':'busy'}
        self.assertEqual(self.client.put('/api/model',json=self.body()).status_code,409)
        self.assertEqual(self.client.post('/api/model/test',json=self.body()).status_code,409)
        self.app.state.runner.active=None

    def test_environment_fallback_and_old_provider_vars_are_not_used(self):
        with patch.dict(os.environ,{'OPENAI_BASE_URL':BASE,'OPENAI_MODEL':'relay-model','OPENAI_API_KEY':KEY}):
            model=self.client.get('/api/model').json()
            self.assertTrue(model['ready']);self.assertTrue(model['api_key_set'])
            self.assertNotIn(KEY,json.dumps(model))


if __name__=='__main__':unittest.main()
