CREATE TABLE g3_remote_listing (
 account_id text NOT NULL,listing_key text NOT NULL,revision bigint NOT NULL DEFAULT 0,
 content jsonb NOT NULL DEFAULT '{}',cells jsonb NOT NULL DEFAULT '{}',manifest jsonb NOT NULL DEFAULT '{}',PRIMARY KEY(account_id,listing_key));
CREATE TABLE g3_remote_operation (
 id uuid PRIMARY KEY,account_id text NOT NULL,request_key text NOT NULL,listing_key text NOT NULL,
 request_hash text NOT NULL,body jsonb NOT NULL,state text NOT NULL,result jsonb NOT NULL DEFAULT '{}',
 ready_at timestamptz NOT NULL DEFAULT now(),UNIQUE(account_id,request_key));
CREATE TABLE g3_remote_fault (
 account_id text NOT NULL,listing_key text NOT NULL,mode text NOT NULL,arg text NOT NULL DEFAULT '',
 remaining int NOT NULL DEFAULT 1,released boolean NOT NULL DEFAULT false,
 PRIMARY KEY(account_id,listing_key));
DO $$ DECLARE t text; BEGIN
 FOREACH t IN ARRAY ARRAY['g3_remote_listing','g3_remote_operation','g3_remote_fault'] LOOP
 EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY',t);
 EXECUTE format('ALTER TABLE %I FORCE ROW LEVEL SECURITY',t);
 EXECUTE format('CREATE POLICY account_scope ON %I USING(account_id=current_setting(''sim.account'',true)) WITH CHECK(account_id=current_setting(''sim.account'',true))',t);
 END LOOP;END $$;
-- The B migration substitutes its separate role; both use the same wire-protocol schema.
GRANT SELECT,INSERT,UPDATE ON g3_remote_listing,g3_remote_operation,g3_remote_fault TO hotel_sim_app;
