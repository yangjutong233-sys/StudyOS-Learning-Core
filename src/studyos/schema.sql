CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE schema_migrations(version INTEGER PRIMARY KEY, sha256 TEXT NOT NULL, applied_at TEXT NOT NULL);
CREATE TABLE commands(request_id TEXT PRIMARY KEY, request_hash TEXT NOT NULL, result TEXT NOT NULL, occurred_at TEXT NOT NULL);
CREATE TABLE audit_events(
 event_seq INTEGER PRIMARY KEY AUTOINCREMENT, event_id TEXT NOT NULL UNIQUE,
 entity_type TEXT NOT NULL, entity_id TEXT NOT NULL, event_type TEXT NOT NULL,
 occurred_at TEXT NOT NULL, recorded_at TEXT NOT NULL, actor TEXT NOT NULL, source TEXT NOT NULL,
 request_id TEXT NOT NULL REFERENCES commands(request_id) DEFERRABLE INITIALLY DEFERRED,
 previous_revision INTEGER, new_revision INTEGER NOT NULL, payload_version INTEGER NOT NULL CHECK(payload_version=1),
 event_payload TEXT NOT NULL);
CREATE INDEX audit_entity ON audit_events(entity_type,entity_id,event_seq);
CREATE INDEX audit_request ON audit_events(request_id);
CREATE TRIGGER audit_no_update BEFORE UPDATE ON audit_events BEGIN SELECT RAISE(ABORT,'audit append only'); END;
CREATE TRIGGER audit_no_delete BEFORE DELETE ON audit_events BEGIN SELECT RAISE(ABORT,'audit append only'); END;
CREATE TABLE catalogs(id TEXT PRIMARY KEY, subject TEXT NOT NULL CHECK(subject IN ('math2','cs408','english1','politics101')), name TEXT NOT NULL);
CREATE TABLE knowledge_points(id TEXT PRIMARY KEY, catalog_id TEXT NOT NULL REFERENCES catalogs(id), external_id TEXT NOT NULL,
 name TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'UNASSESSED', revision INTEGER NOT NULL DEFAULT 1,
 data TEXT NOT NULL, UNIQUE(catalog_id,external_id));
CREATE TABLE knowledge_dependencies(knowledge_id TEXT NOT NULL REFERENCES knowledge_points(id), prerequisite_id TEXT NOT NULL REFERENCES knowledge_points(id),
 kind TEXT NOT NULL CHECK(kind IN ('strong','weak')), PRIMARY KEY(knowledge_id,prerequisite_id,kind), CHECK(knowledge_id<>prerequisite_id));
CREATE TABLE materials(id TEXT NOT NULL, version INTEGER NOT NULL, subject TEXT NOT NULL, role TEXT NOT NULL,
 path_key TEXT NOT NULL, sha256 TEXT NOT NULL, data TEXT NOT NULL, PRIMARY KEY(id,version));
CREATE TABLE knowledge_materials(knowledge_id TEXT NOT NULL REFERENCES knowledge_points(id), material_id TEXT NOT NULL,
 material_version INTEGER NOT NULL, locator TEXT NOT NULL, FOREIGN KEY(material_id,material_version) REFERENCES materials(id,version),
 PRIMARY KEY(knowledge_id,material_id,material_version));
CREATE TABLE lesson_tasks(id TEXT NOT NULL, version INTEGER NOT NULL, knowledge_id TEXT NOT NULL REFERENCES knowledge_points(id),
 goal TEXT NOT NULL, data TEXT NOT NULL, PRIMARY KEY(id,version));
CREATE TABLE learning_sessions(id TEXT PRIMARY KEY, task_id TEXT NOT NULL, task_version INTEGER NOT NULL,
 status TEXT NOT NULL CHECK(status IN ('ACTIVE','PAUSED','COMPLETED')), started_at TEXT NOT NULL,
 revision INTEGER NOT NULL DEFAULT 1, FOREIGN KEY(task_id,task_version) REFERENCES lesson_tasks(id,version));
CREATE UNIQUE INDEX one_active_session ON learning_sessions(status) WHERE status='ACTIVE';
CREATE TABLE session_records(id TEXT PRIMARY KEY, session_id TEXT NOT NULL REFERENCES learning_sessions(id), occurred_at TEXT NOT NULL,
 minutes REAL CHECK(minutes>=0), checkpoint TEXT NOT NULL, data TEXT NOT NULL);
CREATE TABLE questions(id TEXT NOT NULL, version INTEGER NOT NULL, knowledge_id TEXT NOT NULL REFERENCES knowledge_points(id),
 question TEXT NOT NULL, data TEXT NOT NULL, PRIMARY KEY(id,version));
CREATE TABLE assessments(id TEXT PRIMARY KEY, knowledge_id TEXT NOT NULL REFERENCES knowledge_points(id), task_id TEXT,
 task_version INTEGER, status TEXT NOT NULL, revision INTEGER NOT NULL DEFAULT 1, blueprint TEXT NOT NULL, result TEXT,
 FOREIGN KEY(task_id,task_version) REFERENCES lesson_tasks(id,version));
CREATE TABLE attempts(id TEXT PRIMARY KEY, question_id TEXT NOT NULL, question_version INTEGER NOT NULL,
 assessment_id TEXT REFERENCES assessments(id), session_id TEXT REFERENCES learning_sessions(id), occurred_at TEXT NOT NULL,
 answer TEXT NOT NULL, assistance TEXT NOT NULL, data TEXT NOT NULL,
 FOREIGN KEY(question_id,question_version) REFERENCES questions(id,version));
