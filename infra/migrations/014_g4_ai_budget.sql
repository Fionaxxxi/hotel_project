ALTER TABLE g4_ai_task ADD COLUMN budget_cycle int NOT NULL DEFAULT 1 CHECK(budget_cycle BETWEEN 1 AND 3);
DROP TRIGGER g4_ai_immutable ON g4_ai_task;
CREATE TRIGGER g4_ai_immutable BEFORE UPDATE ON g4_ai_task FOR EACH ROW
 EXECUTE FUNCTION g3_immutable('state','result','owner','fence','lease_until','attempts','budget_cycle');
UPDATE schema_revision SET version='014';
