-- Aleem chat-history schema for chainlit==2.11.1 SQLAlchemyDataLayer.
--
-- The data layer (chainlit/data/sql_alchemy.py) issues raw INSERT / SELECT
-- against these five tables but ships no migration. We create them on
-- first launch from src/ui/persistence.py — all statements are
-- IF NOT EXISTS, so re-running is a no-op.
--
-- Column names are quoted because some are camelCase (the data layer
-- quotes them in its queries; SQLite is case-sensitive once quoted).
-- Type affinities follow SQLite conventions:
--   - TEXT for IDs (UUID strings), JSON blobs, ISO-8601 timestamps.
--   - INTEGER for booleans (0/1) and numeric flags.

CREATE TABLE IF NOT EXISTS users (
    "id" TEXT PRIMARY KEY,
    "identifier" TEXT NOT NULL UNIQUE,
    "metadata" TEXT NOT NULL,
    "createdAt" TEXT
);

CREATE TABLE IF NOT EXISTS threads (
    "id" TEXT PRIMARY KEY,
    "createdAt" TEXT,
    "name" TEXT,
    "userId" TEXT REFERENCES users("id") ON DELETE CASCADE,
    "userIdentifier" TEXT,
    "tags" TEXT,
    "metadata" TEXT
);

CREATE TABLE IF NOT EXISTS steps (
    "id" TEXT PRIMARY KEY,
    "name" TEXT NOT NULL,
    "type" TEXT NOT NULL,
    "threadId" TEXT,
    "parentId" TEXT,
    "command" TEXT,
    "modes" TEXT,
    "streaming" INTEGER NOT NULL DEFAULT 0,
    "waitForAnswer" INTEGER,
    "isError" INTEGER,
    "metadata" TEXT,
    "tags" TEXT,
    "input" TEXT,
    "output" TEXT,
    "createdAt" TEXT,
    "start" TEXT,
    "end" TEXT,
    "generation" TEXT,
    "showInput" TEXT,
    "defaultOpen" INTEGER,
    "autoCollapse" INTEGER,
    "language" TEXT,
    "icon" TEXT
);

CREATE TABLE IF NOT EXISTS elements (
    "id" TEXT PRIMARY KEY,
    "threadId" TEXT,
    "type" TEXT,
    "url" TEXT,
    "chainlitKey" TEXT,
    "name" TEXT NOT NULL,
    "display" TEXT,
    "objectKey" TEXT,
    "size" TEXT,
    "page" INTEGER,
    "language" TEXT,
    "forId" TEXT,
    "mime" TEXT,
    "props" TEXT
);

CREATE TABLE IF NOT EXISTS feedbacks (
    "id" TEXT PRIMARY KEY,
    "forId" TEXT NOT NULL,
    "threadId" TEXT NOT NULL,
    "value" INTEGER NOT NULL,
    "comment" TEXT
);
