# 區塊鏈駭客事件案例庫（Solana / Ethereum / BSC）

這個資料夾放三條鏈共用的工具：資料表結構、驗證、建置資料庫、合併研究結果，以及用來大量收集與交叉驗證案例的 workflow 腳本。

| 案例庫 | 狀態 | 說明 |
|---|---|---|
| [`solana_hacks/`](../solana_hacks/README.md) | ✅ 111 起事件 | 見該資料夾 README |
| `ethereum_hacks/` | ⏳ 匯入中 | DeFiHackLabs 的 345 起以太坊攻擊事件；交易所 / 釣魚等其他類型需要網路搜尋額度 |
| `bsc_hacks/` | ⏳ 匯入中 | DeFiHackLabs 的 371 起 BSC 攻擊事件；其他類型同上 |
| `crypto_hacks.db` | ✅ 自動產生 | 三條鏈合併的跨鏈資料庫，主鍵為 `(chain, id)` |

## 結構

```
hack_db/
├── schema.sql              # 每條鏈共用的 SQLite 結構
├── common_lookups.json     # 共用代碼表：專案類型、攻擊類別、追回狀態
├── vuln_patterns/
│   ├── solana.json         # Solana 程式漏洞模式（帳戶驗證缺失等）
│   └── evm.json            # EVM 漏洞模式（重入、權限控管、代理升級、編譯器漏洞等），Ethereum 與 BSC 共用
├── build.py                # 驗證、建置單鏈資料庫、匯出 CSV、建置跨鏈資料庫
├── merge.py                # 把 workflow 結果合併進案例庫
└── workflows/              # 案例收集與交叉驗證的 workflow 腳本

<鏈>_hacks/
├── data/incidents.json     # 事件（唯一需要編輯的資料）
├── data/lookups.json       # 本鏈設定：名稱、事件範圍代碼、漏洞模式家族
├── <鏈>_hacks.db           # 建置好的資料庫
└── exports/                # CSV（UTF-8 BOM，Excel 可直接開啟）
```

每條鏈的 `chain_scope` 分成「只影響本鏈」（`solana_only` / `ethereum_only` / `bsc_only`）與 `multi_chain`。同一起多鏈事件（例如交易所多條鏈的熱錢包同時被盜）可以同時出現在多個案例庫，在 `crypto_hacks.db` 中以 `(chain, id)` 區分。

## 建置與測試

```bash
python -m hack_db                 # 建置所有已有資料的案例庫與 crypto_hacks.db
python -m hack_db ethereum_hacks  # 只建置一條鏈
pytest tests/test_hack_db.py
```

跨鏈比較範例：

```sql
-- sqlite3 crypto_hacks.db
SELECT chain, year, incidents, single_chain_loss_usd FROM v_chain_yearly;
SELECT chain, category_zh, incidents, total_loss_usd FROM v_chain_category;
```

## 從 DeFiHackLabs 匯入 Ethereum / BSC 案例（不需網路搜尋）

