-- Solana 生態系資安事件資料庫 (SQLite)
-- 由 build_db.py 讀取 data/*.json 後建立；請勿手動修改產生出的 .db 檔。

PRAGMA foreign_keys = ON;

-- ---------------------------------------------------------------------------
-- 代碼表 (lookup tables)
-- ---------------------------------------------------------------------------

CREATE TABLE project_types (
    code     TEXT PRIMARY KEY,
    name_zh  TEXT NOT NULL,
    name_en  TEXT NOT NULL
);

CREATE TABLE categories (
    code            TEXT PRIMARY KEY,
    name_zh         TEXT NOT NULL,
    name_en         TEXT NOT NULL,
    description_zh  TEXT NOT NULL
);

CREATE TABLE recovery_statuses (
    code     TEXT PRIMARY KEY,
    name_zh  TEXT NOT NULL,
    name_en  TEXT NOT NULL
);

-- 邏輯漏洞模式（只套用於 smart_contract_bug 與 protocol_vulnerability 類別）
CREATE TABLE vuln_patterns (
    code            TEXT PRIMARY KEY,
    name_zh         TEXT NOT NULL,
    name_en         TEXT NOT NULL,
    description_zh  TEXT NOT NULL,
    defense_zh      TEXT NOT NULL   -- 開發者防禦建議
);

-- ---------------------------------------------------------------------------
-- 事件主表
-- ---------------------------------------------------------------------------

CREATE TABLE incidents (
    id               TEXT PRIMARY KEY,               -- 例如 wormhole-2022
    date             TEXT NOT NULL                   -- 事件發生（或揭露）日期，UTC
                     CHECK (date GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]'),
    project          TEXT NOT NULL,
    project_type     TEXT NOT NULL REFERENCES project_types (code),
    category         TEXT NOT NULL REFERENCES categories (code),
    vuln_pattern     TEXT REFERENCES vuln_patterns (code),  -- 僅邏輯漏洞類事件
    attack_vector    TEXT NOT NULL,                  -- 簡短英文描述攻擊手法
    loss_usd         REAL CHECK (loss_usd IS NULL OR loss_usd >= 0),
    assets_stolen    TEXT,
    recovered_usd    REAL CHECK (recovered_usd IS NULL OR recovered_usd >= 0),
    recovery_status  TEXT NOT NULL REFERENCES recovery_statuses (code),
    attribution      TEXT NOT NULL,
    chain_scope      TEXT NOT NULL CHECK (chain_scope IN ('solana_only', 'multi_chain')),
    summary_zh       TEXT NOT NULL,
    root_cause_zh    TEXT NOT NULL,
    aftermath_zh     TEXT NOT NULL,
    confidence       TEXT NOT NULL CHECK (confidence IN ('high', 'medium', 'low')),
    notes            TEXT
);

CREATE INDEX idx_incidents_date         ON incidents (date);
CREATE INDEX idx_incidents_category     ON incidents (category);
CREATE INDEX idx_incidents_project_type ON incidents (project_type);

-- ---------------------------------------------------------------------------
-- 參考來源（一個事件可有多個來源）
-- ---------------------------------------------------------------------------

CREATE TABLE sources (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    incident_id  TEXT NOT NULL REFERENCES incidents (id) ON DELETE CASCADE,
    title        TEXT NOT NULL,
    publisher    TEXT NOT NULL,
    url          TEXT NOT NULL CHECK (url LIKE 'https://%' OR url LIKE 'http://%'),
    UNIQUE (incident_id, url)
);

CREATE INDEX idx_sources_incident ON sources (incident_id);

-- ---------------------------------------------------------------------------
-- 檢視表 (views)
-- ---------------------------------------------------------------------------

-- 事件明細（含中文代碼名稱、年份、淨損失）
CREATE VIEW v_incidents AS
SELECT
    i.id,
    i.date,
    CAST(substr(i.date, 1, 4) AS INTEGER)                    AS year,
    i.project,
    i.project_type,
    pt.name_zh                                               AS project_type_zh,
    i.category,
    c.name_zh                                                AS category_zh,
    i.vuln_pattern,
    vp.name_zh                                               AS vuln_pattern_zh,
    i.attack_vector,
    i.loss_usd,
    i.recovered_usd,
    CASE WHEN i.loss_usd IS NULL THEN NULL
         ELSE MAX(i.loss_usd - COALESCE(i.recovered_usd, 0), 0)
    END                                                      AS net_loss_usd,
    i.recovery_status,
    rs.name_zh                                               AS recovery_status_zh,
    i.assets_stolen,
    i.attribution,
    i.chain_scope,
    i.summary_zh,
    i.root_cause_zh,
    i.aftermath_zh,
    i.confidence,
    i.notes,
    (SELECT COUNT(*) FROM sources s WHERE s.incident_id = i.id) AS source_count
FROM incidents i
JOIN project_types     pt ON pt.code = i.project_type
JOIN categories        c  ON c.code  = i.category
JOIN recovery_statuses rs ON rs.code = i.recovery_status
LEFT JOIN vuln_patterns vp ON vp.code = i.vuln_pattern;

-- 年度統計
-- total_loss_usd 含多鏈事件的全部損失；solana_only_loss_usd 只計純 Solana 事件。
CREATE VIEW v_yearly_summary AS
SELECT
    year,
    COUNT(*)                                        AS incidents,
    SUM(COALESCE(loss_usd, 0))                      AS total_loss_usd,
    SUM(CASE WHEN chain_scope = 'solana_only'
             THEN COALESCE(loss_usd, 0) ELSE 0 END) AS solana_only_loss_usd,
    SUM(COALESCE(recovered_usd, 0))                 AS total_recovered_usd,
    MAX(loss_usd)                                   AS largest_loss_usd
FROM v_incidents
GROUP BY year
ORDER BY year;

-- 攻擊類別統計
CREATE VIEW v_category_summary AS
SELECT
    category,
    category_zh,
    COUNT(*)                                        AS incidents,
    SUM(COALESCE(loss_usd, 0))                      AS total_loss_usd,
    SUM(CASE WHEN chain_scope = 'solana_only'
             THEN COALESCE(loss_usd, 0) ELSE 0 END) AS solana_only_loss_usd
FROM v_incidents
GROUP BY category, category_zh
ORDER BY total_loss_usd DESC;

-- 專案類型統計
CREATE VIEW v_project_type_summary AS
SELECT
    project_type,
    project_type_zh,
    COUNT(*)                                        AS incidents,
    SUM(COALESCE(loss_usd, 0))                      AS total_loss_usd,
    SUM(CASE WHEN chain_scope = 'solana_only'
             THEN COALESCE(loss_usd, 0) ELSE 0 END) AS solana_only_loss_usd
FROM v_incidents
GROUP BY project_type, project_type_zh
ORDER BY total_loss_usd DESC;

-- 邏輯漏洞模式統計（含防禦建議）
CREATE VIEW v_vuln_pattern_summary AS
SELECT
    vp.code                         AS vuln_pattern,
    vp.name_zh                      AS vuln_pattern_zh,
    COUNT(i.id)                     AS incidents,
    SUM(COALESCE(i.loss_usd, 0))    AS total_loss_usd,
    group_concat(i.project, '、')   AS projects,
    vp.defense_zh
FROM vuln_patterns vp
LEFT JOIN incidents i ON i.vuln_pattern = vp.code
GROUP BY vp.code, vp.name_zh, vp.defense_zh
ORDER BY total_loss_usd DESC, incidents DESC;
