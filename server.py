"""Art House WSGI application with SQLite development and Supabase production storage."""
import hashlib
import hmac
import html
import json
import logging
from email.mime.text import MIMEText
import os
from pathlib import Path
import re
import secrets
import smtplib
import ssl
import sqlite3
import time
from contextlib import closing
from http import HTTPStatus
from http.cookies import SimpleCookie, CookieError
from urllib.parse import parse_qs, quote, urlsplit
from wsgiref.simple_server import make_server

ROOT = Path(__file__).resolve().parent


def load_env_file(path):
    """Load simple KEY=value entries without requiring a third-party dotenv package."""
    if not path.is_file():
        return
    for line in path.read_text(encoding='utf-8').splitlines():
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        if line.startswith('export '):
            line = line[7:].lstrip()
        if '=' not in line:
            continue
        name, value = line.split('=', 1)
        name, value = name.strip(), value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
            value = value[1:-1]
        elif ' #' in value:
            value = value.split(' #', 1)[0].rstrip()
        if name and name not in os.environ:
            os.environ[name] = value


load_env_file(ROOT / '.env')
# Brevo SMTP settings follow the lab's configuration format. Keep credentials in
# environment variables instead of putting them in source control.
SMTP_SERVER = os.environ.get('BREVO_SMTP_HOST', 'smtp-relay.brevo.com')
SMTP_PORT = int(os.environ.get('BREVO_SMTP_PORT', '2525'))
SMTP_LOGIN = os.environ.get('BREVO_SMTP_LOGIN', '')
SMTP_PASSWORD = (os.environ.get('BREVO_SMTP_PASSWORD')
                 or os.environ.get('BREVO_SMTP_KEY', ''))
SMTP_SENDER_EMAIL = os.environ.get('BREVO_SENDER_EMAIL', '')
SMTP_SENDER_NAME = os.environ.get('BREVO_SENDER_NAME', 'Art House')
SESSION_SECONDS = 7 * 24 * 60 * 60
PUBLIC_FILES = {
    '/': 'text/html', '/index.html': 'text/html', '/styles.css': 'text/css',
    '/css/vendor.css': 'text/css',
    **{'/js/' + name + '.js': 'text/javascript'
       for name in ('script', 'plugins', 'jquery-1.11.0.min')},
}
AUTH_PATHS = {'/api/auth/register', '/api/auth/login', '/api/auth/logout',
              '/api/auth/register/verify', '/api/auth/profile/persona',
              '/api/auth/password-reset/request', '/api/auth/password-reset/confirm'}


class Database:
    """Small DB-API bridge so local SQLite and hosted PostgreSQL share the app."""
    def __init__(self, connection, postgres=False):
        self.connection = connection
        self.postgres = postgres
        self.transaction_context = None

    def execute(self, statement, parameters=()):
        if self.postgres:
            statement = statement.replace('?', '%s')
        return self.connection.execute(statement, parameters)

    def executescript(self, script):
        if self.postgres:
            for statement in script.split(';'):
                if statement.strip():
                    self.connection.execute(statement)
        else:
            self.connection.executescript(script)

    def __enter__(self):
        if self.postgres:
            self.transaction_context = self.connection.transaction()
            self.transaction_context.__enter__()
        else:
            self.connection.__enter__()
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        if self.postgres:
            result = self.transaction_context.__exit__(exc_type, exc_value, traceback)
            self.transaction_context = None
            return result
        return self.connection.__exit__(exc_type, exc_value, traceback)

    def close(self):
        self.connection.close()


def digest(value):
    return hashlib.sha256(value.encode('utf-8')).hexdigest()


def password_hash(password, salt):
    # Match the original scrypt parameters and ASCII hex salt to retain accounts.
    return hashlib.scrypt(password.encode('utf-8'), salt=salt.encode('ascii'),
                          n=16384, r=8, p=1, dklen=64).hex()


def send_otp_email(receiver_email, otp, intent):
    """Send a one-time code through Brevo SMTP using the lab's flow."""
    if not SMTP_LOGIN or not SMTP_PASSWORD or not SMTP_SENDER_EMAIL:
        raise RuntimeError('Brevo transactional email is not configured.')
    message = MIMEText(
        f'Your {intent} One-Time Password (OTP) is: {otp}\n\n'
        'Please enter this code to proceed. Do not share this code with anyone.'
    )
    message['Subject'] = f'Art House - {intent} OTP'
    message['From'] = f'{SMTP_SENDER_NAME} <{SMTP_SENDER_EMAIL}>'
    message['To'] = receiver_email
    with smtplib.SMTP(SMTP_SERVER, SMTP_PORT, timeout=12) as smtp:
        smtp.ehlo()
        smtp.starttls(context=ssl.create_default_context())
        smtp.ehlo()
        smtp.login(SMTP_LOGIN, SMTP_PASSWORD)
        smtp.send_message(message)
    return True


