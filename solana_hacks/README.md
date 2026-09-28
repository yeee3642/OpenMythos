# Solana 生態系駭客攻擊 / 資安事件資料庫

這份資料庫收錄 Solana 生態系自 2021 年至 2026 年 9 月、有公開資料可查的重大資安事件。範圍包括 DeFi 合約漏洞、預言機操縱、私鑰外洩、內部人員作案、供應鏈攻擊、釣魚 / 社交工程、治理攻擊、第三方服務遭入侵、中心化交易所的 Solana 熱錢包被盜，以及 Solana L1 協議層漏洞。資料以 JSON 維護，再用建置腳本轉成 **SQLite** 資料庫與 **CSV**，可以用 SQL、Excel 或 Python 查詢。

> ⚠️ 本資料僅供資安研究與教育用途。金額以事件發生當時的美元價值計算，不同來源的數字可能有出入，差異記在 `notes` 欄位。每筆事件都附有公開來源連結，請以原始來源為準。

<!-- STATS -->

## 檔案結構

```
solana_hacks/
├── data/
│   ├── incidents.json     # 事件原始資料（唯一需要手動編輯的檔案）
│   └── lookups.json       # 代碼表：攻擊類別、專案類型、追回狀態
├── schema.sql             # SQLite 資料表與檢視表定義
├── build_db.py            # 驗證資料 → 建立 solana_hacks.db → 匯出 CSV
├── queries.sql            # 常用查詢範例
├── solana_hacks.db        # 建置好的 SQLite 資料庫
└── exports/               # CSV 匯出（UTF-8 BOM，Excel 可直接開啟中文）
    ├── incidents.csv
    ├── sources.csv
    ├── yearly_summary.csv
    └── category_summary.csv
```

## 使用方式

```bash
# 重新建置資料庫與 CSV（只需 Python 3.10+ 標準函式庫）
python solana_hacks/build_db.py

# 執行資料驗證測試
pytest tests/test_solana_hacks.py

# 用 sqlite3 CLI 執行範例查詢
sqlite3 solana_hacks/solana_hacks.db < solana_hacks/queries.sql
```

Python 查詢範例：

```python
import sqlite3

conn = sqlite3.connect("solana_hacks/solana_hacks.db")
for row in conn.execute(
    "SELECT date, project, category_zh, loss_usd FROM v_incidents"
    " ORDER BY loss_usd DESC LIMIT 5"
):
    print(row)
```

## 資料表結構

### `incidents`（事件主表）

| 欄位 | 說明 |
|---|---|
| `id` | 事件代碼，例如 `wormhole-2022` |
| `date` | 事件發生日期（UTC）；協議層漏洞則為揭露或修補日期 |
| `project` | 受害專案或元件名稱 |
| `project_type` | 專案類型代碼 → `project_types` |
| `category` | 攻擊類別代碼 → `categories` |
| `attack_vector` | 攻擊手法（英文簡述） |
| `loss_usd` | 事件當時的美元損失；`NULL` 表示金額不明，`0` 表示確認無損失 |
| `assets_stolen` | 被盜資產明細 |
| `recovered_usd` | 從攻擊者手上追回、凍結或攻擊者歸還的金額。**不含**項目方或投資方另行賠付給使用者的部分 |
| `recovery_status` | 追回狀態代碼 → `recovery_statuses` |
| `attribution` | 攻擊者身分或歸因（例如 Lazarus Group），不明時為 `Unknown` |
| `chain_scope` | `solana_only`（只有 Solana）或 `multi_chain`（多鏈事件，其中包含 Solana 資產） |
| `summary_zh` / `root_cause_zh` / `aftermath_zh` | 事件摘要、技術根因、後續處理（繁體中文） |
| `confidence` | 資料可信度：`high` / `medium` / `low` |
| `notes` | 金額範圍、來源差異等備註 |

### `sources`（參考來源）

一筆事件可以有多個來源，欄位為 `incident_id`、`publisher`、`title`、`url`。

### 代碼表

- `categories`：攻擊類別（中英文名稱與說明）
- `project_types`：專案類型
- `recovery_statuses`：資金追回狀態

### 檢視表

| 檢視表 | 用途 |
|---|---|
| `v_incidents` | 事件明細，附上中文類別名稱、年份、淨損失 (`net_loss_usd`) 與來源數 |
| `v_yearly_summary` | 年度事件數、總損失、總追回金額、單一最大損失 |
| `v_category_summary` | 依攻擊類別統計 |
| `v_project_type_summary` | 依專案類型統計 |

## 收錄原則

- **收錄**：Solana 鏈上協議遭攻擊；Solana 錢包、交易機器人、SDK 遭入侵；中心化交易所或託管商的 **Solana 資產**被盜；Solana L1 核心漏洞（即使沒有造成損失）；有明確損失紀錄的 Solana 釣魚或供應鏈攻擊。
- **不收錄**：純粹的迷因幣 rug pull 或內線拋售爭議、網路壅塞 / 停機等非資安事件、損失主要發生在其他鏈的事件。
- `operational_error`（操作失誤）不屬於駭客攻擊，但因為有資金損失，所以一併收錄供參考，統計時可以自行排除。

## 新增或修正事件

1. 編輯 `data/incidents.json`，依日期排序新增一筆物件（欄位同上表，並附至少一個 `sources`）。
2. 執行 `python solana_hacks/build_db.py`，建置時會自動驗證欄位、代碼與日期排序。
3. 執行 `pytest tests/test_solana_hacks.py`。
