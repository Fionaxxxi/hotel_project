CREATE TABLE g3_remote_quota(account_id text PRIMARY KEY,tokens numeric NOT NULL,updated_at timestamptz NOT NULL);
ALTER TABLE g3_remote_quota ENABLE ROW LEVEL SECURITY;
ALTER TABLE g3_remote_quota FORCE ROW LEVEL SECURITY;
CREATE POLICY account_scope ON g3_remote_quota USING(account_id=current_setting('sim.account',true)) WITH CHECK(account_id=current_setting('sim.account',true));
GRANT SELECT,INSERT,UPDATE ON g3_remote_quota TO hotel_sim_app;
