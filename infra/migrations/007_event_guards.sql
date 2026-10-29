GRANT UPDATE(result) ON consumer_inbox TO hotel_app;
CREATE FUNCTION g2_event_immutable() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN
 IF (NEW.id,NEW.tenant_id,NEW.event_type,NEW.aggregate_id,NEW.aggregate_version,NEW.payload,NEW.trace_id)
 IS DISTINCT FROM (OLD.id,OLD.tenant_id,OLD.event_type,OLD.aggregate_id,OLD.aggregate_version,OLD.payload,OLD.trace_id)
 THEN RAISE EXCEPTION 'immutable event'; END IF; RETURN NEW; END $$;
CREATE TRIGGER immutable_event BEFORE UPDATE ON outbox_event FOR EACH ROW EXECUTE FUNCTION g2_event_immutable();
ALTER TABLE execution_job ADD CONSTRAINT job_states CHECK(state IN ('READY','RUNNING','RETRY_WAIT','SUCCEEDED','BLOCKED','MANUAL','CANCELLED'));
UPDATE schema_revision SET version='007';
