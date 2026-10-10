ART HOUSE
=========

Art House is a storefront concept for discovering independent art and prints.
The static home page includes an artwork collection, artist introduction,
newsletter signup, search overlay, and interactive shopping bag preview.

RUN LOCALLY
-----------

Use Python 3.9 or newer with sqlite3 and hashlib.scrypt available. Local SQLite
development uses the Python standard library; production dependencies are in
requirements.txt.

    python3 server.py

Then visit http://localhost:8000. The Python server serves the site and account
API together. A static-only server cannot provide account access. Bootstrap,
Swiper, and Google Fonts load from public CDNs and need internet access.

Run the authentication integration tests with:

    python3 -m unittest discover -s test -v

PROJECT FILES
-------------

server.py        Python WSGI server, authentication, SQLite and PostgreSQL storage
test/test_auth.py Authentication integration tests
migrations/      Supabase PostgreSQL schema migrations
requirements.txt Production Python dependencies
Dockerfile       Container deployment setup
Procfile         WSGI process command for compatible hosts
render.yaml      Render web-service deployment blueprint
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
HttpOnly, SameSite=Lax cookies; only token hashes are stored in the database. Logout
revokes the session. Account APIs and pages are served with Cache-Control: no-store.
Login and registration share persistent limits: 10 attempts per email and 50 per
connection IP in 15 minutes. Forwarded IP headers are deliberately not trusted.
Behind a reverse proxy, the connection IP limit is shared by that proxy's users;
configure suitable edge rate limiting before scaling to a larger audience.

CONFIGURATION AND DEPLOYMENT
----------------------------
LOCAL ENVIRONMENT
-----------------
Copy .env.example to .env and use `python server.py`. Local development uses SQLite.
The default database file is data/auth.sqlite and is excluded from Git.

PRODUCTION DEPLOYMENT
---------------------
The app is a Python WSGI service. Deploy the repository with its Dockerfile or
Procfile and configure these secrets in the hosting platform (never commit them):

APP_ENV=production
APP_ORIGIN=https://your-domain.example
PORT=<provided by host>
DATABASE_URL=<Supabase PostgreSQL connection URI>
APP_SECRET=<at least 32 random characters>
BREVO_SMTP_HOST=smtp-relay.brevo.com
BREVO_SMTP_PORT=2525
BREVO_SMTP_LOGIN=<Brevo SMTP login>
BREVO_SMTP_PASSWORD=<Brevo SMTP key/password>
BREVO_SENDER_EMAIL=<verified Brevo sender address>
BREVO_SENDER_NAME=Art House
# For Vercel, use the HTTPS API instead of SMTP IP allowlisting:
BREVO_API_KEY=<Brevo v3 API key>

In Supabase, open Project > Connect and copy the Session pooler connection URI
(or use the direct URI when the host supports IPv6). Keep the database password
URL-encoded. PostgreSQL connections require SSL. On startup, the app applies the
numbered SQL migrations in migrations/ before accepting requests. Back up the
Supabase database and keep its service credentials private.

For SMTP deployments, find the SMTP relay settings under Transactional >
Settings > SMTP & API. Use the SMTP login and SMTP key (not an API key),
placing the SMTP key in BREVO_SMTP_PASSWORD, and verify the sender
address/domain before deploying. For Vercel, use a Brevo v3 API key in
BREVO_API_KEY instead of relying on SMTP authorized-IP addresses. The API
avoids Vercel's dynamic outbound IP limitation when sending one-time codes.
Reset codes expire after 10 minutes, allow five verification attempts, and are
stored as keyed hashes. Account existence is not disclosed by the request endpoint.
If mail delivery fails, the app logs the failure without logging the OTP or SMTP credentials.

RENDER
------
Create a new Render Web Service from this repository and use the included
`render.yaml` Blueprint, or configure these commands manually:

    Build Command: pip install -r requirements.txt
    Start Command: gunicorn --bind 0.0.0.0:$PORT --workers 2 --threads 4 --timeout 30 "server:create_app()"
    Health Check Path: /healthz

Set these Render environment variables:

    APP_ENV=production
    APP_ORIGIN=https://your-service-name.onrender.com
    APP_SECRET=<at least 32 random characters>
    DATABASE_URL=<Supabase PostgreSQL connection URI>
    BREVO_API_KEY=<Brevo v3 API key>
    BREVO_SENDER_EMAIL=<verified Brevo sender address>
    BREVO_SENDER_NAME=Art House

`APP_ORIGIN` must exactly match the public Render URL, including `https://` and
without a trailing slash. Render supplies `PORT` automatically. The service
uses `/healthz` for deployment health checks and the Brevo HTTPS API so SMTP
authorized-IP restrictions are not required.

VERCEL
------
Import the repository with the project root as Root Directory. The Python WSGI
entrypoint is `server:app` (configured in pyproject.toml). The Flask adapter
routes all page and API requests through the existing WSGI app. Set Vercel's
Framework Preset to Flask if it does not detect Flask automatically. No Node
build command or output directory is needed. The minimal `vercel.json` only
declares the Vercel schema.

Add the production secrets under Project Settings > Environment Variables, then
redeploy. Set APP_ENV=production, APP_SECRET, DATABASE_URL, BREVO_API_KEY, and
BREVO_SENDER_EMAIL. SMTP variables remain supported for local or fixed-IP
deployments. In the Production environment, set
APP_ORIGIN to the site's exact HTTPS origin (for example,
`https://art-house-cpsoft.vercel.app`); this must match the browser origin used by
forms and account API requests. For Preview deployments, leave APP_ORIGIN unset so
the app can use Vercel's automatic VERCEL_URL value. Add the database, secret, and
Brevo variables to each Vercel environment where registration or password reset
should work. BREVO_SMTP_HOST, BREVO_SMTP_PORT, and BREVO_SENDER_NAME are optional
because the app has defaults for them. Do not upload or commit `.env`.

Use a Brevo v3 API key for Vercel rather than relying on SMTP authorized-IP
addresses, because Vercel's outbound IP is not a stable application setting. To
verify the deployment, open `/register.html` and submit the form. The
`/api/auth/register` endpoint expects a POST, so opening that URL directly in a
browser is not a valid registration check.

Generate APP_SECRET with `python -c "import secrets; print(secrets.token_urlsafe(48))"`.
The host must terminate HTTPS and forward requests to the WSGI service. Production
requires HTTPS APP_ORIGIN and sets Secure, HttpOnly, SameSite=Lax session cookies.
Changing APP_ORIGIN or cookie mode requires users to log in again. Startup fails
when required production configuration is missing.

To migrate an existing local SQLite database, first configure DATABASE_URL and run:

    python migrate_sqlite_to_supabase.py data/auth.sqlite

The script copies accounts, credential hashes, profiles, listings, checkouts, and
login records. Existing sessions and rate-limit counters are intentionally not
copied; users must sign in again. Make a backup before migrating and run the import
only once into an empty Supabase project.

Checkout, payment verification, fulfillment, shipping, inventory reservations,
and reporting are still not implemented. The current bag is a browser-side preview.

The application keeps the existing scrypt parameters and SQLite development
database behavior. Migrated PostgreSQL accounts keep their password hashes, but
users must log in again after the move. `/healthz` is available for host health
checks. Node.js and npm are not needed; storefront interactions use the bundled
browser JavaScript.
