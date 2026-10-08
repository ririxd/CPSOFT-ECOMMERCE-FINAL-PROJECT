"""Art House authentication: Python's sqlite3, hashlib and WSGI standard libraries."""
import hashlib
import hmac
import html
import json
import logging
import os
from pathlib import Path
import re
import secrets
import sqlite3
import time
from contextlib import closing
from http import HTTPStatus
from http.cookies import SimpleCookie, CookieError
from urllib.parse import parse_qs, urlsplit
from wsgiref.simple_server import make_server

ROOT = Path(__file__).resolve().parent
SESSION_SECONDS = 7 * 24 * 60 * 60
PUBLIC_FILES = {
    '/': 'text/html', '/index.html': 'text/html', '/styles.css': 'text/css',
    '/css/vendor.css': 'text/css',
    **{'/js/' + name + '.js': 'text/javascript'
       for name in ('script', 'plugins', 'jquery-1.11.0.min')},
}
AUTH_PATHS = {'/api/auth/register', '/api/auth/login', '/api/auth/logout'}


def digest(value):
    return hashlib.sha256(value.encode('utf-8')).hexdigest()


def password_hash(password, salt):
    # Match the original scrypt parameters and ASCII hex salt to retain accounts.
    return hashlib.scrypt(password.encode('utf-8'), salt=salt.encode('ascii'),
                          n=16384, r=8, p=1, dklen=64).hex()


def now_ms():
    return int(time.time() * 1000)


class RequestError(Exception):
    def __init__(self, status, message):
        self.status = status
        self.message = message


