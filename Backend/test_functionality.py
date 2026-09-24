"""Regression tests using synthetic settings and a temporary database, never .env.

Run: venv/Scripts/python.exe Backend/test_functionality.py
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
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.modules["Backend.database"] = database
CONFIG = {"GEMINI_API_KEY": "test-only", "SESSION_SECRET": "isolated-test-secret", "MAIL_USERNAME": "test@example.com", "MAIL_PASSWORD": "test-only", "MAIL_FROM": "test@example.com"}
env_patch = patch.dict(os.environ, CONFIG, clear=True)
env_patch.start()
spec = importlib.util.spec_from_file_location("Backend.app", Path(__file__).with_name("app.py"))
app = importlib.util.module_from_spec(spec)
sys.modules["Backend.app"] = app
with patch.object(dotenv, "load_dotenv", return_value=False), patch.object(genai, "Client"):
    spec.loader.exec_module(app)
app.initialize_database()

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
        with patch.object(app.tempfile, 'gettempdir', return_value=TEMP.name), patch.object(app.edge_tts, 'Communicate', FakeSpeech):
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

    def test_vercel_entrypoint_and_page_routes(self):
        from api.index import app as entrypoint
        self.assertIs(entrypoint, app.app)
        for prefix in ['', '/m']:
            for page in ['/home', '/settings', '/history', '/overview', '/qr-login', '/qr-confirm', '/profile-settings', '/api-pro']:
                response = self.client.get(prefix + page)
                self.assertEqual(response.status_code, 200, prefix + page)
        for asset in ['theme.css', 'theme.js', 'logo.jpg']:
            self.assertEqual(self.client.get('/static/' + asset).status_code, 200, asset)

    def test_mobile_routing_preserves_queries_and_mobile_templates(self):
        for user_agent in ['Mozilla/5.0 (iPhone; CPU iPhone OS 18_0)', 'Mozilla/5.0 (Linux; Android 15) Mobile']:
            headers = {'user-agent': user_agent}
            for path in app.MOBILE_PAGE_PATHS:
                response = self.client.get(path + '?test=keep', headers=headers, follow_redirects=False)
                expected = '/m/' if path == '/' else '/m' + path
                self.assertEqual(response.status_code, 307, path)
                self.assertEqual(response.headers['location'], expected + '?test=keep')
            response = self.client.get('/m/home', headers=headers)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.content, (Path(app.MOBILE_DIR) / 'home.html').read_bytes())
            self.assertNotIn('id="mobileSidebarToggle"', response.text)
            self.assertEqual(self.client.get('/api/settings', headers=headers, follow_redirects=False).status_code, 200)
        self.assertEqual(self.client.get('/home', follow_redirects=False).status_code, 200)

    def test_each_mobile_route_serves_its_own_template(self):
        pages = {'/m/': 'intro.html', '/m/home': 'home.html', '/m/login': 'login.html',
                 '/m/signup': 'signup.html', '/m/settings': 'settings.html',
                 '/m/history': 'history.html', '/m/speakwithai': 'speakWithAI.html',
                 '/m/profile-settings': 'profile-settings.html', '/m/api-pro': 'apipro.html',
                 '/m/forgot-password': 'forgot-password.html', '/m/reset-password': 'reset-password.html'}
        for route, filename in pages.items():
            response = self.client.get(route)
            self.assertEqual(response.status_code, 200, route)
            self.assertEqual(response.content, (Path(app.MOBILE_DIR) / filename).read_bytes(), route)
        self.assertEqual(self.client.get('/home').content, (Path(app.PC_DIR) / 'home.html').read_bytes())
        self.assertEqual(self.client.get('/m/overview').content, (Path(app.PC_DIR) / 'overview.html').read_bytes())

    def test_mobile_account_preferences_persist(self):
        response = self.client.patch('/api/user/profile', json={'name': 'Mobile User', 'bio': 'A real profile'})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(self.client.get('/api/user/profile').json()['name'], 'Mobile User')
        response = self.client.put('/api/settings', json={'temperature': 0.4, 'context_limit': 64, 'output_language': 'te-IN'})
        self.assertEqual(response.status_code, 200, response.text)
        settings = self.client.get('/api/settings').json()
        self.assertEqual(settings['temperature'], 0.4)
        self.assertEqual(settings['output_language'], 'te-IN')

    def test_mobile_conversation_workflow(self):
        chat_id = self.client.post('/api/chat/new', json={'title': 'New Chat'}).json()['id']
        self.assertEqual(self.client.get(f'/api/chat/{chat_id}').status_code, 200)
        with patch.object(app.client.models, 'generate_content', return_value=types.SimpleNamespace(text='Mobile answer')):
            response = self.client.post('/ask', json={'text': 'Plan my day', 'model': 'flash'})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()['title'], 'Plan my day')
        saved = self.client.get(f'/api/chat/{chat_id}').json()
        self.assertEqual([m['content'] for m in saved['messages']], ['Plan my day', 'Mobile answer'])
        self.assertEqual(self.client.post(f'/api/chat/{chat_id}/pin').status_code, 200)
        self.assertTrue(self.client.get('/api/chat/list').json()[0]['pinned'])
        self.assertEqual(self.client.delete(f'/api/chat/{chat_id}').status_code, 200)
        self.assertEqual(self.client.get(f'/api/chat/{chat_id}').status_code, 404)

    def test_mobile_assets_and_accessible_shells(self):
        for asset in ['mobile.css', 'mobile.js', 'mobile-home.css', 'mobile-home.js', 'mobile-voice.css', 'mobile-voice.js', 'mobile-history.js', 'mobile-history.css', 'mobile-profile.js', 'mobile-profile.css']:
            self.assertEqual(self.client.get('/static/' + asset).status_code, 200)
        for route in ['home', 'history', 'settings', 'profile-settings', 'api-pro', 'speakwithai']:
            html = self.client.get('/m/' + route).text
            script = {'home': 'mobile-home.js', 'speakwithai': 'mobile-voice.js', 'history': 'mobile-history.js', 'profile-settings': 'mobile-profile.js'}.get(route, 'mobile.js')
            self.assertIn('/static/' + script, html)
            marker = {'home': 'id="appSidebar"', 'speakwithai': 'id="sidebarDrawer"'}.get(route, 'aria-label="Main navigation"')
            self.assertIn(marker, html)
            self.assertNotIn('user-scalable=no', html)
            self.assertNotIn('href="#"', html)

    def test_supplied_mobile_profile_controls(self):
        html = self.client.get('/m/profile-settings').text
        for element in ['profile-name-input', 'profile-role-input', 'bio-input', 'profile-timezone', 'profile-locale', 'device-list', 'delete-confirmation']:
            self.assertIn(f'id="{element}"', html)
        for fake in ['Dev Operator', 'operator@maximus.ai', '103.22.45.19', 'LATENCY: 12ms', 'SYS-ID #9402', 'Hardware Verified']:
            self.assertNotIn(fake, html)
        script = self.client.get('/static/mobile-profile.js').text
        for endpoint in ['/api/user/profile', '/api/user/email/request', '/api/user/email/verify', '/api/user/password', '/api/devices', '/api/user/export', '/api/account']:
            self.assertIn(endpoint, script)
        self.assertNotIn('innerHTML', script)

    def test_profile_preferences_avatar_and_validation(self):
        data = {'name': 'Profile Test', 'role': 'Engineer', 'bio': 'Mobile profile', 'timezone': 'UTC', 'locale': 'en-US', 'weekly_digest': True, 'avatar_data': 'data:image/png;base64,dGVzdA=='}
        self.assertEqual(self.client.patch('/api/user/profile', json=data).status_code, 200)
        profile = self.client.get('/api/user/profile').json()
        for key in ['name', 'role', 'bio', 'timezone', 'locale', 'weekly_digest']:
            self.assertEqual(profile[key], data[key])
        self.assertEqual(profile['icon_url'], data['avatar_data'])
        self.assertEqual(self.client.patch('/api/user/profile', json={'name': ' '}).status_code, 400)
        self.assertEqual(self.client.patch('/api/user/profile', json={'bio': 'x'*241}).status_code, 400)
        self.assertEqual(self.client.delete('/api/user/profile/avatar').status_code, 200)
        self.assertIsNone(self.client.get('/api/user/profile').json()['icon_url'])

    def test_email_change_delivery_and_verification(self):
        with patch.object(app, 'send_otp_email', side_effect=RuntimeError('Mail down')), patch.object(app, 'generate_otp', return_value='123456'):
            self.assertEqual(self.client.post('/api/user/email/request', json={'new_email': 'new@example.com'}).status_code, 503)
        self.assertEqual(self.client.post('/api/user/email/verify', json={'new_email': 'new@example.com', 'otp': '123456'}).status_code, 400)
        with patch.object(app, 'send_otp_email', return_value=None), patch.object(app, 'generate_otp', return_value='654321'):
            self.assertEqual(self.client.post('/api/user/email/request', json={'new_email': 'new@example.com'}).status_code, 200)
        self.assertEqual(self.client.post('/api/user/email/verify', json={'new_email': 'new@example.com', 'otp': '000000'}).status_code, 400)
        self.assertEqual(self.client.post('/api/user/email/verify', json={'new_email': 'new@example.com', 'otp': '654321'}).status_code, 200)
        self.assertEqual(self.client.get('/api/user/profile').json()['email'], 'new@example.com')
        self.assertEqual(self.client.post('/api/user/email/verify', json={'new_email': 'new@example.com', 'otp': '654321'}).status_code, 400)

    def test_profile_password_set_and_update(self):
        self.assertEqual(self.client.post('/api/user/password/set', json={'current_password': '', 'new_password': 'synthetic-password'}).status_code, 200)
        self.assertTrue(self.client.get('/api/user/profile').json()['has_password'])
        self.assertEqual(self.client.post('/api/user/password', json={'current_password': 'wrong', 'new_password': 'updated-test-password'}).status_code, 400)
        self.assertEqual(self.client.post('/api/user/password', json={'current_password': 'synthetic-password', 'new_password': 'updated-test-password'}).status_code, 200)

    def test_profile_export_and_account_deletion_isolation(self):
        self.assertEqual(self.client.get('/api/user/export').json()['user']['email'], 'test@example.com')
        self.assertEqual(self.client.delete('/api/account').status_code, 200)
        # The manually seeded domainless cookie is not removed by an HTTP
        # response for testserver.local; real browser cookies are domain-bound.
        with database.SessionLocal() as db:
            self.assertIsNone(db.get(app.User, 1))
        self.client.cookies.clear()
        self.assertEqual(self.client.get('/api/user/profile').status_code, 401)
        with database.SessionLocal() as db:
            self.assertIsNotNone(db.get(app.User, 2))

    def test_supplied_mobile_history_has_real_controls(self):
        html = self.client.get('/m/history').text
        for element in ['history-items-container', 'preview-messages', 'history-search', 'history-pinned', 'history-recent', 'confirm-delete', 'export-session']:
            self.assertIn(f'id="{element}"', html)
        for sample in ['sess-01', 'Pro Tier Active', '2.4k tokens', '0.4 MB', 'VAD Interruption']:
            self.assertNotIn(sample, html)
        script = self.client.get('/static/mobile-history.js').text
        for endpoint in ['/api/chat/list', '/api/live/list', '/api/user/profile']:
            self.assertIn(endpoint, script)
        self.assertNotIn('innerHTML', script)

    def test_history_chat_and_live_ids_do_not_collide(self):
        with database.SessionLocal() as db:
            db.add_all([app.ChatSession(id=1, user_id=1, title='Chat history'), app.LiveSession(id=1, user_id=1, title='Voice history')])
            db.flush()
            db.add_all([app.ChatMessage(user_id=1, session_id=1, role='user', content='Chat message'), app.LiveMessage(user_id=1, session_id=1, role='user', content='Voice message')])
            db.commit()
        self.assertEqual(self.client.get('/api/chat/list').json()[0]['message_count'], 1)
        self.assertEqual(self.client.get('/api/live/list').json()[0]['message_count'], 1)
        self.assertEqual(self.client.post('/api/live/1/pin').status_code, 200)
        self.assertFalse(self.client.get('/api/chat/list').json()[0]['pinned'])
        self.assertTrue(self.client.get('/api/live/list').json()[0]['pinned'])
        self.assertEqual(self.client.delete('/api/live/1').status_code, 200)
        self.assertEqual(self.client.get('/api/chat/1').json()['messages'][0]['content'], 'Chat message')
        self.assertEqual(self.client.get('/api/live/1').status_code, 404)

    def test_supplied_mobile_home_has_real_controls(self):
        html = self.client.get('/m/home').text
        for element in ['chatInput', 'messageList', 'activeModelSelect', 'networkToggle', 'pinnedList', 'recentList', 'cameraModal']:
            self.assertIn(f'id="{element}"', html)
        self.assertNotIn('operator@maximus.ai', html)
        self.assertNotIn('stitch-placeholder', html)
        self.assertNotIn('value="pro"', html)
        script = self.client.get('/static/mobile-home.js').text
        for endpoint in ['/ask-image', '/ask', '/api/chat/list', '/api/providers', '/api/user/profile', '/api/settings']:
            self.assertIn(endpoint, script)
        self.assertNotIn('Simulate model reply', script)

    def test_failed_chat_can_be_retried_without_duplicate_messages(self):
        chat_id = self.client.post('/api/chat/new', json={'title': 'New Chat'}).json()['id']
        with patch.object(app.client.models, 'generate_content', side_effect=RuntimeError('Temporary outage')):
            self.assertEqual(self.client.post('/ask', json={'text': 'Retry me'}).status_code, 502)
        self.assertEqual(self.client.get(f'/api/chat/{chat_id}').json()['messages'], [])
        with patch.object(app.client.models, 'generate_content', return_value=types.SimpleNamespace(text='Recovered')):
            self.assertEqual(self.client.post('/ask', json={'text': 'Retry me'}).status_code, 200)
        messages = self.client.get(f'/api/chat/{chat_id}').json()['messages']
        self.assertEqual([m['content'] for m in messages], ['Retry me', 'Recovered'])

    def test_failed_live_turn_does_not_leave_duplicate_transcript(self):
        session_id = self.client.post('/api/live/new', json={'title': 'New Live Session'}).json()['id']
        with patch.object(app.client.models, 'generate_content', side_effect=RuntimeError('Outage')):
            self.assertEqual(self.client.post('/api/live-ask', json={'text': 'Retry voice'}).status_code, 503)
        self.assertEqual(self.client.get(f'/api/live/{session_id}').json()['messages'], [])
        with patch.object(app.client.models, 'generate_content', return_value=types.SimpleNamespace(text='Recovered')):
            self.assertEqual(self.client.post('/api/live-ask', json={'text': 'Retry voice'}).status_code, 200)
        self.assertEqual([m['content'] for m in self.client.get(f'/api/live/{session_id}').json()['messages']], ['Retry voice', 'Recovered'])

    def test_supplied_voice_page_controls(self):
        html = self.client.get('/m/speakwithai').text
        for identifier in ['openLiveSettingsBtn', 'pinLiveConversationBtn', 'centralMicBtn', 'transcriptFeed']:
            self.assertIn(f'id="{identifier}"', html)
        self.assertNotIn('operator@maximus.ai', html)
        self.assertNotIn('Simulate Assistant Response', html)
        self.assertNotIn('value="ultra"', html)

    def test_oauth_state_error_is_not_internal_server_error(self):
        from unittest.mock import AsyncMock
        from authlib.integrations.base_client.errors import MismatchingStateError
        with patch.object(app.oauth.google, 'authorize_access_token', new=AsyncMock(side_effect=MismatchingStateError())):
            response = self.client.get('/auth/google/callback')
        self.assertEqual(response.status_code, 400)
        self.assertIn('start sign-in again', response.json()['detail'])

    def test_google_success_returns_mobile_home(self):
        from unittest.mock import AsyncMock
        info = {'sub': 'test-google', 'email': 'test@example.com', 'name': 'Test'}
        with patch.object(app.oauth.google, 'authorize_access_token', new=AsyncMock(return_value={'userinfo': info})):
            response = self.client.get('/auth/google/callback', headers={'user-agent': 'iPhone'}, follow_redirects=False)
        self.assertEqual(response.status_code, 303)
        self.assertEqual(response.headers['location'], '/m/home')

    def test_shared_otp_is_one_time_and_expires(self):
        app.store_otp('test', '123456', {'name': 'Test'})
        self.assertIsNone(app.verify_otp('test', '000000'))
        self.assertEqual(app.verify_otp('test', '123456'), {'name': 'Test'})
        self.assertIsNone(app.verify_otp('test', '123456'))
        app.store_challenge('otp', 'expired', {}, -1, verification='123456')
        self.assertIsNone(app.verify_otp('expired', '123456'))

    def test_shared_qr_transfer_and_polling(self):
        with patch.dict(os.environ, {'APP_BASE_URL': 'https://maximus.example'}):
            response = self.client.get('/auth/qr/generate')
        self.assertEqual(response.status_code, 200)
        token = response.headers['X-MAXIMUS-QR-Token']
        self.assertEqual(self.client.get('/auth/qr/pending').json()['status'], 'waiting')
        with TestClient(app.app) as phone:
            self.assertEqual(phone.get('/auth/qr/status', params={'token': token}).status_code, 404)
            claimed = phone.get('/auth/qr-confirm', params={'token': token, 'mobile': 'true'}, follow_redirects=False)
            self.assertEqual(claimed.status_code, 303, claimed.text)
            self.assertEqual(claimed.headers['location'], '/m/home')
            self.assertEqual(phone.get('/auth/qr-confirm', params={'token': token}).status_code, 400)
        self.assertEqual(self.client.get('/auth/qr/pending').json()['status'], 'completed')

    def test_vercel_database_requires_postgres(self):
        import runpy
        with patch.dict(os.environ, {'VERCEL': '1', 'DATABASE_URL': 'sqlite:///not-allowed.db'}):
            with self.assertRaisesRegex(RuntimeError, 'hosted PostgreSQL'):
                runpy.run_path(str(Path(__file__).with_name('database.py')))

    def test_public_urls_use_deployment_hostname(self):
        from starlette.requests import Request
        request = Request({'type': 'http', 'scheme': 'http', 'server': ('localhost', 8000), 'path': '/', 'headers': []})
        with patch.dict(os.environ, {'VERCEL': '1', 'VERCEL_URL': 'maximus.example', 'APP_BASE_URL': ''}):
            self.assertEqual(app._public_base_url(request), 'https://maximus.example')

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
