ART HOUSE
=========

Art House is a storefront concept for discovering independent art and prints.
The static home page includes an artwork collection, artist introduction,
newsletter signup, search overlay, and interactive shopping bag preview.

RUN LOCALLY
-----------

Use Python 3.9 or newer with sqlite3 and hashlib.scrypt available.
No third-party packages are required for local development.

    python3 server.py

Then visit http://localhost:8000. The Python server serves the site and account
API together. A static-only server cannot provide account access. Bootstrap,
Swiper, and Google Fonts load from public CDNs and need internet access.

Run the authentication integration tests with:

    python3 -m unittest discover -s test -v

PROJECT FILES
-------------

server.py        Python WSGI server, authentication and sqlite3 storage
test/test_auth.py Authentication integration tests
login.html       Login form rendered by Python
register.html    Registration form rendered by Python
account.html     Protected account page rendered by Python
index.html       Page markup and lightweight storefront interactions
styles.css       Base styles, preserved template effects, and Art House design
css/vendor.css   Vendor styles
js/script.js     Existing page interactions and slider setup
js/plugins.js    Existing vendor plugins

CUSTOMIZE
---------

The four featured artwork cards are in the #artwork-grid section of index.html.
Replace their inline SVG artwork, titles, sizes, and prices with the shop's catalog.
Update the artist and story sections, newsletter destination, and contact links
with the shop's information.

The bag currently works as a front-end preview only. Product and quantity
selection are kept in the current browser session; checkout, inventory,
subscriptions, and payment processing need to be connected to a store service.

ARTIST ACCOUNTS
---------------
The navbar's “be a house-artist” link opens login.html. register.html accepts
username, email, and password. Registration signs in immediately and opens the
protected account.html page. That page displays account details and offers logout.
Email addresses are normalized to lowercase and must be unique. Usernames are
3–30 letters, numbers, underscores or hyphens; passwords are 12–128 characters.

server.py provides POST /api/auth/register, POST /api/auth/login,
POST /api/auth/logout and GET /api/auth/me. Mutation requests require the exact
APP_ORIGIN in their Origin header. Endpoints accept JSON or URL-encoded forms.
HTML forms redirect after success and render errors on the page. Login,
registration, account details and logout work without JavaScript.
Passwords use salted scrypt hashes. Seven-day sessions use random tokens in
HttpOnly, SameSite=Lax cookies; only token hashes are stored in SQLite. Logout
revokes the session. Account APIs and pages are served with Cache-Control: no-store.
Login and registration share persistent limits: 10 attempts per email and 50 per
connection IP in 15 minutes. Forwarded IP headers are deliberately not trusted.
Behind a reverse proxy, the connection IP limit is shared by that proxy's users;
configure suitable edge rate limiting before scaling to a larger audience.

CONFIGURATION AND DEPLOYMENT
----------------------------
PORT defaults to 8000. APP_ORIGIN defaults to http://localhost:<PORT>; set it to
the exact browser origin when using a different host or an HTTPS preview URL.
DB_PATH defaults to data/auth.sqlite (ignored by Git and never served publicly).
Use a persistent writable disk for this SQLite file and keep its directory private.
Back up the database consistently, including WAL state, using SQLite backup tools.
Do not place DB_PATH inside a publicly hosted asset directory.

For production, set APP_ENV=production and APP_ORIGIN=https://your-domain.example,
and terminate HTTPS at your hosting platform or reverse proxy. Production startup
rejects a missing or non-HTTPS origin. HTTPS origins enable Secure cookies with
the __Host- prefix. Use a production WSGI server (for example, install Gunicorn in a virtual
environment and run `gunicorn --bind 0.0.0.0:8000 "server:create_app()"`).
The bundled wsgiref server is for local development. Keep the SQLite database
on persistent local disk; static hosting alone is insufficient. Changing the origin or cookie mode requires
users to log in again. To stop locally, press Ctrl+C.

Email verification, password recovery, and artwork listing/upload are not included.
Registration does not verify ownership of an email address.

The Python backend retains the prior database schema, millisecond timestamps,
scrypt parameters and session cookie names. Existing accounts and unexpired
sessions remain valid when DB_PATH and APP_ORIGIN stay the same. Node.js and
npm are not needed. Existing storefront JavaScript is only for shop interactions.
