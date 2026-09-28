# Solana 生態系駭客攻擊 / 資安事件資料庫

這份資料庫收錄 Solana 生態系自 2021 年至 2026 年 9 月、有公開資料可查的重大資安事件。範圍包括 DeFi 合約漏洞、預言機操縱、私鑰外洩、內部人員作案、供應鏈攻擊、釣魚 / 社交工程、治理攻擊、第三方服務遭入侵、中心化交易所的 Solana 熱錢包被盜，以及 Solana L1 協議層漏洞。資料以 JSON 維護，再用建置腳本轉成 **SQLite** 資料庫與 **CSV**，可以用 SQL、Excel 或 Python 查詢。

> 下一步：[solana_monitor](../solana_monitor/README.md) 會用這個案例庫定出過濾規則，收集 Solana 新流動池的資料並篩選。

> ⚠️ 本資料僅供資安研究與教育用途。金額以事件發生當時的美元價值計算，不同來源的數字可能有出入，差異記在 `notes` 欄位。每筆事件都附有公開來源連結，請以原始來源為準。

## 資料概況（截至 2026-09-28）

- **69 起事件**：時間範圍 2021-08-19 至 2026-09-17，共 202 筆參考來源。
- **純 Solana 事件 55 起**，損失合計約 **10.28 億美元**。若加上 14 起多鏈事件（例如 FTX、Phemex、Nobitex 等中心化交易所遭駭，其中包含 Solana 資產），合計約 17.36 億美元。多鏈事件的金額包含其他鏈的資產，**不能直接當成 Solana 的損失**。
- **資料可信度**：high 40 筆、medium 27 筆、low 2 筆。

### 年度統計

| 年份 | 事件數 | 總損失（含多鏈） | 純 Solana 損失 | 從攻擊者追回 |
|---|---:|---:|---:|---:|
| 2021 | 7 | 197 萬美元 | 197 萬美元 | 0 |
| 2022 | 13 | 9.93 億美元 | 5.16 億美元 | 2.14 億美元 |
| 2023 | 7 | 682 萬美元 | 575 萬美元 | 0 |
| 2024 | 9 | 2,457 萬美元 | 2,457 萬美元 | 0 |
| 2025 | 19 | 3.47 億美元 | 1.26 億美元 | 928 萬美元 |
| 2026（至 9 月） | 14 | 3.63 億美元 | 3.53 億美元 | 470 萬美元 |

### 純 Solana 事件損失前十名

| 日期 | 事件 | 類別 | 損失 |
|---|---|---|---:|
| 2022-02-02 | Wormhole 跨鏈橋 | 合約漏洞（簽名驗證繞過） | 3.26 億美元 |
| 2026-04-01 | Drift Protocol | 社交工程（多簽成員預簽 durable nonce 交易） | 2.85 億美元 |
| 2022-10-11 | Mango Markets | 預言機 / 價格操縱 | 1.14 億美元 |
| 2022-03-23 | Cashio | 合約漏洞（無限鑄幣） | 5,280 萬美元 |
| 2025-07-18 | CoinDCX（Solana 營運錢包） | 社交工程（假求職植入惡意程式） | 4,420 萬美元 |
| 2025-09-08 | SwissBorg / Kiln | 第三方質押 API 遭入侵 | 4,100 萬美元 |
| 2025-11-26 | Upbit（Solana 熱錢包） | 私鑰外洩 | 3,000 萬美元 |
| 2026-01-31 | Step Finance | 私鑰外洩（主管裝置遭入侵） | 2,730 萬美元 |
| 2024-11-16 | DEXX | 私鑰外洩（託管私鑰） | 2,100 萬美元 |
| 2026-07-06 | BonkDAO | 治理攻擊 | 2,000 萬美元 |

### 觀察

- **2025 年以後，大額損失的主因從合約漏洞轉向鏈下攻擊**：私鑰外洩、社交工程、第三方服務遭入侵（Drift、CoinDCX、SwissBorg、Upbit、Step Finance）。
- **L1 協議層漏洞共 7 起**（rBPF、ELF 對齊、ZK ElGamal 兩次、gossip / 投票處理、QUIC、SIMD-0376），全部在遭利用前修補，沒有造成資金損失。
- **至少 6 起事件被歸因或疑似歸因於北韓**，包括 Drift、Upbit、BitoPro、Phemex、Solareum。可用 `queries.sql` 第 6 個查詢列出。
- 供應鏈攻擊（惡意 npm / crates / GitHub 套件）次數多，但公開的損失金額很少，實際損失可能被低估。

