CREATE TABLE IF NOT EXISTS follows (
    follower_id BIGINT NOT NULL REFERENCES registered_accounts(id) ON DELETE CASCADE,
    following_id BIGINT NOT NULL REFERENCES registered_accounts(id) ON DELETE CASCADE,
    created_at BIGINT NOT NULL,
    PRIMARY KEY (follower_id, following_id),
    CHECK (follower_id <> following_id)
);
CREATE INDEX IF NOT EXISTS follows_following ON follows (following_id, created_at DESC);
CREATE INDEX IF NOT EXISTS messages_conversation_recent ON messages (conversation_id, created_at DESC, id DESC);
CREATE INDEX IF NOT EXISTS conversations_seller ON conversations (seller_id);