def now_ms():
    return int(time.time() * 1000)


class RequestError(Exception):
    def __init__(self, status, message):
        self.status = status
        self.message = message


class AuthApp:
    def __init__(self, db_path=None, origin='http://localhost:8000', production=False,
                 database_url=None):
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
        self.app_secret = os.environ.get('APP_SECRET') or secrets.token_hex(32)
        self.database_url = database_url or (None if db_path else
                                             os.environ.get('DATABASE_URL') or os.environ.get('SUPABASE_DB_URL'))
        self.postgres = bool(self.database_url)
        if production and not self.postgres:
            raise ValueError('Production requires DATABASE_URL for Supabase PostgreSQL.')
        if not self.postgres:
            self.db_path = Path(db_path) if db_path else ROOT / 'data' / 'auth.sqlite'
            self.db_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.dummy_salt = secrets.token_hex(16)
        with closing(self.connect()) as db:
            if self.postgres:
                with db:
                    db.execute('SELECT pg_advisory_xact_lock(734532968)')
                    db.execute('''CREATE TABLE IF NOT EXISTS schema_migrations (
                        version TEXT PRIMARY KEY, applied_at BIGINT NOT NULL)''')
                    for migration_path in sorted((ROOT / 'migrations').glob('*.sql')):
                        version = migration_path.name
                        applied = db.execute('SELECT 1 FROM schema_migrations WHERE version = ?',
                                             (version,)).fetchone()
                        if not applied:
                            db.executescript(migration_path.read_text(encoding='utf-8'))
                            db.execute('INSERT INTO schema_migrations (version, applied_at) VALUES (?, ?)',
                                       (version, now_ms()))
            else:
                db.executescript('''
                PRAGMA journal_mode = WAL;
                CREATE TABLE IF NOT EXISTS users (
                    id INTEGER PRIMARY KEY, username TEXT NOT NULL,
                    email TEXT NOT NULL UNIQUE, salt TEXT NOT NULL, password_hash TEXT NOT NULL,
                    persona TEXT CHECK (persona IS NULL OR persona IN
                        ('artist', 'collector', 'enthusiast', 'interior-designer', 'gallery-professional'))
                );
                -- Registered account identity is separate from role-specific profiles
                -- and credential material. Existing users remain the auth compatibility table.
                CREATE TABLE IF NOT EXISTS registered_accounts (
                    id INTEGER PRIMARY KEY, account_type TEXT NOT NULL DEFAULT 'user'
                        CHECK (account_type IN ('user', 'seller')),
                    created_at INTEGER NOT NULL DEFAULT (unixepoch() * 1000)
                );
                CREATE TABLE IF NOT EXISTS account_credentials (
                    account_id INTEGER PRIMARY KEY REFERENCES registered_accounts(id) ON DELETE CASCADE,
                    username TEXT NOT NULL UNIQUE,
                    email TEXT NOT NULL UNIQUE COLLATE NOCASE,
                    password_salt TEXT NOT NULL,
                    password_hash TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS sellers (
                    id INTEGER PRIMARY KEY REFERENCES registered_accounts(id) ON DELETE CASCADE,
                    display_name TEXT NOT NULL,
                    bio TEXT NOT NULL DEFAULT '',
                    created_at INTEGER NOT NULL DEFAULT (unixepoch() * 1000)
                );
                CREATE TABLE IF NOT EXISTS listed_items (
                    id INTEGER PRIMARY KEY,
                    seller_id INTEGER NOT NULL REFERENCES sellers(id),
                    title TEXT NOT NULL,
                    description TEXT NOT NULL DEFAULT '',
                    price_cents INTEGER NOT NULL CHECK (price_cents >= 0),
                    inventory INTEGER NOT NULL DEFAULT 0 CHECK (inventory >= 0),
                    status TEXT NOT NULL DEFAULT 'draft' CHECK (status IN ('draft', 'active', 'archived')),
                    created_at INTEGER NOT NULL DEFAULT (unixepoch() * 1000)
                );
                CREATE TABLE IF NOT EXISTS checkouts (
                    id INTEGER PRIMARY KEY,
                    buyer_id INTEGER NOT NULL REFERENCES registered_accounts(id),
                    status TEXT NOT NULL DEFAULT 'pending'
                        CHECK (status IN ('pending', 'paid', 'cancelled', 'refunded')),
                    total_cents INTEGER NOT NULL CHECK (total_cents >= 0),
                    created_at INTEGER NOT NULL DEFAULT (unixepoch() * 1000)
                );
                CREATE TABLE IF NOT EXISTS checkout_items (
                    checkout_id INTEGER NOT NULL REFERENCES checkouts(id) ON DELETE CASCADE,
                    item_id INTEGER NOT NULL REFERENCES listed_items(id),
                    quantity INTEGER NOT NULL CHECK (quantity > 0),
                    unit_price_cents INTEGER NOT NULL CHECK (unit_price_cents >= 0),
                    PRIMARY KEY (checkout_id, item_id)
                );
                CREATE TABLE IF NOT EXISTS login_events (
                    id INTEGER PRIMARY KEY,
                    account_id INTEGER REFERENCES registered_accounts(id) ON DELETE SET NULL,
                    email TEXT NOT NULL,
                    succeeded INTEGER NOT NULL CHECK (succeeded IN (0, 1)),
                    created_at INTEGER NOT NULL DEFAULT (unixepoch() * 1000)
                );
                CREATE TABLE IF NOT EXISTS sessions (
                    token_hash TEXT PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id),
                    expires INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS attempts (
                    key TEXT PRIMARY KEY, count INTEGER NOT NULL, expires INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS password_reset_codes (
                    email TEXT PRIMARY KEY, code_hash TEXT NOT NULL,
                    expires INTEGER NOT NULL, attempts INTEGER NOT NULL DEFAULT 0,
                    created_at INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS pending_registrations (
                    email TEXT PRIMARY KEY, username TEXT NOT NULL UNIQUE,
                    salt TEXT NOT NULL, password_hash TEXT NOT NULL, code_hash TEXT NOT NULL,
                    expires INTEGER NOT NULL, attempts INTEGER NOT NULL DEFAULT 0,
                    created_at INTEGER NOT NULL
                );
            ''')
                user_columns = {row['name'] for row in db.execute('PRAGMA table_info(users)').fetchall()}
                if 'persona' not in user_columns:
                    db.execute("""ALTER TABLE users ADD COLUMN persona TEXT CHECK
                        (persona IS NULL OR persona IN
                        ('artist', 'collector', 'enthusiast', 'interior-designer', 'gallery-professional'))""")

    def connect(self):
        if self.postgres:
            try:
                import psycopg
                from psycopg.rows import dict_row
            except ImportError as error:
                raise RuntimeError('Install requirements.txt to connect to Supabase PostgreSQL.') from error
            connection = psycopg.connect(self.database_url, row_factory=dict_row,
                                         sslmode=os.environ.get('PGSSLMODE', 'require'),
                                         connect_timeout=10, autocommit=True)
            return Database(connection, postgres=True)
        connection = sqlite3.connect(self.db_path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute('PRAGMA foreign_keys = ON')
        return Database(connection)

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
        row = db.execute('''SELECT users.id, users.username, users.email, users.persona FROM sessions
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
            count = db.execute('SELECT count FROM attempts WHERE key = ?', (key,)).fetchone()['count']
        return count > maximum

    def reset_code_hash(self, email, code):
        return hmac.new(self.app_secret.encode('utf-8'),
                        f'{email}:{code}'.encode('utf-8'), hashlib.sha256).hexdigest()

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
        if path == '/healthz' and method in ('GET', 'HEAD'):
            return 200, {'status': 'ok'}, []
        if path == '/api/auth/me' and method in ('GET', 'HEAD'):
            user = self.user(db, environ)
            return (200, {'user': user}, []) if user else (401, {'error': 'Please log in.'}, [])
        if path in AUTH_PATHS:
            if method != 'POST':
                raise RequestError(405, 'Use POST.')
            if environ.get('HTTP_ORIGIN') != self.origin:
                raise RequestError(403, 'Request origin is not allowed.')
            form = environ.get('CONTENT_TYPE', '').split(';')[0].strip() == 'application/x-www-form-urlencoded'
            if path == '/api/auth/register':
                data = self.read_body(environ, form)
                email = data.get('email', '')
                username = data.get('username', '')
                password = data.get('password', '')
                email = email.strip().lower() if isinstance(email, str) else ''
                username = username.strip() if isinstance(username, str) else ''
                if (not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+', email) or len(email) > 254
                        or not re.fullmatch(r'[A-Za-z0-9_-]{3,30}', username)
                        or not isinstance(password, str) or not 12 <= len(password) <= 128):
                    raise RequestError(400, 'Use a valid email, username, and a 12–128 character password.')
                if (self.limited(db, 'register-ip:' + environ.get('REMOTE_ADDR', ''), 30)
                        or self.limited(db, 'register-email:' + digest(email), 3)):
                    raise RequestError(429, 'Too many registration attempts. Try again in 15 minutes.')
                existing = db.execute('SELECT 1 FROM users WHERE email = ? OR username = ?',
                                      (email, username)).fetchone()
                if existing:
                    raise RequestError(409, 'That email or username is already registered.')
                code = f'{secrets.randbelow(1_000_000):06d}'
                salt = secrets.token_hex(16)
                hashed = password_hash(password, salt)
                try:
                    with db:
                        db.execute('''INSERT INTO pending_registrations
                            (email, username, salt, password_hash, code_hash, expires, attempts, created_at)
                            VALUES (?, ?, ?, ?, ?, ?, 0, ?)
                            ON CONFLICT(email) DO UPDATE SET username = excluded.username,
                            salt = excluded.salt, password_hash = excluded.password_hash,
                            code_hash = excluded.code_hash, expires = excluded.expires,
                            attempts = 0, created_at = excluded.created_at''',
                                   (email, username, salt, hashed, self.reset_code_hash(email, code),
                                    now_ms() + 10 * 60 * 1000, now_ms()))
                except Exception as error:
                    if not isinstance(error, sqlite3.IntegrityError) and getattr(error, 'sqlstate', None) not in ('23505', '23503', '23514'):
                        raise
                    raise RequestError(409, 'That email or username is already awaiting verification.')
                try:
                    send_otp_email(email, code, intent='Account Registration')
                except (RuntimeError, ValueError, smtplib.SMTPException, OSError):
                    logging.exception('Brevo registration email could not be sent')
                    with db:
                        db.execute('DELETE FROM pending_registrations WHERE email = ?', (email,))
                    raise RequestError(503, 'We could not send a verification email. Please try again later.')
                if form:
                    return 303, '', [('Location', '/verify-email.html?email=' + quote(email, safe=''))]
                return 202, {'message': 'Verification code sent.', 'verification_required': True,
                             'email': email}, []
            if path == '/api/auth/register/verify':
                data = self.read_body(environ, form)
                email = data.get('email', '')
                code = data.get('code', '')
                email = email.strip().lower() if isinstance(email, str) else ''
                if (not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+', email) or len(email) > 254
                        or not isinstance(code, str) or not re.fullmatch(r'\d{6}', code)):
                    raise RequestError(400, 'Enter a valid email and six-digit verification code.')
                if self.limited(db, 'verify-ip:' + environ.get('REMOTE_ADDR', ''), 30):
                    raise RequestError(429, 'Too many verification attempts. Try again later.')
                pending = db.execute('SELECT * FROM pending_registrations WHERE email = ?', (email,)).fetchone()
                if not pending or pending['expires'] <= now_ms() or pending['attempts'] >= 5:
                    if pending:
                        with db:
                            db.execute('DELETE FROM pending_registrations WHERE email = ?', (email,))
                    raise RequestError(400, 'That verification code is invalid or expired. Start registration again.')
                if not hmac.compare_digest(self.reset_code_hash(email, code), pending['code_hash']):
                    with db:
                        db.execute('UPDATE pending_registrations SET attempts = attempts + 1 WHERE email = ?',
                                   (email,))
                    raise RequestError(400, 'That verification code is invalid or expired.')
                try:
                    with db:
                        user_id = db.execute('''INSERT INTO users (username, email, salt, password_hash)
                            VALUES (?, ?, ?, ?) RETURNING id''',
                                             (pending['username'], email, pending['salt'],
                                              pending['password_hash'])).fetchone()['id']
                        account_id = db.execute("INSERT INTO registered_accounts (account_type) VALUES ('user') RETURNING id").fetchone()['id']
                        db.execute('''INSERT INTO account_credentials
                            (account_id, username, email, password_salt, password_hash)
                            VALUES (?, ?, ?, ?, ?)''',
                                   (account_id, pending['username'], email, pending['salt'],
                                    pending['password_hash']))
                        db.execute('DELETE FROM pending_registrations WHERE email = ?', (email,))
                        headers = [self.issue_session(db, environ, user_id)]
                except Exception as error:
                    if not isinstance(error, sqlite3.IntegrityError) and getattr(error, 'sqlstate', None) not in ('23505', '23503', '23514'):
                        raise
                    raise RequestError(409, 'That email or username is already registered. Please log in.')
                user = {'id': user_id, 'username': pending['username'], 'email': email, 'persona': None}
                if form:
                    return 303, '', headers + [('Location', '/account.html?welcome=1')]
                return 201, {'user': user, 'redirect': '/account.html?welcome=1'}, headers
            if path == '/api/auth/profile/persona':
                user = self.user(db, environ)
                if not user:
                    raise RequestError(401, 'Please log in to finish setting up your account.')
                data = self.read_body(environ, form)
                persona = data.get('persona', '')
                allowed_personas = {'artist', 'collector', 'enthusiast',
                                    'interior-designer', 'gallery-professional'}
                if not isinstance(persona, str) or persona not in allowed_personas:
                    raise RequestError(400, 'Choose one of the listed art-market roles.')
                with db:
                    db.execute('UPDATE users SET persona = ? WHERE id = ?', (persona, user['id']))
                if form:
                    return 303, '', [('Location', '/account.html')]
                return 200, {'message': 'Your Art House profile is ready.', 'persona': persona}, []
            if path.endswith('/password-reset/request'):
                data = self.read_body(environ, form)
                email = data.get('email', '')
                email = email.strip().lower() if isinstance(email, str) else ''
                if not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+', email) or len(email) > 254:
                    raise RequestError(400, 'Enter a valid email address.')
                if self.limited(db, 'reset:' + digest(email), 3):
                    raise RequestError(429, 'Too many reset requests. Try again in 15 minutes.')
                account = db.execute('SELECT id FROM users WHERE email = ?', (email,)).fetchone()
                if account:
                    code = f'{secrets.randbelow(1_000_000):06d}'
                    with db:
                        db.execute('''INSERT INTO password_reset_codes
                            (email, code_hash, expires, attempts, created_at) VALUES (?, ?, ?, 0, ?)
                            ON CONFLICT(email) DO UPDATE SET code_hash = excluded.code_hash,
                            expires = excluded.expires, attempts = 0, created_at = excluded.created_at''',
                                   (email, self.reset_code_hash(email, code), now_ms() + 10 * 60 * 1000, now_ms()))
                    try:
                        send_otp_email(email, code, intent='Password Reset')
                    except (RuntimeError, ValueError, smtplib.SMTPException, OSError):
                        logging.exception('Brevo password reset email could not be sent')
                return 200, {'message': 'If an account uses that email, a reset code will be sent.'}, []
            if path.endswith('/password-reset/confirm'):
                data = self.read_body(environ, form)
                email = data.get('email', '')
                code = data.get('code', '')
                password = data.get('password', '')
                email = email.strip().lower() if isinstance(email, str) else ''
                if (not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+', email) or len(email) > 254
                        or not isinstance(code, str) or not re.fullmatch(r'\d{6}', code)
                        or not isinstance(password, str) or not 12 <= len(password) <= 128):
                    raise RequestError(400, 'Enter the email, six-digit code, and a 12–128 character password.')
                reset = db.execute('SELECT * FROM password_reset_codes WHERE email = ?', (email,)).fetchone()
                if (not reset or reset['expires'] <= now_ms() or reset['attempts'] >= 5
                        or not hmac.compare_digest(self.reset_code_hash(email, code), reset['code_hash'])):
                    with db:
                        db.execute('UPDATE password_reset_codes SET attempts = attempts + 1 WHERE email = ?', (email,))
                    raise RequestError(400, 'That reset code is invalid or expired.')
                salt = secrets.token_hex(16)
                hashed = password_hash(password, salt)
                with db:
                    user = db.execute('SELECT id FROM users WHERE email = ?', (email,)).fetchone()
                    if not user:
                        raise RequestError(400, 'That reset code is invalid or expired.')
                    db.execute('UPDATE users SET salt = ?, password_hash = ? WHERE id = ?',
                               (salt, hashed, user['id']))
                    db.execute('''UPDATE account_credentials SET password_salt = ?, password_hash = ?
                                  WHERE email = ?''', (salt, hashed, email))
                    db.execute('DELETE FROM sessions WHERE user_id = ?', (user['id'],))
                    db.execute('DELETE FROM password_reset_codes WHERE email = ?', (email,))
                return 200, {'message': 'Password updated. You can now log in.'}, []
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
            if (not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+', email) or len(email) > 254
                    or not isinstance(password, str) or not 1 <= len(password) <= 128
                    ):
                raise RequestError(400, 'Enter a valid email and password.')
            if self.limited(db, 'email:' + digest(email), 10):
                raise RequestError(429, 'Too many attempts. Please try again in 15 minutes.')
            row = db.execute('SELECT * FROM users WHERE email = ?', (email,)).fetchone()
            candidate = password_hash(password, row['salt'] if row else self.dummy_salt)
            matches = hmac.compare_digest(candidate, row['password_hash'] if row else '0' * 128)
            if not row or not matches:
                raise RequestError(401, 'Email or password is incorrect.')
            user = {key: row[key] for key in ('id', 'username', 'email', 'persona')}
            with db:
                headers = [self.issue_session(db, environ, user['id'])]
            if form:
                return 303, '', headers + [('Location', '/account.html')]
            return 200, {'user': user}, headers
        if method not in ('GET', 'HEAD'):
            raise RequestError(405, 'Method not allowed.')
        if path in ('/login.html', '/register.html'):
            return 200, self.render(path[1:]), []
        if path == '/verify-email.html':
            email = parse_qs(environ.get('QUERY_STRING', '')).get('email', [''])[0]
            return 200, self.render('verify-email.html', email=email, error=''), []
        if path == '/account.html':
            user = self.user(db, environ)
            if not user:
                return 302, '', [('Location', '/login.html')]
            return 200, self.render('account.html', **{**user, 'persona': user.get('persona') or ''}), []
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
                if path == '/api/auth/register':
                    body = self.render('register.html', error=error.message)
                elif path == '/api/auth/register/verify':
                    body = self.render('verify-email.html', email='', error=error.message)
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
    on_vercel = os.environ.get('VERCEL') == '1'
    production = os.environ.get('APP_ENV') == 'production' or on_vercel
    vercel_url = os.environ.get('VERCEL_URL', '').strip()
    default_origin = f'https://{vercel_url}' if on_vercel and vercel_url else f'http://localhost:{port}'
    configured_origin = os.environ.get('APP_ORIGIN', '').strip()
    origin = configured_origin or default_origin
    if production and not configured_origin and not (on_vercel and vercel_url):
        raise ValueError('Production requires APP_ORIGIN or VERCEL_URL.')
    if production:
        missing = [name for name in ('APP_SECRET', 'BREVO_SMTP_LOGIN', 'BREVO_SENDER_EMAIL')
                   if not os.environ.get(name)]
        if not (os.environ.get('BREVO_SMTP_PASSWORD') or os.environ.get('BREVO_SMTP_KEY')):
            missing.append('BREVO_SMTP_PASSWORD')
        if not (os.environ.get('DATABASE_URL') or os.environ.get('SUPABASE_DB_URL')):
            missing.insert(0, 'DATABASE_URL')
        if missing:
            raise ValueError('Production configuration is missing: ' + ', '.join(missing))
        if len(os.environ['APP_SECRET']) < 32:
            raise ValueError('APP_SECRET must contain at least 32 characters.')
    return AuthApp(os.environ.get('DB_PATH'), origin, production,
                   os.environ.get('DATABASE_URL') or os.environ.get('SUPABASE_DB_URL'))


_vercel_app = None


def app(environ, start_response):
    """Lazy WSGI entrypoint detected by Vercel's Python runtime."""
    global _vercel_app
    if _vercel_app is None:
        _vercel_app = create_app()
    return _vercel_app(environ, start_response)


if __name__ == '__main__':
    local_app = create_app()
    with make_server('0.0.0.0', int(os.environ.get('PORT', '8000')), local_app) as server:
        print(f'Art House is listening at {local_app.origin}', flush=True)
        server.serve_forever()
