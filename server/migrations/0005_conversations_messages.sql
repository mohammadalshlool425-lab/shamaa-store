-- Order-scoped private conversations. Parties are derived from the shipment.
CREATE TABLE conversations (
 id INTEGER PRIMARY KEY,
 seller_order_id INTEGER NOT NULL UNIQUE REFERENCES seller_orders(id) ON DELETE RESTRICT,
 created_at INTEGER NOT NULL,
 updated_at INTEGER NOT NULL
);
CREATE INDEX conversations_seller_order ON conversations(seller_order_id,updated_at);

CREATE TABLE messages (
 id INTEGER PRIMARY KEY,
 conversation_id INTEGER NOT NULL REFERENCES conversations(id) ON DELETE RESTRICT,
 sender_id INTEGER NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
 client_key TEXT NOT NULL CHECK(length(client_key) BETWEEN 16 AND 80),
 body TEXT NOT NULL CHECK(length(body) BETWEEN 1 AND 4000),
 body_hash TEXT NOT NULL CHECK(length(body_hash)=64),
 created_at INTEGER NOT NULL,
 UNIQUE(conversation_id,sender_id,client_key)
);
CREATE INDEX messages_conversation_created ON messages(conversation_id,id);

CREATE TRIGGER messages_sender_participant
BEFORE INSERT ON messages
WHEN NOT EXISTS (
 SELECT 1 FROM conversations c
 JOIN seller_orders so ON so.id=c.seller_order_id
 JOIN orders o ON o.id=so.order_id
 JOIN stores s ON s.id=so.store_id
 WHERE c.id=NEW.conversation_id
   AND (o.buyer_id=NEW.sender_id OR s.owner_id=NEW.sender_id)
)
BEGIN
 SELECT RAISE(ABORT,'message sender is not a conversation participant');
END;
CREATE TRIGGER immutable_messages_update
BEFORE UPDATE ON messages
BEGIN
 SELECT RAISE(ABORT,'append-only');
END;
CREATE TRIGGER immutable_messages_delete
BEFORE DELETE ON messages
BEGIN
 SELECT RAISE(ABORT,'append-only');
END;
