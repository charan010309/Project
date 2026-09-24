"""Local-only mobile preview with synthetic auth, temporary DB, and mocked AI.

Run directly with the project Python. Never imported by the production app.
"""
if __name__ == '__main__':
    import base64
    import json
    from types import SimpleNamespace
    from unittest.mock import patch
    import uvicorn
    from fastapi.responses import RedirectResponse
    from itsdangerous import TimestampSigner
    import test_functionality as fixture

    case = fixture.FunctionalityTests()
    case.setUp()
    # Non-production history fixtures; matching IDs exercise chat/live isolation.
    with fixture.database.SessionLocal() as db:
        chat = fixture.app.ChatSession(user_id=1, title='Mobile history test', pinned=True)
        live = fixture.app.LiveSession(user_id=1, title='Voice history test')
        db.add_all([chat, live])
        db.flush()
        db.add_all([
            fixture.app.ChatMessage(user_id=1, session_id=chat.id, role='user', content='Test saved chat'),
            fixture.app.ChatMessage(user_id=1, session_id=chat.id, role='assistant', content='Saved reply <script> is displayed as text.'),
            fixture.app.LiveMessage(user_id=1, session_id=live.id, role='user', content='Test saved voice'),
        ])
        db.commit()

    @fixture.app.app.get('/test-mobile-login')
    def test_login():
        response = RedirectResponse('/m/home')
        cookie = TimestampSigner(fixture.CONFIG['SESSION_SECRET']).sign(
            base64.b64encode(json.dumps({'user_id': 1}).encode())).decode()
        response.set_cookie('session', cookie, httponly=True, samesite='lax')
        return response

    def generate(**kwargs):
        if 'preview-error' in str(kwargs.get('contents', '')):
            raise RuntimeError('Synthetic upstream failure')
        return SimpleNamespace(text='This is a test response. Your conversation is saved in the temporary test database.')

    try:
        with patch.object(fixture.app.client.models, 'generate_content', side_effect=generate):
            uvicorn.run(fixture.app.app, host='127.0.0.1', port=8011, log_level='warning')
    finally:
        case.tearDown()