class AuthApp:
    def __init__(self, db_path=None, origin='http://localhost:8000', production=False):
        parsed = urlsplit(origin)
        if (parsed.scheme not in ('http', 'https') or not parsed.netloc
                or parsed.username or parsed.password or parsed.path not in ('', '/')
                or parsed.query or parsed.fragment):
            raise ValueError('APP_ORIGIN must be an HTTP(S) origin without a path.')
        if production and parsed.scheme != 'https':
            raise ValueError('Production requires an HTTPS APP_ORIGIN.')
        self.origin = origin.rstrip('/')
        self.secure = parsed.scheme == 'https'
        self.cookie_name = '__Host-art_house_session' if self.secure else 'art_house_session'
        self.db_path = Path(db_path) if db_path else ROOT / 'data' / 'auth.sqlite'
        self.db_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.dummy_salt = secrets.token_hex(16)
        with closing(self.connect()) as db:
            db.executescript('''
                PRAGMA journal_mode = WAL;
                CREATE TABLE IF NOT EXISTS users (
                    id INTEGER PRIMARY KEY, username TEXT NOT NULL,
                    email TEXT NOT NULL UNIQUE, salt TEXT NOT NULL, password_hash TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS sessions (
                    token_hash TEXT PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id),
                    expires INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS attempts (
                    key TEXT PRIMARY KEY, count INTEGER NOT NULL, expires INTEGER NOT NULL
                );
            ''')

    def connect(self):
        db = sqlite3.connect(self.db_path, timeout=10)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA foreign_keys = ON')
        return db

    def token(self, environ):
        cookies = SimpleCookie()
        try:
            cookies.load(environ.get('HTTP_COOKIE', ''))
        except CookieError:
            return ''
        return cookies[self.cookie_name].value if self.cookie_name in cookies else ''

    def cookie(self, token, seconds):
        return (f'{self.cookie_name}={token}; Path=/; HttpOnly; SameSite=Lax; Max-Age={seconds}'
                + ('; Secure' if self.secure else ''))

    def user(self, db, environ):
        row = db.execute('''SELECT users.id, users.username, users.email FROM sessions
            JOIN users ON users.id = sessions.user_id WHERE token_hash = ? AND expires > ?''',
                         (digest(self.token(environ)), now_ms())).fetchone()
        return dict(row) if row else None

    def issue_session(self, db, environ, user_id):
        token = secrets.token_hex(32)
        db.execute('DELETE FROM sessions WHERE expires <= ? OR token_hash = ?',
                   (now_ms(), digest(self.token(environ))))
        db.execute('INSERT INTO sessions VALUES (?, ?, ?)',
                   (digest(token), user_id, now_ms() + SESSION_SECONDS * 1000))
        return ('Set-Cookie', self.cookie(token, SESSION_SECONDS))

    def limited(self, db, key, maximum):
        # Commit each counter before checking passwords so failures are counted.
        with db:
            db.execute('DELETE FROM attempts WHERE expires <= ?', (now_ms(),))
            db.execute('''INSERT INTO attempts VALUES (?, 1, ?)
                ON CONFLICT(key) DO UPDATE SET count = count + 1''',
                       (key, now_ms() + 15 * 60 * 1000))
            count = db.execute('SELECT count FROM attempts WHERE key = ?', (key,)).fetchone()[0]
        return count > maximum

    @staticmethod
    def render(filename, **values):
        source = (ROOT / filename).read_text(encoding='utf-8')
        # One-pass substitution escapes data and never interprets it as markup.
        return re.sub(r'\{\{(\w+)\}\}',
                      lambda match: html.escape(str(values.get(match[1], '')), quote=True), source)

    @staticmethod
    def read_body(environ, form):
        content_type = environ.get('CONTENT_TYPE', '').split(';')[0].strip()
        if content_type not in ('application/json', 'application/x-www-form-urlencoded'):
            raise RequestError(415, 'Unsupported form content type.')
        try:
            length = int(environ.get('CONTENT_LENGTH') or '0')
        except ValueError:
            raise RequestError(400, 'Invalid request length.')
        if length < 0 or length > 4096:
            raise RequestError(413, 'Request is too large.')
        try:
            raw = environ['wsgi.input'].read(length).decode('utf-8')
            if form:
                fields = parse_qs(raw, keep_blank_values=True, max_num_fields=10)
                if any(len(values) != 1 for values in fields.values()):
                    raise ValueError()
                return {key: values[0] for key, values in fields.items()}
            data = json.loads(raw)
            if not isinstance(data, dict):
                raise ValueError()
            return data
        except (ValueError, UnicodeError):
            raise RequestError(400, 'Invalid form data.')

    def dispatch(self, environ, db):
        path = environ.get('PATH_INFO', '/')
        method = environ['REQUEST_METHOD']
        if path == '/api/auth/me' and method in ('GET', 'HEAD'):
            user = self.user(db, environ)
            return (200, {'user': user}, []) if user else (401, {'error': 'Please log in.'}, [])
        if path in AUTH_PATHS:
            if method != 'POST':
                raise RequestError(405, 'Use POST.')
            if environ.get('HTTP_ORIGIN') != self.origin:
                raise RequestError(403, 'Request origin is not allowed.')
            form = environ.get('CONTENT_TYPE', '').split(';')[0].strip() == 'application/x-www-form-urlencoded'
            if path.endswith('/logout'):
                with db:
                    db.execute('DELETE FROM sessions WHERE token_hash = ?', (digest(self.token(environ)),))
                headers = [('Set-Cookie', self.cookie('', 0))]
                return (303, '', headers + [('Location', '/login.html')]) if form else (200, {'ok': True}, headers)
            if self.limited(db, 'ip:' + environ.get('REMOTE_ADDR', ''), 50):
                raise RequestError(429, 'Too many attempts. Please try again in 15 minutes.')
            data = self.read_body(environ, form)
            email = data.get('email', '')
            password = data.get('password', '')
            username = data.get('username', '')
            email = email.strip().lower() if isinstance(email, str) else ''
            username = username.strip() if isinstance(username, str) else ''
            registering = path.endswith('/register')
            if (not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+', email) or len(email) > 254
                    or not isinstance(password, str) or not 1 <= len(password) <= 128
                    or (registering and (len(password) < 12 or not re.fullmatch(r'[A-Za-z0-9_-]{3,30}', username)))):
                raise RequestError(400, 'Use a valid email, a 3–30 character username (letters, numbers, _ or -), and a 12–128 character password.'
                                   if registering else 'Enter a valid email and password.')
            if self.limited(db, 'email:' + digest(email), 10):
                raise RequestError(429, 'Too many attempts. Please try again in 15 minutes.')
            if registering:
                salt = secrets.token_hex(16)
                hashed = password_hash(password, salt)
                try:
                    with db:
                        user_id = db.execute('INSERT INTO users (username, email, salt, password_hash) VALUES (?, ?, ?, ?)',
                                             (username, email, salt, hashed)).lastrowid
                        headers = [self.issue_session(db, environ, user_id)]
                except sqlite3.IntegrityError:
                    raise RequestError(409, 'Unable to create this account. Try logging in or use another email.')
                user = {'id': user_id, 'username': username, 'email': email}
            else:
                row = db.execute('SELECT * FROM users WHERE email = ?', (email,)).fetchone()
                candidate = password_hash(password, row['salt'] if row else self.dummy_salt)
                matches = hmac.compare_digest(candidate, row['password_hash'] if row else '0' * 128)
                if not row or not matches:
                    raise RequestError(401, 'Email or password is incorrect.')
                user = {key: row[key] for key in ('id', 'username', 'email')}
                with db:
                    headers = [self.issue_session(db, environ, user['id'])]
            return (303, '', headers + [('Location', '/account.html')]) if form else (201 if registering else 200, {'user': user}, headers)
        if method not in ('GET', 'HEAD'):
            raise RequestError(405, 'Method not allowed.')
        if path in ('/login.html', '/register.html'):
            return 200, self.render(path[1:]), []
        if path == '/account.html':
            user = self.user(db, environ)
            if not user:
                return 302, '', [('Location', '/login.html')]
            return 200, self.render('account.html', **user), []
        if path in PUBLIC_FILES:
            return 200, (ROOT / ('index.html' if path == '/' else path[1:])).read_bytes(), [('Content-Type', PUBLIC_FILES[path] + '; charset=utf-8')]
        raise RequestError(404, 'Not found.')

    def __call__(self, environ, start_response):
        try:
            with closing(self.connect()) as db:
                status, body, headers = self.dispatch(environ, db)
        except RequestError as error:
            status, body, headers = error.status, {'error': error.message}, []
            if status == 429:
                headers.append(('Retry-After', '900'))
            path = environ.get('PATH_INFO', '')
            if (environ.get('CONTENT_TYPE', '').split(';')[0].strip() == 'application/x-www-form-urlencoded'
                    and path in AUTH_PATHS):
                body = self.render('register.html' if path.endswith('/register') else 'login.html', error=error.message)
        except Exception:
            logging.exception('Authentication request failed')
            status, body, headers = 500, {'error': 'Something went wrong. Please try again.'}, []
        if isinstance(body, dict):
            body = json.dumps(body).encode('utf-8')
            headers.append(('Content-Type', 'application/json; charset=utf-8'))
        elif isinstance(body, str):
            body = body.encode('utf-8')
            headers.append(('Content-Type', 'text/html; charset=utf-8'))
        headers.extend([('Content-Length', str(len(body))), ('Cache-Control', 'no-store'),
                        ('X-Content-Type-Options', 'nosniff'), ('Referrer-Policy', 'same-origin'),
                        ('X-Frame-Options', 'DENY')])
        start_response(f'{status} {HTTPStatus(status).phrase}', headers)
        return [b'' if environ['REQUEST_METHOD'] == 'HEAD' else body]


def create_app():
    port = int(os.environ.get('PORT', '8000'))
    production = os.environ.get('APP_ENV') == 'production'
    origin = os.environ.get('APP_ORIGIN', f'http://localhost:{port}')
    if production and not os.environ.get('APP_ORIGIN'):
        raise ValueError('Production requires an explicit HTTPS APP_ORIGIN.')
    return AuthApp(os.environ.get('DB_PATH'), origin, production)


if __name__ == '__main__':
    app = create_app()
    with make_server('0.0.0.0', int(os.environ.get('PORT', '8000')), app) as server:
        print(f'Art House is listening at {app.origin}', flush=True)
        server.serve_forever()