## 邏輯漏洞分析

共 21 起事件的根因是程式邏輯缺陷，合計損失約 **4.01 億美元**：`smart_contract_bug` 14 起，`protocol_vulnerability` 7 起。每起事件都在 `vuln_pattern` 欄位標記漏洞模式，`vuln_patterns` 代碼表附有開發者防禦建議。

| 漏洞模式 | 事件數 | 損失 | 事件 |
|---|---:|---:|---|
| 帳戶驗證缺失 | 6 | 3.91 億美元 | Wormhole、Cashio、Crema、Texture、Raydium 舊版 AMM V3、Solend (2021) |
| 細節未公開 | 2 | 894 萬美元 | Cypher、NoOnes |
| 競態條件 | 1 | 83 萬美元 | Aurory SyncSpace |
| 重複初始化 | 1 | 15.5 萬美元 | Metaplex Candy Machine v1 |
| 鏈下系統邏輯缺陷 | 2 | 1.5 萬美元 | Magic Eden、io.net |
| 密碼學驗證缺陷 | 3 | 0 | ZK ElGamal（2025 年 4 月、6 月）、SIMD-0376 |
| 輸入處理不當（節點崩潰 / 阻斷服務） | 3 | 0 | Agave ELF 對齊、gossip / 投票處理、Quinn QUIC |
| 數值計算錯誤（捨入 / 溢位） | 2 | 0 | SPL token-lending 捨入、rBPF 整數溢位 |
| 控制流程錯誤 | 1 | 0 | Jet Protocol |

### 重點

- **帳戶驗證缺失是 Solana 損失最大的邏輯漏洞**：這 6 起事件約佔邏輯漏洞總損失的 97.5%。Solana 程式用到的帳戶全部由呼叫者傳入，只要漏掉一個 owner、mint 或位址檢查，攻擊者就能用偽造帳戶冒充合法帳戶。Wormhole（偽造 Instructions sysvar）、Cashio（假抵押品帳戶鏈）、Crema（假 tick 帳戶）、Texture（未檢查代幣帳戶擁有者）都屬於這一類。
- **同類錯誤反覆發生**：從 2021 年的 Solend 到 2026 年的 Raydium 舊版 AMM V3，帳戶驗證缺失橫跨五年一再出現。Raydium 案也顯示，已經淘汰但沒有關閉的舊程式仍然是攻擊面。
- **L1 層的邏輯漏洞都在遭利用前修補**：密碼學驗證缺陷（ZK ElGamal、SIMD-0376）一旦被利用，可以無限鑄造機密代幣或偽造簽章，是潛在影響最大的一類，但全部由白帽研究者揭露。
- **審計不等於安全**：Texture 經過審計，仍因缺少 owner 檢查而被攻擊；Cashio 則完全沒有審計。

各模式的防禦建議可用 `queries.sql` 第 11 個查詢列出，第 12 個查詢列出全部邏輯漏洞事件的根因。

## 檔案結構

```
solana_hacks/
├── data/
│   ├── incidents.json     # 事件原始資料（唯一需要手動編輯的檔案）
│   └── lookups.json       # 本鏈設定：事件範圍代碼、漏洞模式家族（共用代碼表在 hack_db/）
├── build_db.py            # 相容舊用法，等同 python -m hack_db solana_hacks
├── queries.sql            # 常用查詢範例
├── solana_hacks.db        # 建置好的 SQLite 資料庫
└── exports/               # CSV 匯出（UTF-8 BOM，Excel 可直接開啟中文）
    ├── incidents.csv
    ├── sources.csv
    ├── yearly_summary.csv
    ├── category_summary.csv
    └── vuln_pattern_summary.csv
```

## 使用方式