CREATE TABLE attempt_evaluations(id TEXT PRIMARY KEY, attempt_id TEXT NOT NULL REFERENCES attempts(id), occurred_at TEXT NOT NULL,
 revision INTEGER NOT NULL, result TEXT NOT NULL, data TEXT NOT NULL, UNIQUE(attempt_id,revision));
CREATE TABLE mistakes(id TEXT PRIMARY KEY, attempt_id TEXT NOT NULL UNIQUE REFERENCES attempts(id), knowledge_id TEXT NOT NULL REFERENCES knowledge_points(id),
 status TEXT NOT NULL, revision INTEGER NOT NULL DEFAULT 1, cause TEXT NOT NULL, data TEXT NOT NULL);
CREATE TABLE mastery_assessments(id TEXT PRIMARY KEY, knowledge_id TEXT NOT NULL REFERENCES knowledge_points(id),
 assessment_id TEXT REFERENCES assessments(id), occurred_at TEXT NOT NULL, status TEXT NOT NULL, data TEXT NOT NULL);
CREATE TABLE review_series(id TEXT PRIMARY KEY, kind TEXT NOT NULL CHECK(kind IN ('knowledge','mistake','comprehensive')),
 knowledge_id TEXT REFERENCES knowledge_points(id), mistake_id TEXT REFERENCES mistakes(id), stage INTEGER NOT NULL DEFAULT 0,
 revision INTEGER NOT NULL DEFAULT 1, data TEXT NOT NULL);
CREATE TABLE review_tasks(id TEXT PRIMARY KEY, series_id TEXT NOT NULL REFERENCES review_series(id), round INTEGER NOT NULL,
 due_date TEXT NOT NULL, suggested_date TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'PENDING', revision INTEGER NOT NULL DEFAULT 1,
 completed_at TEXT, result TEXT, assessment_id TEXT REFERENCES assessments(id), data TEXT NOT NULL, UNIQUE(series_id,round));
CREATE INDEX review_due ON review_tasks(status,due_date);
CREATE TABLE schedule_exceptions(id TEXT PRIMARY KEY, day TEXT NOT NULL, revision INTEGER NOT NULL DEFAULT 1, data TEXT NOT NULL);
CREATE INDEX exception_day ON schedule_exceptions(day);
CREATE TABLE daily_plans(id TEXT NOT NULL, version INTEGER NOT NULL, day TEXT NOT NULL, schedule_hash TEXT NOT NULL,
 data TEXT NOT NULL, PRIMARY KEY(id,version));
CREATE INDEX plan_day ON daily_plans(day);
CREATE TABLE plan_items(id TEXT NOT NULL, plan_id TEXT NOT NULL, plan_version INTEGER NOT NULL, knowledge_id TEXT NOT NULL REFERENCES knowledge_points(id),
 review_id TEXT REFERENCES review_tasks(id), start TEXT NOT NULL, end TEXT NOT NULL, status TEXT NOT NULL,
 data TEXT NOT NULL, PRIMARY KEY(id,plan_version), FOREIGN KEY(plan_id,plan_version) REFERENCES daily_plans(id,version));
CREATE TABLE artifacts(id TEXT PRIMARY KEY, kind TEXT NOT NULL, entity_id TEXT NOT NULL, version INTEGER NOT NULL,
 path TEXT NOT NULL UNIQUE, sha256 TEXT NOT NULL, occurred_at TEXT NOT NULL, status TEXT NOT NULL, data TEXT NOT NULL);
CREATE TRIGGER attempts_no_update BEFORE UPDATE ON attempts BEGIN SELECT RAISE(ABORT,'original answer immutable'); END;
CREATE TRIGGER attempts_no_delete BEFORE DELETE ON attempts BEGIN SELECT RAISE(ABORT,'original answer immutable'); END;
CREATE TRIGGER questions_no_update BEFORE UPDATE ON questions BEGIN SELECT RAISE(ABORT,'question version immutable'); END;
CREATE TRIGGER questions_no_delete BEFORE DELETE ON questions BEGIN SELECT RAISE(ABORT,'question version immutable'); END;
CREATE TRIGGER records_no_update BEFORE UPDATE ON session_records BEGIN SELECT RAISE(ABORT,'session history immutable'); END;
CREATE TRIGGER records_no_delete BEFORE DELETE ON session_records BEGIN SELECT RAISE(ABORT,'session history immutable'); END;
CREATE TRIGGER evaluations_no_update BEFORE UPDATE ON attempt_evaluations BEGIN SELECT RAISE(ABORT,'evaluation history immutable'); END;
CREATE TRIGGER evaluations_no_delete BEFORE DELETE ON attempt_evaluations BEGIN SELECT RAISE(ABORT,'evaluation history immutable'); END;
CREATE TRIGGER mastery_no_update BEFORE UPDATE ON mastery_assessments BEGIN SELECT RAISE(ABORT,'mastery history immutable'); END;
CREATE TRIGGER mastery_no_delete BEFORE DELETE ON mastery_assessments BEGIN SELECT RAISE(ABORT,'mastery history immutable'); END;
CREATE TRIGGER lessons_no_update BEFORE UPDATE ON lesson_tasks BEGIN SELECT RAISE(ABORT,'lesson version immutable'); END;
CREATE TRIGGER plans_no_update BEFORE UPDATE ON daily_plans BEGIN SELECT RAISE(ABORT,'plan version immutable'); END;
CREATE TRIGGER completed_review_no_update BEFORE UPDATE ON review_tasks WHEN OLD.status='COMPLETED' BEGIN SELECT RAISE(ABORT,'review result immutable'); END;
