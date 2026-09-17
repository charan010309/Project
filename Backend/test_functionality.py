"""Regression tests using synthetic settings and a temporary database, never .env.

Run: Backend/venv/Scripts/python.exe Backend/test_functionality.py
"""
import base64
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import patch
import dotenv
from fastapi.testclient import TestClient
from google import genai
from itsdangerous import TimestampSigner
from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker

TEMP = tempfile.TemporaryDirectory(prefix="maximus-tests-")
engine = create_engine("sqlite:///" + (Path(TEMP.name) / "test.db").as_posix(), connect_args={"check_same_thread": False})
database = types.ModuleType("database")
database.engine = engine
database.Base = declarative_base()
database.SessionLocal = sessionmaker(bind=engine)
def get_db():
    with database.SessionLocal() as session:
        yield session
database.get_db = get_db
sys.modules["database"] = database
CONFIG = {"GEMINI_API_KEY": "test-only", "SESSION_SECRET": "isolated-test-secret", "MAIL_USERNAME": "test@example.com", "MAIL_PASSWORD": "test-only", "MAIL_FROM": "test@example.com"}
env_patch = patch.dict(os.environ, CONFIG, clear=True)
env_patch.start()
spec = importlib.util.spec_from_file_location("tested_app", Path(__file__).with_name("app.py"))
app = importlib.util.module_from_spec(spec)
with patch.object(dotenv, "load_dotenv", return_value=False), patch.object(genai, "Client"):
    spec.loader.exec_module(app)