```bash
# 重新建置資料庫與 CSV（只需 Python 3.10+ 標準函式庫）
python -m hack_db solana_hacks        # 或 python -m hack_db 建置所有鏈

# 執行資料驗證測試
pytest tests/test_hack_db.py

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
| `vuln_pattern` | 邏輯漏洞模式代碼 → `vuln_patterns`；`category` 為 `smart_contract_bug` 或 `protocol_vulnerability` 時必填，其他類別為 `NULL` |
| `attack_vector` | 攻擊手法（英文簡述） |
| `loss_usd` | 事件當時的美元損失；`NULL` 表示金額不明，`0` 表示確認無損失 |
| `assets_stolen` | 被盜資產明細 |
| `recovered_usd` | 從攻擊者手上追回、凍結或攻擊者歸還的金額。**不含**項目方或投資方另行賠付給使用者的部分 |
| `recovery_status` | 追回狀態代碼 → `recovery_statuses` |
| `attribution` | 攻擊者身分或歸因（例如 Lazarus Group），不明時為 `Unknown` |
| `chain_scope` | `solana_only`：攻擊發生在 Solana 上（攻擊者事後跨鏈洗錢不影響分類）；`multi_chain`：多鏈事件，Solana 只是其中一部分，`loss_usd` 為全部鏈的合計 |
| `summary_zh` / `root_cause_zh` / `aftermath_zh` | 事件摘要、技術根因、後續處理（繁體中文） |
| `confidence` | 資料可信度：`high` / `medium` / `low` |
| `notes` | 金額範圍、來源差異等備註 |

### `sources`（參考來源）

一筆事件可以有多個來源，欄位為 `incident_id`、`publisher`、`title`、`url`。

### 代碼表

- `categories`：攻擊類別（中英文名稱與說明）
- `project_types`：專案類型
- `recovery_statuses`：資金追回狀態
- `chain_scopes`：事件範圍（`solana_only` / `multi_chain`）
- `vuln_patterns`：邏輯漏洞模式（中英文名稱、說明與開發者防禦建議 `defense_zh`）
- `dataset_info`：案例庫中繼資料

攻擊類別、專案類型、追回狀態三張表與 Ethereum、BSC 案例庫共用，定義在 `hack_db/common_lookups.json`；Solana 的漏洞模式在 `hack_db/vuln_patterns/solana.json`。

### 檢視表

| 檢視表 | 用途 |
|---|---|
| `v_incidents` | 事件明細，附上中文類別名稱、年份、淨損失 (`net_loss_usd`) 與來源數 |
| `v_yearly_summary` | 年度事件數、總損失、單鏈損失 (`single_chain_loss_usd`，不含多鏈事件)、總追回金額、單一最大損失 |
| `v_category_summary` | 依攻擊類別統計 |
| `v_project_type_summary` | 依專案類型統計 |
| `v_vuln_pattern_summary` | 依邏輯漏洞模式統計，附事件清單與防禦建議 |

## 收錄原則

- **收錄**：Solana 鏈上協議遭攻擊；Solana 錢包、交易機器人、SDK 遭入侵；中心化交易所或託管商的 **Solana 資產**被盜；Solana L1 核心漏洞（即使沒有造成損失）；有明確損失紀錄的 Solana 釣魚或供應鏈攻擊。
- **不收錄**：純粹的迷因幣 rug pull 或內線拋售爭議、網路壅塞 / 停機等非資安事件、損失主要發生在其他鏈的事件。
- `operational_error`（操作失誤）不屬於駭客攻擊，但因為有資金損失，所以一併收錄供參考，統計時可以自行排除。

## 資料來源與限制

- 事件由網路搜尋整理而成，來源包括專案官方事後報告、Anza / Solana 基金會公告、Chainalysis、TRM Labs、Elliptic、SlowMist、Halborn、CertiK、Mandiant、Socket、美國司法部，以及 CoinDesk、The Block、Decrypt 等媒體，並以 Helius 的 Solana 攻擊事件整理交叉比對。
- 所有連結都取自搜尋結果，建置時沒有逐一重新連線確認；少數連結可能已失效或改址。
- 2026 年的事件較新，部分只有二手報導（`confidence` 為 `medium` 或 `low`），金額與根因可能在官方事後報告發布後變動。
- 已刻意排除：Audius 治理攻擊（發生在以太坊合約）、Banana Gun（被盜的是以太坊上的 ETH）、Trust Wallet 擴充功能事件（Solana 資產僅約 431 美元），以及疑似發生在 BNB Chain 的 SVT Token 事件。

## 新增或修正事件

1. 編輯 `data/incidents.json`，依日期排序新增一筆物件（欄位同上表，並附至少一個 `sources`）。
2. 執行 `python -m hack_db solana_hacks`，建置時會自動驗證欄位、代碼與日期排序。
3. 執行 `pytest tests/test_hack_db.py`。
