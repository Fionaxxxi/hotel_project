CREATE TABLE sim_revision(version text PRIMARY KEY);
INSERT INTO sim_revision VALUES('001');
CREATE TABLE sim_listing (
 account_id text NOT NULL, listing_key text NOT NULL, revision bigint NOT NULL CHECK(revision>0),
 payload_hash text NOT NULL,payload jsonb NOT NULL, PRIMARY KEY(account_id,listing_key));
CREATE TABLE sim_operation (
 id uuid PRIMARY KEY, account_id text NOT NULL, idempotency_key text NOT NULL, request_hash text NOT NULL,
 listing_key text NOT NULL, revision bigint NOT NULL,payload_hash text NOT NULL,
 UNIQUE(account_id,idempotency_key));
ALTER TABLE sim_listing ENABLE ROW LEVEL SECURITY;
ALTER TABLE sim_listing FORCE ROW LEVEL SECURITY;
ALTER TABLE sim_operation ENABLE ROW LEVEL SECURITY;
ALTER TABLE sim_operation FORCE ROW LEVEL SECURITY;
CREATE POLICY account_boundary ON sim_listing TO hotel_sim_app
 USING(account_id=current_setting('sim.account',true)) WITH CHECK(account_id=current_setting('sim.account',true));
CREATE POLICY account_boundary ON sim_operation TO hotel_sim_app
 USING(account_id=current_setting('sim.account',true)) WITH CHECK(account_id=current_setting('sim.account',true));
GRANT SELECT ON sim_revision TO hotel_sim_app;
GRANT SELECT,INSERT,UPDATE ON sim_listing TO hotel_sim_app;
GRANT SELECT,INSERT ON sim_operation TO hotel_sim_app;
