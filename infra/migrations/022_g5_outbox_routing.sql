-- Delivery consumes intent events. Approval notifications belong to their workflow consumers.
CREATE INDEX g5_outbox_delivery ON outbox_event(tenant_id,event_type,created_at);
UPDATE schema_revision SET version='022';
