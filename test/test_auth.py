"""Integration tests exercise the WSGI application and real temporary SQLite files."""
import hashlib
import io
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from urllib.parse import urlencode
from wsgiref.util import setup_testing_defaults

from server import AuthApp, SESSION_SECONDS


class AuthTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.db_path = Path(self.directory.name) / 'auth.sqlite'
        self.app = AuthApp(self.db_path)
        self.credentials = dict(username='house_artist', email='Artist@example.test', password='Synthetic-password-123!')

    def request(self, path, data=None, cookie='', origin='http://localhost:8000', form=False, raw=None, method=None, content_type=None):
        environ = {}
        setup_testing_defaults(environ)
        body = raw if raw is not None else (urlencode(data).encode() if form and data is not None else json.dumps(data).encode())
        environ.update(PATH_INFO=path, REQUEST_METHOD=method or ('POST' if data is not None or raw is not None else 'GET'),
                       HTTP_ORIGIN=origin, HTTP_COOKIE=cookie, REMOTE_ADDR='127.0.0.1',
                       CONTENT_TYPE=content_type or ('application/x-www-form-urlencoded' if form else 'application/json'),
                       CONTENT_LENGTH=str(len(body)))
        environ['wsgi.input'] = io.BytesIO(body)
        result = {}
        def start(status, headers):
            result['status'] = int(status.split()[0])
            result['headers'] = dict(headers)
        result['body'] = b''.join(self.app(environ, start)).decode()
        return result

    def register(self, form=False):
        result = self.request('/api/auth/register', self.credentials, form=form)
        self.assertEqual(result['status'], 303 if form else 201)
        return result['headers']['Set-Cookie'].split(';')[0]

    def test_registration_persistence_rotation_logout(self):
        cookie = self.register()
        with sqlite3.connect(self.db_path) as db:
            salt, hashed = db.execute('SELECT salt, password_hash FROM users').fetchone()
            self.assertEqual(len(salt), 32)
            self.assertEqual(len(hashed), 128)
            self.assertNotEqual(hashed, self.credentials['password'])
            self.assertEqual(db.execute('SELECT token_hash FROM sessions').fetchone()[0], hashlib.sha256(cookie.split('=')[1].encode()).hexdigest())
        self.app = AuthApp(self.db_path)
        response = self.request('/api/auth/me', cookie=cookie)
        self.assertEqual(response['status'], 200)
        user = json.loads(response['body'])['user']
        self.assertEqual(set(user), {'id', 'username', 'email'})
        self.assertEqual(user['email'], 'artist@example.test')
        self.assertEqual(response['headers']['Cache-Control'], 'no-store')
        self.assertEqual(self.request('/api/auth/register', self.credentials)['status'], 409)
        bad = self.request('/api/auth/login', {**self.credentials, 'password': 'wrong'})
        missing = self.request('/api/auth/login', {**self.credentials, 'email': 'missing@example.test'})
        self.assertEqual(bad['status'], 401)
        self.assertEqual(bad['body'], missing['body'])
        login = self.request('/api/auth/login', self.credentials, cookie=cookie)
        self.assertEqual(login['status'], 200)
        fresh = login['headers']['Set-Cookie'].split(';')[0]
        self.assertNotEqual(fresh, cookie)
        self.assertEqual(self.request('/api/auth/me', cookie=cookie)['status'], 401)
        self.assertEqual(self.request('/api/auth/me', cookie=fresh)['status'], 200)
        logout = self.request('/api/auth/logout', {}, cookie=fresh)
        self.assertEqual(logout['status'], 200)
        self.assertIn('Max-Age=0', logout['headers']['Set-Cookie'])
        self.assertEqual(self.request('/api/auth/me', cookie=fresh)['status'], 401)

    def test_html_forms_work_without_javascript(self):
        for page in ('login', 'register'):
            result = self.request('/' + page + '.html')
            self.assertEqual(result['status'], 200)
            self.assertNotIn('<script', result['body'])
            self.assertNotIn('{{', result['body'])
            self.assertIn('action="/api/auth/' + page + '"', result['body'])
        cookie = self.register(form=True)
        account = self.request('/account.html', cookie=cookie)
        self.assertEqual(account['status'], 200)
        self.assertIn('house_artist', account['body'])
        self.assertIn('artist@example.test', account['body'])
        self.assertNotIn('<script', account['body'])
        self.assertEqual(self.request('/api/auth/logout', {}, cookie=cookie, form=True)['headers']['Location'], '/login.html')
        self.assertEqual(self.request('/account.html', cookie=cookie)['status'], 302)
        bad = self.request('/api/auth/login', {**self.credentials, 'password': 'wrong'}, form=True)
        self.assertEqual(bad['status'], 401)
        self.assertIn('Email or password is incorrect.', bad['body'])
        self.assertNotIn('value="wrong"', bad['body'])
        login = self.request('/api/auth/login', self.credentials, form=True)
        self.assertEqual(login['status'], 303)
        self.assertEqual(login['headers']['Location'], '/account.html')

    def test_server_validation_and_private_files(self):
        for change in ({'password': 'short'}, {'password': 'x' * 129}, {'password': []}, {'username': '<bad>'}, {'email': 'bad'}, {'username': None}):
            self.assertEqual(self.request('/api/auth/register', {**self.credentials, **change})['status'], 400)
        for raw in (b'null', b'[]', b'{invalid', b'"text"', b'\xff'):
            self.assertEqual(self.request('/api/auth/register', raw=raw)['status'], 400)
        self.assertEqual(self.request('/api/auth/register', raw=b'x' * 5000)['status'], 413)
        self.assertEqual(self.request('/api/auth/register', self.credentials, content_type='text/plain')['status'], 415)
        self.assertEqual(self.request('/api/auth/login', raw=b'email=a&email=b', form=True)['status'], 400)
        for path in ('/server.py', '/data/auth.sqlite', '/.git/config', '/test/test_auth.py', '/readme.txt', '/js/auth.js', '/%2e%2e/server.py'):
            self.assertEqual(self.request(path)['status'], 404)
        self.assertEqual(self.request('/account.html')['status'], 302)
        self.assertEqual(self.request('/api/auth/login')['status'], 405)
        self.assertEqual(self.request('/api/auth/me', cookie='art_house_session=forged')['status'], 401)

    def test_origin_checks_protect_every_mutation(self):
        cookie = self.register()
        for path in ('register', 'login', 'logout'):
            for origin in ('https://evil.test', '', 'null'):
                result = self.request('/api/auth/' + path, self.credentials, cookie=cookie, origin=origin, form=True)
                self.assertEqual(result['status'], 403)
        self.assertEqual(self.request('/api/auth/me', cookie=cookie)['status'], 200)

    def test_rate_limits_persist(self):
        for _ in range(10):
            self.assertEqual(self.request('/api/auth/login', self.credentials)['status'], 401)
        self.app = AuthApp(self.db_path)
        result = self.request('/api/auth/login', self.credentials)
        self.assertEqual(result['status'], 429)
        self.assertEqual(result['headers']['Retry-After'], '900')
        with sqlite3.connect(self.db_path) as db:
            db.execute('UPDATE attempts SET expires = 0')
        self.assertEqual(self.request('/api/auth/login', self.credentials)['status'], 401)
        for _ in range(49):
            self.assertEqual(self.request('/api/auth/register', {})['status'], 400)
        self.assertEqual(self.request('/api/auth/register', {})['status'], 429)

    def test_expiry_secure_cookies_and_production_configuration(self):
        with self.assertRaises(ValueError):
            AuthApp(self.db_path, production=True)
        with self.assertRaises(ValueError):
            AuthApp(self.db_path, origin='https://example.test/path')
        self.app = AuthApp(self.db_path, origin='https://example.test', production=True)
        result = self.request('/api/auth/register', self.credentials, origin='https://example.test')
        self.assertEqual(result['status'], 201)
        header = result['headers']['Set-Cookie']
        self.assertTrue(header.startswith('__Host-art_house_session='))
        for flag in ('HttpOnly', 'SameSite=Lax', '; Secure', 'Path=/', 'Max-Age=' + str(SESSION_SECONDS)):
            self.assertIn(flag, header)
        cookie = header.split(';')[0]
        with sqlite3.connect(self.db_path) as db:
            db.execute('UPDATE sessions SET expires = 0')
        self.assertEqual(self.request('/api/auth/me', cookie=cookie)['status'], 401)
        self.assertEqual(self.request('/account.html', cookie=cookie)['status'], 302)

    def test_account_output_is_escaped(self):
        cookie = self.register()
        with sqlite3.connect(self.db_path) as db:
            db.execute('UPDATE users SET username = ?', ('<img src=x onerror=alert(1)>',))
        page = self.request('/account.html', cookie=cookie)['body']
        self.assertNotIn('<img src=x', page)
        self.assertIn('&lt;img src=x', page)


if __name__ == '__main__':
    unittest.main()
