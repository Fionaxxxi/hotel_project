DROP TRIGGER g4_subscription_immutable ON g4_subscription;
CREATE TRIGGER g4_subscription_immutable BEFORE UPDATE ON g4_subscription FOR EACH ROW
 EXECUTE FUNCTION g3_immutable('state','version','policy_id','initial_activation_id');
UPDATE schema_revision SET version='013';