class FunctionalityTests(unittest.TestCase):
    def setUp(self):
        with engine.begin() as connection:
            for table in reversed(database.Base.metadata.sorted_tables):
                connection.execute(table.delete())
        with database.SessionLocal() as db:
            db.add_all([app.User(id=1, name="Test", email="test@example.com", is_verified=True), app.User(id=2, name="Other", email="other@example.com", is_verified=True)])
            db.commit()
        self.client = TestClient(app.app)
        cookie = TimestampSigner(CONFIG['SESSION_SECRET']).sign(base64.b64encode(json.dumps({'user_id': 1}).encode())).decode()
        self.client.cookies.set('session', cookie)

    def tearDown(self):
        self.client.close()

    def image(self, **extra):
        return {'image': base64.b64encode(b'test-image').decode(), 'mime_type': 'image/png', 'text': 'Read this', **extra}

    def test_history_routes_and_user_isolation(self):
        with database.SessionLocal() as db:
            db.add_all([app.ChatSession(user_id=1, title='mine'), app.ChatSession(user_id=2, title='other'), app.LiveSession(user_id=1, title='mine'), app.LiveSession(user_id=2, title='other')]); db.commit()
        self.assertEqual(self.client.get('/api/chat/history').status_code, 200)
        self.assertEqual(self.client.delete('/api/chat/history').status_code, 200)
        self.assertEqual(self.client.delete('/api/live/history').status_code, 200)
        with database.SessionLocal() as db:
            self.assertEqual(db.query(app.ChatSession).count(), 1)
            self.assertEqual(db.query(app.LiveSession).count(), 1)
            self.assertEqual(db.query(app.ChatSession).first().user_id, 2)

    def test_authentication(self):
        with TestClient(app.app) as anonymous:
            for path, payload in [('/ask-image', self.image()), ('/api/tts', {'text': 'hello'}), ('/ask', {'text': 'hello'})]:
                self.assertEqual(anonymous.post(path, json=payload).status_code, 401, path)

    def test_generation_failures_return_errors(self):
        with patch.object(app.client.models, 'generate_content', side_effect=RuntimeError('private upstream error')):
            for path, payload in [('/ask-image', self.image()), ('/ask', {'text': 'hello'})]:
                response = self.client.post(path, json=payload)
                self.assertEqual(response.status_code, 502, response.text)
                self.assertNotIn('private upstream error', response.text)

    def test_image_validation_and_provider_support(self):
        with patch.object(app.client.models, 'generate_content') as generate:
            for extra in [{'image': 'invalid!'}, {'model': 'provider:1:vision'}, {'mime_type': 'text/plain'}]:
                self.assertEqual(self.client.post('/ask-image', json=self.image(**extra)).status_code, 400)
            generate.assert_not_called()

    def test_screen_history_and_vision_preferences(self):
        live_id = self.client.post('/api/live/new', json={'title': 'Screen'}).json()['id']
        with patch.object(app.client.models, 'generate_content', return_value=types.SimpleNamespace(text='Screen result')) as generate:
            response = self.client.post('/ask-image', json=self.image(live_session_id=live_id))
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()['live_session_id'], live_id)
        with database.SessionLocal() as db:
            self.assertEqual(db.query(app.LiveMessage).count(), 2)
            self.assertEqual(db.query(app.ChatMessage).count(), 0)
        prompt = generate.call_args.kwargs['contents'][0]
        self.assertIn('visible text', prompt)
        self.assertIn('relative positions', prompt)

    def test_screen_ownership(self):
        with database.SessionLocal() as db:
            live = app.LiveSession(user_id=2, title='Private'); db.add(live); db.commit(); live_id = live.id
        self.assertEqual(self.client.post('/ask-image', json=self.image(live_session_id=live_id)).status_code, 404)

    def test_provider_selection_and_metrics(self):
        with database.SessionLocal() as db:
            provider = app.Provider(user_id=1, label='Test', provider='custom', base_url='https://provider.example', is_valid=True, model_id='model-a', models_json='["model-a", "model-b"]')
            db.add(provider); db.commit(); provider_id = provider.id
        selection = f'provider:{provider_id}:model-a'
        response = self.client.put('/api/settings', json={'model': selection})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(self.client.get('/api/settings').json()['model'], selection)
        with patch.object(app, '_provider_request', return_value=(200, {'choices': [{'message': {'content': 'hello'}}]}, 10)):
            response = self.client.post('/ask', json={'text': 'hello', 'model': selection})
        self.assertEqual(response.status_code, 200, response.text)
        with patch.object(app, '_provider_request', return_value=(503, {'error': {'message': 'Unavailable'}}, 20)):
            response = self.client.post('/ask', json={'text': 'hello', 'model': selection})
        self.assertEqual(response.status_code, 502, response.text)
        stats = self.client.get('/api/providers/telemetry').json()
        self.assertEqual(stats['requests'], 2)
        self.assertEqual(stats['errors'], 1)
        self.assertEqual(stats['registered_models'], 2)
        self.assertIsNotNone(stats['p95_latency_ms'])

    def test_overview(self):
        response = self.client.get('/overview')
        self.assertEqual(response.status_code, 200)
        self.assertIn('Your activity', response.text)
        self.assertNotIn('99.98%', response.text)

    def test_speech_uses_selected_voice_and_cleans_temporary_audio(self):
        voices = []
        class FakeSpeech:
            def __init__(self, text, voice):
                voices.append(voice)
            async def save(self, path):
                Path(path).write_bytes(b'test-audio')
        with patch.object(app, 'BASE_DIR', TEMP.name), patch.object(app.edge_tts, 'Communicate', FakeSpeech):
            response = self.client.post('/api/tts', json={'text': 'hello', 'voice': 'en-IN-NeerjaNeural'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, b'test-audio')
        self.assertEqual(voices, ['en-IN-NeerjaNeural'])
        self.assertEqual(list(Path(TEMP.name).glob('*.mp3')), [])

    def test_telemetry_percentile_window_and_ownership(self):
        from datetime import datetime, timedelta
        with database.SessionLocal() as db:
            for latency in range(1, 21):
                db.add(app.GenerationMetric(user_id=1, provider_id=1, latency_ms=latency, success=True))
            db.add(app.GenerationMetric(user_id=2, provider_id=2, latency_ms=99999, success=False))
            db.add(app.GenerationMetric(user_id=1, provider_id=1, latency_ms=99999, success=False, created_at=datetime.utcnow()-timedelta(days=2)))
            db.commit()
        stats = self.client.get('/api/providers/telemetry').json()
        self.assertEqual(stats['requests'], 20)
        self.assertEqual(stats['errors'], 0)
        self.assertEqual(stats['p95_latency_ms'], 19)

    def test_normal_chat_and_live_generation(self):
        with patch.object(app.client.models, 'generate_content', return_value=types.SimpleNamespace(text='Test reply')):
            for endpoint in ['/ask', '/api/live-ask']:
                response = self.client.post(endpoint, json={'text': 'hello'})
                self.assertEqual(response.status_code, 200, response.text)
                self.assertEqual(response.json()['reply'], 'Test reply')
        with database.SessionLocal() as db:
            self.assertEqual(db.query(app.ChatMessage).count(), 2)
            self.assertEqual(db.query(app.LiveMessage).count(), 2)

if __name__ == '__main__':
    try:
        unittest.main(verbosity=2)
    finally:
        engine.dispose()
        env_patch.stop()
        TEMP.cleanup()
