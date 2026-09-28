-- Solana 資安事件資料庫：常用查詢範例
-- 執行方式：sqlite3 solana_hacks/solana_hacks.db < solana_hacks/queries.sql
.headers on
.mode column

-- 1. 純 Solana 事件損失金額前 10 名
SELECT date, project, category_zh, printf('$%,d', loss_usd) AS loss
  FROM v_incidents
 WHERE loss_usd IS NOT NULL AND chain_scope = 'solana_only'
 ORDER BY loss_usd DESC
 LIMIT 10;

-- 1b. 含多鏈事件的損失金額前 10 名（多鏈事件的金額包含非 Solana 資產）
SELECT date, project, chain_scope, printf('$%,d', loss_usd) AS loss
  FROM v_incidents
 WHERE loss_usd IS NOT NULL
 ORDER BY loss_usd DESC
 LIMIT 10;

-- 2. 年度統計（事件數、總損失、純 Solana 損失、追回金額）
SELECT year, incidents,
       printf('$%,d', total_loss_usd)       AS total_loss,
       printf('$%,d', solana_only_loss_usd) AS solana_only_loss,
       printf('$%,d', total_recovered_usd)  AS total_recovered
  FROM v_yearly_summary;

-- 3. 依攻擊類別統計
SELECT category_zh, incidents,
       printf('$%,d', total_loss_usd)       AS total_loss,
       printf('$%,d', solana_only_loss_usd) AS solana_only_loss
  FROM v_category_summary;

-- 4. 依專案類型統計
SELECT project_type_zh, incidents,
       printf('$%,d', total_loss_usd)       AS total_loss,
       printf('$%,d', solana_only_loss_usd) AS solana_only_loss
  FROM v_project_type_summary;

-- 5. 私鑰外洩、內部人員、釣魚、供應鏈等「非合約漏洞」造成的損失
SELECT date, project, category_zh, printf('$%,d', loss_usd) AS loss
  FROM v_incidents
 WHERE category IN ('private_key_compromise', 'insider_threat',
                    'phishing_social_engineering', 'supply_chain',
                    'third_party_compromise', 'frontend_account_compromise')
 ORDER BY date;

-- 6. 已知歸因於北韓 (Lazarus / DPRK) 的事件
SELECT date, project, printf('$%,d', loss_usd) AS loss, attribution
  FROM v_incidents
 WHERE attribution LIKE '%Lazarus%'
    OR attribution LIKE '%North Korea%'
    OR attribution LIKE '%DPRK%'
 ORDER BY date;

-- 7. 攻擊者歸還或追回資金的事件
SELECT date, project, recovery_status_zh,
       printf('$%,d', loss_usd)      AS loss,
       printf('$%,d', recovered_usd) AS recovered
  FROM v_incidents
 WHERE recovered_usd > 0
 ORDER BY recovered_usd DESC;

-- 8. L1 協議層漏洞（多半未造成損失）
SELECT date, project, attack_vector, summary_zh
  FROM v_incidents
 WHERE category = 'protocol_vulnerability'
 ORDER BY date;

-- 9. 關鍵字搜尋（例：預言機）
SELECT date, project, summary_zh
  FROM v_incidents
 WHERE summary_zh LIKE '%預言機%' OR root_cause_zh LIKE '%預言機%';

-- 10. 某事件的所有參考來源
SELECT publisher, title, url
  FROM sources
 WHERE incident_id = 'wormhole-2022';

-- 11. 邏輯漏洞：依漏洞模式統計（含防禦建議）
SELECT vuln_pattern_zh, incidents, printf('$%,d', total_loss_usd) AS total_loss,
       defense_zh
  FROM v_vuln_pattern_summary;

-- 12. 邏輯漏洞：全部事件明細，依漏洞模式分組
SELECT vuln_pattern_zh, date, project, printf('$%,d', loss_usd) AS loss,
       root_cause_zh
  FROM v_incidents
 WHERE vuln_pattern IS NOT NULL
 ORDER BY vuln_pattern, date;
