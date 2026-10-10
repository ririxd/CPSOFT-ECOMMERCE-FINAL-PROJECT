-- Registration is stored only as a pending record until its email OTP is verified.
ALTER TABLE users ADD COLUMN IF NOT EXISTS persona TEXT
    CHECK (persona IS NULL OR persona IN (
        'artist', 'collector', 'enthusiast', 'interior-designer', 'gallery-professional'
    ));

CREATE TABLE IF NOT EXISTS pending_registrations (
    email TEXT PRIMARY KEY,
    username TEXT NOT NULL UNIQUE,
    salt TEXT NOT NULL,
    password_hash TEXT NOT NULL,
    code_hash TEXT NOT NULL,
    expires BIGINT NOT NULL,
    attempts INTEGER NOT NULL DEFAULT 0,
    created_at BIGINT NOT NULL
);
