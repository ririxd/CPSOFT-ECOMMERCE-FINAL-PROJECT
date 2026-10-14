"""Integration tests exercise the WSGI application and real temporary SQLite files."""
import hashlib
import io
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from urllib.parse import urlencode, urlsplit
from unittest.mock import patch
import time
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
        parsed_path = urlsplit(path)
        environ.update(PATH_INFO=parsed_path.path, QUERY_STRING=parsed_path.query, REQUEST_METHOD=method or ('POST' if data is not None or raw is not None else 'GET'),
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
        self.assertEqual(login['headers']['Location'], '/buyer-dashboard.html')

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

    def test_artist_dashboard_page_and_data(self):
        with sqlite3.connect(self.db_path) as db:
            db.execute("INSERT INTO users (username, email, salt, password_hash, persona) VALUES (?, ?, ?, ?, 'artist')",
                       ('dashboard_artist', 'dashboard@example.test', '0' * 32, '0' * 128))
            user_id = db.execute('SELECT id FROM users WHERE email = ?', ('dashboard@example.test',)).fetchone()[0]
        db.close()
        with self.app.connect() as db:
            with db:
                set_cookie = self.app.issue_session(db, {}, user_id)
        db.close()
        cookie = set_cookie[1].split(';')[0]
        page = self.request('/seller-dashboard.html', cookie=cookie)
        self.assertEqual(page['status'], 200)
        self.assertIn('Your work, in one place.', page['body'])
        self.assertNotIn('{{username}}', page['body'])
        dashboard = self.request('/api/seller/dashboard', cookie=cookie)
        self.assertEqual(dashboard['status'], 200)
        self.assertEqual(json.loads(dashboard['body'])['products'], [])
        image = 'data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII='
        created = self.request('/api/seller/products', {
            'title': 'Image study', 'description': 'A small study', 'price': '12.50',
            'inventory': 2, 'low_stock_threshold': 1, 'images': [image]
        }, cookie=cookie)
        self.assertEqual(created['status'], 201)
        dashboard = self.request('/api/seller/dashboard', cookie=cookie)
        self.assertEqual(json.loads(dashboard['body'])['products'][0]['images'], [image])

    def community_account(self, name, persona):
        with self.app.connect() as db:
            with db:
                user_id = db.execute("""INSERT INTO users (username, email, salt, password_hash, persona)
                    VALUES (?, ?, ?, ?, ?) RETURNING id""", (name, name + '@example.test', '0' * 32, '0' * 128, persona)).fetchone()['id']
                user = dict(db.execute('SELECT * FROM users WHERE id = ?', (user_id,)).fetchone())
                if persona == 'artist':
                    self.app.create_seller_for_user(db, user)
                else:
                    account_id = db.execute("INSERT INTO registered_accounts (account_type) VALUES ('user') RETURNING id").fetchone()['id']
                    db.execute("""INSERT INTO account_credentials (account_id, username, email, password_salt, password_hash)
                        VALUES (?, ?, ?, ?, ?)""", (account_id, name, user['email'], user['salt'], user['password_hash']))
                account_id = self.app.account_id_for_user(db, user)
                cookie = self.app.issue_session(db, {}, user_id)[1].split(';')[0]
        return account_id, cookie

    def test_following_is_authenticated_idempotent_and_persistent(self):
        seller, seller_cookie = self.community_account('follow_seller', 'artist')
        buyer, buyer_cookie = self.community_account('follow_buyer', 'collector')
        other, other_cookie = self.community_account('follow_other', 'artist')
        path = '/api/profiles/' + str(seller) + '/follow'
        self.assertEqual(self.request('/api/social/me')['status'], 401)
        self.assertEqual(self.request(path, {'following': True})['status'], 401)
        self.assertEqual(self.request(path, {'following': True}, cookie=buyer_cookie, origin='https://other.test')['status'], 403)
        self.assertEqual(self.request(path, {'following': 'true'}, cookie=buyer_cookie)['status'], 400)
        self.assertEqual(self.request(path, {'following': True}, cookie=seller_cookie)['status'], 400)
        self.assertEqual(self.request('/api/profiles/99999/follow', {'following': True}, cookie=buyer_cookie)['status'], 404)
        for _ in range(2):
            self.assertEqual(self.request(path, {'following': True}, cookie=buyer_cookie)['status'], 200)
        self.request(path, {'following': True}, cookie=other_cookie)
        self.request('/api/profiles/' + str(buyer) + '/follow', {'following': True}, cookie=seller_cookie)
        self.app = AuthApp(self.db_path)
        mine = json.loads(self.request('/api/social/me', cookie=buyer_cookie)['body'])
        self.assertEqual(mine['following_ids'], [seller])
        self.assertEqual(mine['following_count'], 1)
        self.assertEqual(mine['followers_count'], 1)
        profile = json.loads(self.request('/api/profiles/' + str(seller), cookie=buyer_cookie)['body'])['profile']
        self.assertEqual(profile['followers_count'], 2)
        self.assertTrue(profile['is_following'])
        self.assertFalse(profile['is_self'])
        own_profile = json.loads(self.request('/api/profiles/' + str(seller), cookie=seller_cookie)['body'])['profile']
        self.assertTrue(own_profile['is_self'])
        followers = json.loads(self.request('/api/profiles/' + str(seller) + '/followers')['body'])['profiles']
        self.assertEqual({row['id'] for row in followers}, {buyer, other})
        self.assertTrue(all('email' not in row for row in followers))
        following = json.loads(self.request('/api/profiles/' + str(buyer) + '/following')['body'])['profiles']
        self.assertEqual([row['id'] for row in following], [seller])
        for _ in range(2):
            self.assertEqual(self.request(path, {'following': False}, cookie=buyer_cookie)['status'], 200)
        mine = json.loads(self.request('/api/social/me', cookie=buyer_cookie)['body'])
        self.assertEqual(mine['following_count'], 0)
        self.assertEqual(json.loads(self.request('/api/profiles/' + str(seller))['body'])['profile']['followers_count'], 1)

    def test_follow_list_pagination_upgrade_and_cascade(self):
        target, cookie = self.community_account('pagination_artist', 'artist')
        # Upgrading an existing database installs the new table without losing accounts.
        with self.app.connect() as db:
            db.execute('DROP TABLE follows')
        self.app = AuthApp(self.db_path)
        for index in range(51):
            follower, follower_cookie = self.community_account('pagination_user_' + str(index), 'collector')
            self.assertEqual(self.request('/api/profiles/' + str(target) + '/follow', {'following': True}, cookie=follower_cookie)['status'], 200)
        first = json.loads(self.request('/api/profiles/' + str(target) + '/followers')['body'])
        second = json.loads(self.request('/api/profiles/' + str(target) + '/followers?offset=50')['body'])
        self.assertEqual(len(first['profiles']), 50)
        self.assertTrue(first['has_more'])
        self.assertEqual(len(second['profiles']), 1)
        self.assertFalse(second['has_more'])
        self.assertFalse({row['id'] for row in first['profiles']} & {row['id'] for row in second['profiles']})
        self.assertEqual(self.request('/api/profiles/' + str(target) + '/followers?offset=-1')['status'], 400)
        with self.app.connect() as db:
            # The PostgreSQL migration uses compatible standard SQL; execute it twice
            # against SQLite too to verify idempotent schema statements.
            db.executescript((Path(__file__).resolve().parents[1] / 'migrations/009_social_connections.sql').read_text())
            db.executescript((Path(__file__).resolve().parents[1] / 'migrations/009_social_connections.sql').read_text())
            with db:
                db.execute('DELETE FROM registered_accounts WHERE id = ?', (target,))
            self.assertEqual(db.execute('SELECT COUNT(*) AS total FROM follows').fetchone()['total'], 0)

    def test_chat_all_account_types_preserves_legacy_threads_and_privacy(self):
        artist_a = self.community_account('chat_artist_a', 'artist')
        artist_b = self.community_account('chat_artist_b', 'artist')
        buyer_a = self.community_account('chat_buyer_a', 'collector')
        buyer_b = self.community_account('chat_buyer_b', 'collector')
        outsider = self.community_account('chat_observer', 'collector')
        # Legacy cross-role rows were ordered by role, rather than by account ID.
        with self.app.connect() as db:
            with db:
                legacy = db.execute('INSERT INTO conversations (buyer_id,seller_id,created_at) VALUES (?,?,1) RETURNING id', (buyer_a[0], artist_a[0])).fetchone()['id']
                db.execute("INSERT INTO messages (conversation_id,sender_id,body,created_at) VALUES (?,?,'Legacy message',1)", (legacy, artist_a[0]))
        for first, second in [(artist_a, artist_b), (buyer_a, buyer_b), (artist_a, buyer_a)]:
            for sender, receiver, text in [(first, second, 'Hello'), (second, first, 'Reply')]:
                sent = self.request('/api/chat', {'account_id':receiver[0], 'message':text}, cookie=sender[1])
                self.assertEqual(sent['status'], 201)
            for owner, target in [(first,second),(second,first)]:
                read = self.request('/api/chat?account_id=' + str(target[0]), cookie=owner[1])
                self.assertEqual(read['status'], 200)
                messages = json.loads(read['body'])['messages']
                self.assertEqual([row['body'] for row in messages][-2:], ['Hello', 'Reply'])
                self.assertEqual([row['sender_id'] for row in messages][-2:], [first[0],second[0]])
                if first == artist_a and second == buyer_a:
                    self.assertEqual(messages[0]['body'], 'Legacy message')
            with self.app.connect() as db:
                count = db.execute('SELECT COUNT(*) AS total FROM conversations WHERE (buyer_id=? AND seller_id=?) OR (buyer_id=? AND seller_id=?)', (first[0],second[0],second[0],first[0])).fetchone()['total']
                self.assertEqual(count, 1)
        private = self.request('/api/chat?account_id=' + str(artist_a[0]), cookie=outsider[1])
        self.assertEqual(json.loads(private['body'])['messages'], [])
        self.assertEqual(json.loads(self.request('/api/chat/conversations', cookie=outsider[1])['body'])['conversations'], [])
        self.assertEqual(self.request('/api/chat', {'account_id':artist_a[0], 'message':'No'}, cookie=artist_a[1])['status'], 400)
        self.assertEqual(self.request('/api/chat', {'account_id':artist_b[0], 'message':'No'}, cookie=artist_a[1], origin='https://other.test')['status'], 403)
        self.assertEqual(self.request('/api/chat', {'account_id':artist_a[0], 'message':'No'})['status'], 401)
        # Changing persona cannot hide an existing conversation.
        with self.app.connect() as db:
            with db:
                db.execute("UPDATE users SET persona='collector' WHERE username='chat_artist_b'")
        self.assertEqual(len(json.loads(self.request('/api/chat?account_id=' + str(artist_b[0]), cookie=artist_a[1])['body'])['messages']), 2)
        # Long conversations return the most recent 200 messages, chronologically.
        with self.app.connect() as db:
            conversation = db.execute('SELECT id FROM conversations WHERE buyer_id=? AND seller_id=?', (artist_a[0],artist_b[0])).fetchone()['id']
            with db:
                for index in range(205):
                    db.execute('INSERT INTO messages (conversation_id,sender_id,body,created_at) VALUES (?,?,?,?)', (conversation,artist_a[0], 'Recent ' + str(index), 9999999999999 + index))
        latest = json.loads(self.request('/api/chat?account_id=' + str(artist_b[0]), cookie=artist_a[1])['body'])['messages']
        self.assertEqual(len(latest), 200)
        self.assertEqual(latest[0]['body'], 'Recent 5')
        self.assertEqual(latest[-1]['body'], 'Recent 204')

    def test_admin_dashboard_summary_and_management(self):
        self.assertEqual(self.request('/api/admin/dashboard')['status'], 403)
        self.assertEqual(self.request('/admin-dashboard.html')['status'], 302)
        with self.app.connect() as db:
            with db:
                cookie = self.app.issue_admin_session(db, {})[1].split(';')[0]
        empty = self.request('/api/admin/dashboard', cookie=cookie)
        self.assertEqual(empty['status'], 200)
        self.assertEqual(json.loads(empty['body'])['summary']['revenue_cents'], 0)
        self.assertEqual(json.loads(empty['body'])['summary']['pending_orders'], 0)
        day_ms = 86400000
        today = int(time.time() * 1000) // day_ms * day_ms
        with self.app.connect() as db:
            with db:
                for name, persona in [('admin_buyer', 'collector'), ('admin_artist', 'artist')]:
                    db.execute("""INSERT INTO users (username, email, salt, password_hash, persona)
                        VALUES (?, ?, ?, ?, ?)""", (name, name + '@example.test', '0' * 32, '0' * 128, persona))
                    user = dict(db.execute('SELECT * FROM users WHERE username = ?', (name,)).fetchone())
                    self.app.create_seller_for_user(db, user)
                    db.execute('UPDATE registered_accounts SET created_at = ? WHERE id = ?',
                               (today, self.app.account_id_for_user(db, user)))
                buyer_id = db.execute("SELECT account_id FROM account_credentials WHERE username = 'admin_buyer'").fetchone()['account_id']
                seller_id = db.execute("SELECT account_id FROM account_credentials WHERE username = 'admin_artist'").fetchone()['account_id']
                normal_cookie = self.app.issue_session(db, {}, user['id'])[1].split(';')[0]
                db.execute("""INSERT INTO listed_items (seller_id, title, price_cents, inventory, status)
                    VALUES (?, 'Admin catalog draft', 5000, 3, 'draft')""", (seller_id,))
                for cents, timestamp in [(10000, today), (5000, today - day_ms),
                                         (7000, today - 7 * day_ms), (4000, today - 30 * day_ms)]:
                    db.execute("""INSERT INTO checkouts (buyer_id, status, total_cents, created_at, paid_at)
                        VALUES (?, 'paid', ?, ?, ?)""", (buyer_id, cents, today - 40 * day_ms, timestamp))
                for _ in range(101):
                    db.execute("INSERT INTO checkouts (buyer_id, status, total_cents, created_at) VALUES (?, 'pending', 2000, ?)", (buyer_id, today))
                db.execute("INSERT INTO checkouts (buyer_id, status, total_cents, created_at) VALUES (?, 'refunded', 9900, ?)", (buyer_id, today))
                ticket_id = db.execute("""INSERT INTO support_tickets (account_id, subject, message, created_at)
                    VALUES (?, 'Admin support check', 'Synthetic support request', ?) RETURNING id""", (buyer_id, today)).fetchone()['id']
        self.assertEqual(self.request('/api/admin/dashboard', cookie=normal_cookie)['status'], 403)
        banned = self.request('/api/admin/accounts/' + str(buyer_id) + '/ban',
                              {'banned': True, 'reason': 'Test moderation'}, cookie=cookie)
        self.assertEqual(banned['status'], 200)
        with patch('server.now_ms', return_value=today + 43200000):
            response = self.request('/api/admin/dashboard', cookie=cookie)
        self.assertEqual(response['status'], 200)
        self.assertEqual(response['headers']['Cache-Control'], 'no-store')
        data = json.loads(response['body'])
        self.assertEqual(data['summary']['revenue_cents'], 26000)
        self.assertEqual(data['summary']['pending_orders'], 101)
        self.assertEqual(data['summary']['artist_signups_today'], 1)
        self.assertEqual(data['summary']['flagged_accounts'], 1)
        self.assertEqual(data['summary']['previous_week_cents'], 7000)
        self.assertEqual(len(data['summary']['daily_sales']), 7)
        self.assertEqual([day['total_cents'] for day in data['summary']['daily_sales']], [0, 0, 0, 0, 0, 5000, 10000])
        self.assertEqual(len(data['orders']), 100)
        self.assertEqual(data['orders'][0]['buyer_username'], 'admin_buyer')
        self.assertIn('fulfillment_status', data['orders'][0])
        self.assertEqual(data['products'][0]['status'], 'draft')
        self.assertEqual(data['moderation'][0]['reason'], 'Test moderation')
        closed = self.request('/api/admin/support/' + str(ticket_id) + '/close', cookie=cookie, method='POST')
        self.assertEqual(closed['status'], 200)
        self.assertEqual(json.loads(self.request('/api/admin/dashboard', cookie=cookie)['body'])['tickets'][0]['status'], 'closed')
        unbanned = self.request('/api/admin/accounts/' + str(buyer_id) + '/ban', {'banned': False}, cookie=cookie)
        self.assertEqual(unbanned['status'], 200)
        self.assertEqual(json.loads(self.request('/api/admin/dashboard', cookie=cookie)['body'])['summary']['flagged_accounts'], 0)
        page = self.request('/admin-dashboard.html', cookie=cookie)
        self.assertEqual(page['status'], 200)
        self.assertIn('id="sales-chart"', page['body'])
        self.assertIn('id="admin-search"', page['body'])

    def test_recent_conversations_are_private_ordered_and_persistent(self):
        def account(name, persona):
            with self.app.connect() as db:
                with db:
                    user_id = db.execute("""INSERT INTO users
                        (username, email, salt, password_hash, persona)
                        VALUES (?, ?, ?, ?, ?) RETURNING id""",
                        (name, name + '@example.test', '0' * 32, '0' * 128, persona)).fetchone()['id']
                    user = dict(db.execute('SELECT * FROM users WHERE id = ?', (user_id,)).fetchone())
                    self.app.create_seller_for_user(db, user)
                    account_id = self.app.account_id_for_user(db, user)
                    cookie = self.app.issue_session(db, {}, user_id)[1].split(';')[0]
            return account_id, cookie

        buyer, buyer_cookie = account('chat_buyer', 'collector')
        seller, seller_cookie = account('chat_seller', 'artist')
        other_seller, other_cookie = account('other_seller', 'artist')
        outsider, outsider_cookie = account('chat_outsider', 'collector')
        with sqlite3.connect(self.db_path) as db:
            db.execute('INSERT INTO conversations (buyer_id, seller_id, created_at) VALUES (?, ?, ?)',
                       (buyer, seller, 1234567890000))
        self.assertEqual(self.request('/api/chat/conversations')['status'], 401)
        empty = self.request('/api/chat/conversations', cookie=buyer_cookie)
        self.assertEqual(empty['status'], 200)
        self.assertEqual(json.loads(empty['body'])['conversations'], [])

        def send(cookie, target, message):
            result = self.request('/api/chat', {'account_id': target, 'message': message}, cookie=cookie)
            self.assertEqual(result['status'], 201)

        send(buyer_cookie, seller, 'First question')
        send(buyer_cookie, other_seller, 'Second question')
        send(seller_cookie, buyer, '<img src=x onerror=alert(1)> Latest reply')
        send(outsider_cookie, other_seller, 'Private outsider conversation')
        # Equal timestamps exercise the stable message-ID ordering.
        with sqlite3.connect(self.db_path) as db:
            db.execute('UPDATE messages SET created_at = 1234567890000')
        self.app = AuthApp(self.db_path)
        result = self.request('/api/chat/conversations', cookie=buyer_cookie)
        self.assertEqual(result['headers']['Cache-Control'], 'no-store')
        recent = json.loads(result['body'])['conversations']
        self.assertEqual([row['id'] for row in recent], [seller, other_seller])
        self.assertEqual(recent[0]['username'], 'chat_seller')
        self.assertEqual(recent[0]['last_message'], '<img src=x onerror=alert(1)> Latest reply')
        self.assertEqual(recent[0]['sender_id'], seller)
        self.assertEqual(recent[0]['last_message_at'], 1234567890000)
        self.assertNotIn('email', recent[0])
        seller_recent = json.loads(self.request('/api/chat/conversations', cookie=seller_cookie)['body'])['conversations']
        self.assertEqual([row['id'] for row in seller_recent], [buyer])
        self.assertEqual(seller_recent[0]['conversation_id'], recent[0]['conversation_id'])
        outsider_recent = json.loads(self.request('/api/chat/conversations', cookie=outsider_cookie)['body'])['conversations']
        self.assertEqual([row['id'] for row in outsider_recent], [other_seller])
        # Timestamps take priority over IDs for both the preview and list order.
        with sqlite3.connect(self.db_path) as db:
            db.execute("UPDATE messages SET created_at = 1234567890002 WHERE body = 'First question'")
            db.execute("UPDATE messages SET created_at = 1234567890003 WHERE body = 'Second question'")
        reordered = json.loads(self.request('/api/chat/conversations', cookie=buyer_cookie)['body'])['conversations']
        self.assertEqual([row['id'] for row in reordered], [other_seller, seller])
        self.assertEqual(reordered[1]['last_message'], 'First question')
        for cookie, page in [(buyer_cookie, 'buyer'), (seller_cookie, 'seller')]:
            html = self.request('/' + page + '-dashboard.html', cookie=cookie)
            self.assertEqual(html['status'], 200)
            self.assertIn('id="chat-conversations"', html['body'])
            self.assertNotIn('{{account_id}}', html['body'])

    def test_buyer_catalog_cart_checkout_and_order_status(self):
        image = 'data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII='
        with sqlite3.connect(self.db_path) as db:
            db.execute("INSERT INTO users (username, email, salt, password_hash, persona) VALUES (?, ?, ?, ?, 'collector')",
                       ('buyer_user', 'buyer@example.test', '0' * 32, '0' * 128))
            user_id = db.execute('SELECT id FROM users WHERE email = ?', ('buyer@example.test',)).fetchone()[0]
            account_id = db.execute("INSERT INTO registered_accounts (account_type) VALUES ('user')").lastrowid
            db.execute('''INSERT INTO account_credentials
                (account_id, username, email, password_salt, password_hash)
                VALUES (?, ?, ?, ?, ?)''',
                       (account_id, 'buyer_user', 'buyer@example.test', '0' * 32, '0' * 128))
            seller_id = db.execute("INSERT INTO registered_accounts (account_type) VALUES ('seller')").lastrowid
            db.execute("INSERT INTO sellers (id, display_name, payment_details) VALUES (?, ?, ?)",
                       (seller_id, 'Test Seller', 'Bank: 123456'))
            item_id = db.execute('''INSERT INTO listed_items
                (seller_id, title, description, price_cents, inventory, images, status)
                VALUES (?, ?, ?, ?, ?, ?, 'active')''',
                (seller_id, 'Test print', 'A test print', 2500, 3, json.dumps([image]))).lastrowid
        db.close()
        with self.app.connect() as db:
            with db:
                set_cookie = self.app.issue_session(db, {}, user_id)
        db.close()
        cookie = set_cookie[1].split(';')[0]
        self.assertEqual(self.request('/api/catalog')['status'], 200)
        catalog = json.loads(self.request('/api/catalog')['body'])['products']
        self.assertEqual(catalog[0]['title'], 'Test print')
        with sqlite3.connect(self.db_path) as db:
            db.execute("UPDATE listed_items SET status = 'draft' WHERE id = ?", (item_id,))
        db.close()
        catalog = json.loads(self.request('/api/catalog')['body'])['products']
        self.assertEqual(catalog[0]['status'], 'draft')
        cart = self.request('/api/buyer/cart', {'item_id': item_id, 'quantity': 2}, cookie=cookie)
        self.assertEqual(cart['status'], 404)
        with sqlite3.connect(self.db_path) as db:
            db.execute("UPDATE listed_items SET status = 'active' WHERE id = ?", (item_id,))
        db.close()
        cart = self.request('/api/buyer/cart', {'item_id': item_id, 'quantity': 2}, cookie=cookie)
        self.assertEqual(cart['status'], 200)
        cart_data = json.loads(cart['body'])
        self.assertEqual(cart_data['cart'][0]['payment_details'], 'Bank: 123456')
        checkout = self.request('/api/buyer/checkout',
                                {'payment_method': 'online', 'payment_reference': 'TX-123'},
                                cookie=cookie)
        self.assertEqual(checkout['status'], 201)
        order_id = json.loads(checkout['body'])['order_id']
        with sqlite3.connect(self.db_path) as db:
            db.execute("UPDATE checkouts SET fulfillment_status = 'delivered' WHERE id = ?", (order_id,))
        db.close()
        orders = self.request('/api/buyer/cart', cookie=cookie)
        self.assertEqual(orders['status'], 200)
        buyer_orders = json.loads(orders['body'])
        self.assertEqual(buyer_orders['orders'], [])
        self.assertEqual(buyer_orders['completed_orders'][0]['fulfillment_status'], 'delivered')


if __name__ == '__main__':
    unittest.main()
