-- schema_revision is a singleton current-version marker, not migration history.
DELETE FROM schema_revision;
INSERT INTO schema_revision(version) VALUES('012');
