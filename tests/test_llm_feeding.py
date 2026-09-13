"""Isolated API/adapter checks; no real provider calls or device execution."""
import contextlib
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch
import requests

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT/'backend'), str(ROOT/'scripts')]
TEMP = tempfile.TemporaryDirectory(prefix='feeding-llm-')
os.environ['DB_URL'] = 'sqlite:///' + (Path(TEMP.name)/'test.db').as_posix()
from app import app
from database import Base, engine, SessionLocal
from models import Suggestion, FeedingTask
from services import llm_feeding
from services.common import now
import init_db

KEY='test-only-secret-not-a-real-key'

class LlmFeedingTests(unittest.TestCase):
    def setUp(self):
        Base.metadata.drop_all(engine)
        Base.metadata.create_all(engine)
        with contextlib.redirect_stdout(io.StringIO()): init_db.seed()
        self.client=app.test_client()
        self.env=patch.dict(os.environ, {'SILICONFLOW_API_KEY': KEY, 'SILICONFLOW_MODEL':'test-model'})
        self.env.start(); self.addCleanup(self.env.stop)
        self.client.post('/api/measurements',headers={'X-User':'owner'},json={
            'pond_id':2,'metric':'temperature','value':26,'unit':'℃','source':'sim',
            'collected_at':now().strftime('%Y-%m-%d %H:%M:%S')})

    def response(self, content=None, status=200, finish='stop'):
        r=MagicMock();r.__enter__.return_value=r;r.status_code=status
        r.json.return_value={'choices':[{'finish_reason':finish,'message':{'content':json.dumps(content or {
            'decision':'feed','amount_kg':30,'unit':'kg','reason':'摄食状态未知，适当减少并现场核实。'})}}]}
        return r

    def call(self, mode='llm', user='owner'):
        return self.client.post('/api/ponds/2/suggestions',headers={'X-User':user},json={'mode':mode})

    def empty(self):
        with SessionLocal() as db:
            self.assertEqual(db.query(Suggestion).count(),0)
            self.assertEqual(db.query(FeedingTask).count(),0)

    def test_success_is_persisted_and_still_requires_confirmation(self):
        with patch.object(llm_feeding.requests,'post',return_value=self.response()) as post:
            r=self.call().json
        self.assertTrue(r['ok'],r);s=r['data']
        self.assertEqual((s['source'],s['amount'],s['model']),('llm',30,'test-model'))
        self.assertEqual(s['inputs']['llm']['provider'],'SiliconFlow')
        self.assertNotIn(KEY,json.dumps(r))
        self.assertEqual(post.call_args.args[0],llm_feeding.ENDPOINT)
        self.assertNotIn(KEY,json.dumps(post.call_args.kwargs['json']))
        with SessionLocal() as db:self.assertEqual(db.query(FeedingTask).count(),0)
        t=self.client.post('/api/ponds/2/tasks',headers={'X-User':'owner'},json={
            'request_no':'llm-test-confirm','suggestion_code':s['code'],'confirm_amount':30}).json
        self.assertTrue(t['ok'],t);self.assertEqual(t['data']['suggested_amount'],30)
        self.assertIsNone(t['data']['actual_amount'])

    def test_missing_key_does_not_call_or_fallback(self):
        with patch.dict(os.environ,{'SILICONFLOW_API_KEY':''}),patch.object(llm_feeding.requests,'post') as post:
            self.assertFalse(self.call().json['ok']);post.assert_not_called()
        self.empty()

    def test_timeout_and_provider_errors_do_not_leak(self):
        for error in [requests.Timeout(KEY),requests.ConnectionError(KEY)]:
            with self.subTest(error=type(error).__name__),patch.object(llm_feeding.requests,'post',side_effect=error) as post:
                r=self.call().json;self.assertFalse(r['ok']);self.assertNotIn(KEY,json.dumps(r));self.assertEqual(post.call_count,1)
                self.empty()
        for status in [302,400,401,429,500]:
            with self.subTest(status=status),patch.object(llm_feeding.requests,'post',return_value=self.response(status=status)):
                self.assertFalse(self.call().json['ok']);self.empty()

    def test_reject_invalid_output_and_never_save_rule_fallback(self):
        cases=[{'decision':'feed','amount_kg':x,'unit':'kg','reason':'测试'} for x in [-1,0,99999,float('nan'),float('inf'),True,'30']]
        cases += [{'decision':'feed','amount_kg':30,'unit':'tonne','reason':'测试'},
                  {'decision':'feed','amount_kg':30,'unit':'kg','reason':KEY},
                  {'decision':'unknown','amount_kg':30,'unit':'kg','reason':'测试'}]
        for case in cases:
            with self.subTest(case=case),patch.object(llm_feeding.requests,'post',return_value=self.response(case)):
                r=self.call().json;self.assertFalse(r['ok']);self.assertNotIn(KEY,json.dumps(r));self.empty()

    def test_hold_and_truncated_output_are_not_executable(self):
        for response in [self.response({'decision':'hold','amount_kg':None,'unit':'kg','reason':'先确认溶氧和摄食情况'}),self.response(finish='length')]:
            with patch.object(llm_feeding.requests,'post',return_value=response):
                self.assertFalse(self.call().json['ok']);self.empty()

    def test_rule_mode_and_no_permission_make_no_provider_call(self):
        with patch.object(llm_feeding.requests,'post') as post:
            self.assertEqual(self.call(user='unknown-user').status_code,401)
            r=self.call(mode='rule').json;self.assertTrue(r['ok']);self.assertEqual(r['data']['source'],'rule')
            post.assert_not_called()

    def test_single_inflight_request(self):
        llm_feeding._slot.acquire()
        try:
            with patch.object(llm_feeding.requests,'post') as post:
                self.assertFalse(self.call().json['ok']);post.assert_not_called();self.empty()
        finally:llm_feeding._slot.release()

    def test_invalid_context_does_not_call_provider_and_releases_slot(self):
        with patch.object(llm_feeding.requests, 'post') as post:
            with self.assertRaises(llm_feeding.AdviceError):
                llm_feeding.recommend({'temperature': float('nan')}, 0.1, 30)
            post.assert_not_called()
        self.assertTrue(llm_feeding._slot.acquire(blocking=False))
        llm_feeding._slot.release()

    def test_stale_temperature_does_not_call_provider(self):
        from models import Measurement
        from datetime import timedelta
        with SessionLocal() as db:
            for m in db.query(Measurement).all():m.collected_at=now()-timedelta(days=3)
            db.commit()
        with patch.object(llm_feeding.requests,'post') as post:
            self.assertFalse(self.call().json['ok']);post.assert_not_called();self.empty()

def tearDownModule():
    engine.dispose()
    TEMP.cleanup()


if __name__=='__main__':unittest.main()