[DeFiHackLabs](https://github.com/SunWeb3Sec/DeFiHackLabs) 收錄了數百起 EVM 攻擊事件，每一起都附有可重現的 Foundry PoC。這條路線只需要能連到 `raw.githubusercontent.com`：

```bash
# 下載清單與 PoC、依 PoC 的 fork 設定判斷鏈別，產生 <鏈>_inputs.json
python -m hack_db.defihacklabs /tmp/dhl
```

接著用 `hack_db/workflows/dhl_case_records.js` 產生紀錄：每個 agent 讀 10 起事件的 PoC 程式碼撰寫中文紀錄，另一個 agent 重新讀 PoC 逐筆嘗試推翻（分類、根因、損失金額、來源都要對得上）。參數範例：

```json
{"dataset": "bsc_hacks", "chainName": "BNB Smart Chain (BSC)", "singleScope": "bsc_only",
 "inputPath": "/tmp/dhl/bsc_inputs.json", "count": 371, "part": "bsc"}
```

來源只能從每起事件的 `candidate_sources` 選（DeFiHackLabs PoC、分析文章、攻擊交易），不會出現編造的網址。損失金額：DeFiHackLabs 有寫美元金額就直接使用；只有 ETH / BNB 等代幣數量時，由 agent 以當時的大約價格估算並在 `notes` 說明。

## 重新執行研究 workflow

案例收集用 Claude Code 的 Workflow 功能執行，每個時期一個 workflow，全部並行：

1. **Discover**：依季度搜尋，另加四種橫向搜尋（交易所 / 跨鏈橋、釣魚 / 供應鏈、L1 與編譯器漏洞 / 重大揭露、治理 / 內部人員 / 管理金鑰）。
2. **Dedupe**：依年份合併重複候選事件。
3. **Write**：每個 agent 撰寫 6 筆完整紀錄，附來源。
4. **Verify**：「事實」與「範圍 / 分類」兩個獨立角度的 agent 逐筆嘗試推翻。
5. **Critic**：檢查是否漏掉重大事件，找到就再寫、再驗證，最多兩輪。

> ⚠️ 每個 agent 都需要大量網路搜尋。Claude Code 預設每個 session 只有 200 次搜尋，Ethereum 與 BSC 全部時期大約需要數千次。請先在雲端環境設定中加入環境變數 `CLAUDE_CODE_MAX_WEB_SEARCHES_PER_SESSION`（例如 `5000`），再開新 session 執行。

在新 session 中請 Claude 用 `hack_db/workflows/evm_case_library_era.js` 啟動 workflow，依序帶入下列參數（每一列一個 workflow）：

| 鏈 | era | from | to | periods |
|---|---|---|---|---|
| ethereum | eth-2015-2020 | 2015-07-30 | 2020-12-31 | 2015-2017、2018-2019、2020H1、2020H2 |
| ethereum | eth-2021 | 2021-01-01 | 2021-12-31 | 四個季度 |
| ethereum | eth-2022 | 2022-01-01 | 2022-12-31 | 四個季度 |
| ethereum | eth-2023 | 2023-01-01 | 2023-12-31 | 四個季度 |
| ethereum | eth-2024 | 2024-01-01 | 2024-12-31 | 四個季度 |
| ethereum | eth-2025-2026 | 2025-01-01 | 2026-09-28 | 2025 四個季度、2026Q1–Q3 |
| bsc | bsc-2020-2021 | 2020-09-01 | 2021-12-31 | 2020、2021 四個季度 |
| bsc | bsc-2022 | 2022-01-01 | 2022-12-31 | 四個季度 |
| bsc | bsc-2023-2026 | 2023-01-01 | 2026-09-28 | 2023H1 至 2025H2 每半年、2026 |

參數格式：

```json
{"chain": "ethereum", "chainName": "Ethereum", "era": "eth-2021", "from": "2021-01-01", "to": "2021-12-31",
 "periods": [{"name": "2021Q1", "from": "2021-01-01", "to": "2021-03-31"}, "..."]}
```

BSC 的 `chainName` 用 `"BNB Smart Chain (BSC)"`。

每個 workflow 回傳 `{results, rejected}`。存成 JSON 後合併：

```bash
python -m hack_db.merge ethereum_hacks eth-2021.json eth-2022.json ...   # 先加 --dry-run 檢查
python -m hack_db
```

合併規則：任一角度判定 `drop` 的紀錄會被丟棄；「事實」角度只能修正事實欄位，「範圍」角度只能修正分類欄位；重複的 id 自動加上 `-2`、`-3`。

`solana_verify_and_sweep.js` 用同樣的兩個角度驗證 Solana 案例並搜尋遺漏事件；要對整個 Solana 案例庫重新驗證時，把 `recordsPath` 指向 `solana_hacks/data/incidents.json`，`ids` 帶入要驗證的事件 id。
