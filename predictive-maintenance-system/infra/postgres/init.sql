-- PostgreSQL 초기화 스크립트
-- docker-compose 첫 실행 시 자동 실행됨

-- 확장 모듈
CREATE EXTENSION IF NOT EXISTS "pgcrypto";
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- 지점 정보
CREATE TABLE IF NOT EXISTS sites (
    site_id     VARCHAR PRIMARY KEY,
    site_name   VARCHAR NOT NULL,
    location    VARCHAR,
    created_at  TIMESTAMPTZ DEFAULT NOW()
);

-- 기계 정보
CREATE TABLE IF NOT EXISTS machines (
    machine_id   VARCHAR PRIMARY KEY,
    site_id      VARCHAR REFERENCES sites(site_id),
    machine_name VARCHAR,
    status       VARCHAR DEFAULT 'NORMAL',
    updated_at   TIMESTAMPTZ DEFAULT NOW()
);

-- 관리자 정보
CREATE TABLE IF NOT EXISTS managers (
    manager_id  SERIAL PRIMARY KEY,
    site_id     VARCHAR REFERENCES sites(site_id),
    name        VARCHAR NOT NULL,
    fcm_token   VARCHAR,
    apns_token  VARCHAR
);

-- 이상 이벤트
CREATE TABLE IF NOT EXISTS anomaly_events (
    event_id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    site_id              VARCHAR,
    machine_id           VARCHAR REFERENCES machines(machine_id),
    subsystem            VARCHAR,
    detected_at          TIMESTAMPTZ NOT NULL,
    reconstruction_error FLOAT,
    anomaly_score        FLOAT,
    severity             VARCHAR,
    feature_snapshot     JSONB DEFAULT '{}',
    is_resolved          BOOLEAN DEFAULT FALSE
);

-- Fault Diagnosis 결과
CREATE TABLE IF NOT EXISTS fault_diagnosis_results (
    result_id    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    event_id     UUID REFERENCES anomaly_events(event_id) UNIQUE,
    model_name   VARCHAR,
    diagnosed_at TIMESTAMPTZ NOT NULL,
    root_causes  JSONB DEFAULT '[]',
    confidence   FLOAT
);

-- 알림 이력
CREATE TABLE IF NOT EXISTS notification_history (
    notif_id   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    event_id   UUID REFERENCES anomaly_events(event_id),
    manager_id INT REFERENCES managers(manager_id),
    sent_at    TIMESTAMPTZ NOT NULL,
    channel    VARCHAR NOT NULL,
    status     VARCHAR NOT NULL,
    message    VARCHAR(500)
);

-- 인덱스
CREATE INDEX IF NOT EXISTS idx_anomaly_events_site_id    ON anomaly_events(site_id);
CREATE INDEX IF NOT EXISTS idx_anomaly_events_machine_id ON anomaly_events(machine_id);
CREATE INDEX IF NOT EXISTS idx_anomaly_events_detected   ON anomaly_events(detected_at DESC);
CREATE INDEX IF NOT EXISTS idx_anomaly_events_resolved   ON anomaly_events(is_resolved);
CREATE INDEX IF NOT EXISTS idx_notif_history_event_id    ON notification_history(event_id);

-- 샘플 데이터 (개발용)
INSERT INTO sites (site_id, site_name, location) VALUES
    ('site-A', '공장 A (서울)',  '서울특별시 강서구'),
    ('site-B', '공장 B (부산)',  '부산광역시 사상구'),
    ('site-C', '공장 C (대구)',  '대구광역시 달성군')
ON CONFLICT DO NOTHING;

INSERT INTO machines (machine_id, site_id, machine_name, status) VALUES
    ('cnc-001', 'site-A', 'CNC 선반 #1', 'NORMAL'),
    ('cnc-002', 'site-A', 'CNC 선반 #2', 'NORMAL'),
    ('cnc-003', 'site-B', 'CNC 선반 #1', 'NORMAL'),
    ('cnc-004', 'site-C', 'CNC 선반 #1', 'NORMAL')
ON CONFLICT DO NOTHING;

INSERT INTO managers (site_id, name) VALUES
    (NULL,     '중앙관리자'),
    ('site-A', '공장A 관리자'),
    ('site-B', '공장B 관리자'),
    ('site-C', '공장C 관리자')
ON CONFLICT DO NOTHING;
