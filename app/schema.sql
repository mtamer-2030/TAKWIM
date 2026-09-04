-- مِحَكّ — مخطّط قاعدة البيانات (CLAUDE.md §6)
-- ملفّ SQLite واحد: data/mihakk.db
-- teacher_id مُبقى منذ اليوم الأول رغم أنّ النظام أحادي الأستاذ الآن (§17).

PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

-- أستاذ واحد الآن، لكن العمود موجود لتفادي ترحيل قاعدة فيها سنة معطيات لاحقاً.
CREATE TABLE IF NOT EXISTS teachers (
    id         INTEGER PRIMARY KEY,
    label      TEXT NOT NULL DEFAULT 'الأستاذ',
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS classes (
    id          INTEGER PRIMARY KEY,
    teacher_id  INTEGER NOT NULL DEFAULT 1 REFERENCES teachers(id),
    label       TEXT NOT NULL UNIQUE,          -- TC1، 1BAC2، ...
    level       TEXT NOT NULL,                 -- TC، 1BAC، 2BAC
    school_year TEXT,
    active      INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS students (
    id         INTEGER PRIMARY KEY,
    class_id   INTEGER NOT NULL REFERENCES classes(id),
    roster_id  TEXT NOT NULL UNIQUE,           -- TC1-07 (للعرض، يُولَّد تلقائياً)
    massar_id  TEXT,                            -- رقم مسار الرسمي (هوية ثابتة)
    login_code TEXT NOT NULL UNIQUE,           -- TC1-07-K7 (للدخول)
    full_name  TEXT NOT NULL,
    active     INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS ix_students_class ON students(class_id);
-- رقم مسار فريد حين يوجد (يسمح بغيابه للنمط القديم TC1-07).
CREATE UNIQUE INDEX IF NOT EXISTS ux_students_massar
    ON students(massar_id) WHERE massar_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS assessments (
    id                   INTEGER PRIMARY KEY,
    teacher_id           INTEGER NOT NULL DEFAULT 1 REFERENCES teachers(id),
    title                TEXT NOT NULL,
    kind                 TEXT NOT NULL CHECK (kind IN ('diagnostic','exercise','exam')),
    level                TEXT,
    unit                 TEXT,
    concept              TEXT,
    stimuli_json         TEXT NOT NULL DEFAULT '[]',  -- نصوص الانطلاق المشتركة
    created_at           TEXT NOT NULL DEFAULT (datetime('now')),
    source_assessment_id INTEGER REFERENCES assessments(id)  -- للاستنساخ
);

CREATE TABLE IF NOT EXISTS questions (
    id              INTEGER PRIMARY KEY,
    assessment_id   INTEGER NOT NULL REFERENCES assessments(id) ON DELETE CASCADE,
    position        INTEGER NOT NULL,
    type            TEXT NOT NULL,
    prompt          TEXT NOT NULL,
    stimulus        TEXT,                       -- معرّف نصّ الانطلاق (اختياري)
    payload_json    TEXT NOT NULL DEFAULT '{}',
    indicators_json TEXT NOT NULL DEFAULT '{}',
    max_score       REAL NOT NULL DEFAULT 0,
    competency      TEXT NOT NULL CHECK (competency IN
        ('problematization','conceptualization','argumentation','synthesis','knowledge')),
    auto_scored     INTEGER NOT NULL DEFAULT 0  -- 1 للمقفلة، 0 للمفتوحة أساساً
);
CREATE INDEX IF NOT EXISTS ix_questions_assessment ON questions(assessment_id);

CREATE TABLE IF NOT EXISTS sessions (
    id            INTEGER PRIMARY KEY,
    assessment_id INTEGER NOT NULL REFERENCES assessments(id),
    class_id      INTEGER NOT NULL REFERENCES classes(id),
    status        TEXT NOT NULL DEFAULT 'draft'
                  CHECK (status IN ('draft','open','closed')),
    opened_at     TEXT,
    closed_at     TEXT,
    allow_retry   INTEGER NOT NULL DEFAULT 0,
    max_attempts  INTEGER NOT NULL DEFAULT 1,
    -- 'none' إجباري لكل تقويم kind='exam' (يُفرض في الكود عند الفتح)
    reveal_mode   TEXT NOT NULL DEFAULT 'immediate'
                  CHECK (reveal_mode IN ('immediate','none'))
);
CREATE INDEX IF NOT EXISTS ix_sessions_class ON sessions(class_id);

CREATE TABLE IF NOT EXISTS attendance (
    session_id INTEGER NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    student_id INTEGER NOT NULL REFERENCES students(id),
    present    INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (session_id, student_id)
);

CREATE TABLE IF NOT EXISTS attempts (
    id           INTEGER PRIMARY KEY,
    session_id   INTEGER NOT NULL REFERENCES sessions(id),
    student_id   INTEGER NOT NULL REFERENCES students(id),
    attempt_no   INTEGER NOT NULL DEFAULT 1,
    status       TEXT NOT NULL DEFAULT 'in_progress'
                 CHECK (status IN ('in_progress','submitted','abandoned')),
    device_token TEXT,                          -- قفل الجهاز
    started_at   TEXT NOT NULL DEFAULT (datetime('now')),
    submitted_at TEXT,
    total_ms     INTEGER,
    UNIQUE (session_id, student_id, attempt_no)
);
CREATE INDEX IF NOT EXISTS ix_attempts_session ON attempts(session_id);

CREATE TABLE IF NOT EXISTS answers (
    id                INTEGER PRIMARY KEY,
    attempt_id        INTEGER NOT NULL REFERENCES attempts(id) ON DELETE CASCADE,
    question_id       INTEGER NOT NULL REFERENCES questions(id),
    raw_json          TEXT,                      -- جواب التلميذ الخام
    -- تصحيح يقيني داخل الجلسة (check_rule + المقفلة)
    auto_score        REAL,
    rule_verdicts_json TEXT,
    -- المساعدة المسائية (مقترحة، خارج القاعة)
    ai_score          REAL,
    ai_verdicts_json  TEXT,
    ai_model          TEXT,
    ai_run_at         TEXT,
    -- الأستاذ
    manual_score      REAL,
    teacher_note      TEXT,
    teacher_confirmed INTEGER NOT NULL DEFAULT 0,
    -- التشخيص
    error_tags        TEXT,                      -- رموز الأخطاء من البدائل المختارة
    -- الزمن والمراجعة
    first_seen_at     TEXT,
    answered_at       TEXT,
    ms_spent          INTEGER,
    revision_count    INTEGER NOT NULL DEFAULT 0,
    UNIQUE (attempt_id, question_id)
);
CREATE INDEX IF NOT EXISTS ix_answers_attempt ON answers(attempt_id);
CREATE INDEX IF NOT EXISTS ix_answers_question ON answers(question_id);

CREATE TABLE IF NOT EXISTS identity_events (
    id           INTEGER PRIMARY KEY,
    session_id   INTEGER REFERENCES sessions(id),
    login_code   TEXT,
    event        TEXT NOT NULL CHECK (event IN
        ('claimed','rejected_locked','rejected_absent','rejected_unknown','teacher_unlocked')),
    device_token TEXT,
    ip           TEXT,
    at           TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS ix_identity_session ON identity_events(session_id, at);

CREATE TABLE IF NOT EXISTS question_bank (
    id              INTEGER PRIMARY KEY,
    teacher_id      INTEGER NOT NULL DEFAULT 1 REFERENCES teachers(id),
    level           TEXT,
    unit            TEXT,
    concept         TEXT,
    competency      TEXT,
    type            TEXT,
    prompt          TEXT NOT NULL,
    stimulus_text   TEXT,
    payload_json    TEXT NOT NULL DEFAULT '{}',
    indicators_json TEXT NOT NULL DEFAULT '{}',
    max_score       REAL NOT NULL DEFAULT 0,
    tags            TEXT,
    times_used      INTEGER NOT NULL DEFAULT 0
);

-- لائحة رموز الأخطاء المشخِّصة: مفتوحة وقابلة للتوسيع من لوحة الأستاذ (§11).
CREATE TABLE IF NOT EXISTS error_codes (
    code  TEXT PRIMARY KEY,
    label TEXT NOT NULL
);
