# Solana 流動池資料收集與過濾（solana_monitor）

這是整個流程的第二步。第一步是[案例庫](../solana_hacks/README.md)，整理了 Solana 上曾被駭的事件並分類。這一步用那些案例定出過濾規則，再收集 Solana 新流動池的資料，**過濾掉危險或沒有價值的池子，只留下值得關注的**，每個決定都附上理由存進 SQLite。

```
案例庫 (solana_hacks.db)
   │  歸納成規則，每條規則標注對應案例
   ▼
資料來源 ──► 第 0 階段：建池交易 ──► 第 1 階段：市場資料 ──► 第 2 階段：鏈上 mint ──► 第 3 階段：案例庫 ──► market.db
DexScreener    初始流動性、建池者     免費，先丟雜訊        只查通過前面階段的池子                      保留 / 丟棄＋理由
自己監聽新池   （只限監聽）                                                                            AVE 風險報告存檔
AVE
```

## 目標鏈：Solana

案例庫已經收錄 Solana 的駭客事件，所以資料收集也以 Solana 為主。如果你熟悉 PancakeSwap（BSC），可以這樣對照：

| PancakeSwap（BSC） | Solana 對應 | 本工具的監聽標記 |
|---|---|---|
| V2 `PairCreated` 事件 | Raydium AMM v4 `initialize2` | `raydium_amm_v4` |
| V2 類型（恆定乘積） | Raydium CPMM、PumpSwap | `raydium_cpmm`、`pumpswap` |
| V3 `PoolCreated`（集中流動性） | Raydium CLMM、Orca Whirlpool、Meteora DLMM | `raydium_clmm`、`orca_whirlpool`、`meteora_dlmm` |
| （無） | Pump.fun 聯合曲線發幣 | `pumpfun` |

EVM 用 `eth_subscribe logs` 監聽事件；Solana 則用 `logsSubscribe`，依程式 ID 訂閱，再從 `Program log:` 找出建池指令。

## 資料來源

| 來源 | 狀態 | 優點 | 缺點 |
|---|---|---|---|
| **DexScreener API** | ✅ 已實作（`scan`） | 免金鑰、有流動性、交易次數、FDV | 有延遲與速率限制；只看得到已被收錄的池子 |
| **自己監聽（Solana RPC WebSocket）** | ✅ 已實作（`listen`） | 最即時，池子一建立就能看到 | 剛建立的池子沒有市場數據；公共 RPC 限流嚴重，建議用 Helius、Triton、QuickNode 等付費節點 |
| **Solana RPC（mint 檢查）** | ✅ 已實作 | 直接讀鏈上權限與 Token-2022 擴充，不依賴第三方 | 每個代幣要多次 RPC 呼叫 |
| **AVE Cloud 資料 API** | ✅ 已實作（`--ave`） | 有合約風險 / 貔貅檢測、持有人、熱門榜；免費方案就包含資料 REST API | 需要 `AVE_API_KEY`（在 [cloud.ave.ai](https://cloud.ave.ai) 申請）。回應欄位尚未對應到過濾規則，目前只把保留池子的風險報告原樣存檔，供日後比對 |
| GMGN | ⬜ 未實作 | 資料豐富（聰明錢、KOL、開發者、捆綁錢包持倉） | 官方 OpenAPI 要先到 [gmgn.ai/ai](https://gmgn.ai/ai) 註冊 Ed25519 公鑰才能取得金鑰，且官方只提供 `gmgn-cli` 工具，沒有公開 REST 路徑文件；交易 API 另需足夠交易量才能申請 |

AVE 的端點與驗證方式取自 AVE 官方的 [ave-cloud-skill](https://github.com/AveCloud/ave-cloud-skill)：

- base URL：`https://data.ave-api.xyz/v2`
- 驗證 header：`X-API-KEY`
- 端點：`/contracts/{地址}-solana`（風險）、`/tokens/holders/{地址}-solana`、`/tokens/trending`

GMGN 的資訊取自其官方的 [gmgn-skills](https://github.com/GMGNAI/gmgn-skills)。

## 過濾掉什麼、留下什麼

**原則**：任何一條「丟棄」規則成立，就丟掉這個池子；「標記」規則不丟棄，但會提高風險分數。**全部通過的池子才保留**，並依風險分排序。

### 第 0 階段：建池交易（只適用於自己監聽的新池）

| 規則 | 動作 | 過濾什麼 |
|---|---|---|
| `low_initial_liquidity` | 丟棄 | 建池時存進池子的報價代幣太少（預設 < 5 SOL，或 < 1,000 USDC / USDT）。計算方式：建池交易後，不屬於建池者本人的 SOL / USDC / USDT 代幣帳戶餘額 |
| `serial_creator` | 丟棄 | 同一個建池錢包（交易的手續費付款人）24 小時內已經建了 3 個以上的池子 |

Pump.fun 的聯合曲線使用原生 SOL，初始儲備是固定的，所以不做初始流動性檢查。

### 第 1 階段：市場資料（不花 RPC 額度）

| 規則 | 動作 | 過濾什麼 | 對應案例 |
|---|---|---|---|
| `quote_not_allowed` | 丟棄 | 不是以 SOL / USDC / USDT 報價 | |
| `low_liquidity` | 丟棄 | 流動性 < 10,000 美元，價格容易被操縱 | Mango、Solend USDH、Loopscale |
| `inactive_pool` | 丟棄 | 一小時內沒有任何交易（死池、垃圾池） | |
| `symbol_impersonation` | 丟棄 | 符號與 SOL、USDC、JUP、BONK 等相同，但 mint 地址不同 | Jupiter、Pump.fun X 帳號遭挾持 |
| `honeypot_suspect` | 丟棄 | 一小時內 ≥ 20 次買入卻 0 次賣出 | |
| `deprecated_program` | 丟棄 | 池子建立在已淘汰的程式上（在設定檔中列出） | Raydium 舊版 AMM V3（2026） |
| `fdv_liquidity_ratio` | 標記 | FDV 超過流動性 50 倍 | |

### 第 2 階段：鏈上 mint 檢查

| 規則 | 動作 | 過濾什麼 | 對應案例 |
|---|---|---|---|
| `onchain_check_failed` | 丟棄 | 讀不到 mint 資料（寧可錯殺） | |
| `unknown_token_program` | 丟棄 | 不是 SPL Token / Token-2022 | |
| `mint_authority_active` | 丟棄 | 增發權限未放棄 | Cashio |
| `freeze_authority_active` | 丟棄 | 凍結權限未放棄（貔貅盤常見手法） | |
| `permanent_delegate` | 丟棄 | Token-2022 永久委託，可轉走任何人的幣 | |
| `transfer_hook` | 丟棄 | Token-2022 transfer hook，可阻擋賣出 | |
| `non_transferable` / `default_frozen` / `pausable` | 丟棄 | 不可轉讓、新帳戶預設凍結、可暫停轉帳 | |
| `transfer_fee` | 丟棄 / 標記 | 轉帳稅 > 1% 丟棄；有稅或有調稅權限則標記 | |
| `confidential_transfer` | 標記 | 使用機密轉帳擴充 | ZK ElGamal 漏洞（2025 兩起） |
| `mint_close_authority` | 標記 | mint 可被關閉後重建 | |
| `holder_concentration` | 丟棄 / 標記 | 前 10 大持有人（排除池子與銷毀地址）> 50% 丟棄，> 30% 標記 | |

### 第 3 階段：案例庫比對

| 規則 | 動作 | 說明 |
|---|---|---|
| `protocol_recent_incident` | 標記 | 池子所在的 DEX 在 180 天內有協議層級的資安事件，例如 Raydium 舊版 AMM V3。只計合約漏洞、私鑰外洩、預言機操縱等類別；團隊被詐騙或 X 帳號被盜不算 |

### 從 PancakeSwap 監聽經驗對照

在 BSC 監聽 PancakeSwap V2 / V3 新池時常用的過濾條件，換到 Solana 的對應如下：

| BSC / PancakeSwap 常見過濾 | Solana 對應 | 規則 |
|---|---|---|
| 加池的 BNB / USDT 太少 | 建池存入的 SOL / USDC 太少 | `low_initial_liquidity` |
| 同一部署者連續發幣 | 同一錢包短時間大量建池 | `serial_creator` |
| 不是 WBNB / USDT 報價 | 不是 SOL / USDC / USDT 報價 | `quote_not_allowed` |
| 合約沒有放棄 owner、有 mint 函式 | mint authority 未放棄 | `mint_authority_active` |
| 貔貅：黑名單、限制賣出、模擬賣出失敗 | freeze authority、transfer hook、permanent delegate、可暫停、預設凍結 | `freeze_authority_active` 等 |
| 買賣稅過高 | Token-2022 轉帳手續費 | `transfer_fee` |
| 冒用知名代幣名稱 | 符號相同但 mint 不同 | `symbol_impersonation` |
| 持幣集中在少數地址 | 前 10 大持有人比例 | `holder_concentration` |
| LP 沒有鎖倉 / 銷毀 | LP 代幣是否銷毀 | **尚未實作**（需要解析各 DEX 的池子帳戶） |

EVM 代幣的權限藏在合約程式碼裡，需要分析原始碼或模擬交易。Solana 的 SPL 代幣權限則直接寫在 mint 帳戶，讀一次 RPC 就能判斷，所以大部分貔貅檢查在 Solana 上更簡單可靠。

完整規則與理由可用 `python -m solana_monitor rules` 列出。每條規則的 `case_refs` 都由測試檢查，確保對應的案例確實存在於案例庫。

### 調整門檻

所有門檻都可以用 JSON 設定檔覆寫：

```json
{
  "min_liquidity_usd": 20000,
  "honeypot_min_buys_h1": 30,
  "drop_top10_holder_pct": 40,
  "min_initial_quote": {"SOL": 10, "USDC": 2000, "USDT": 2000},
  "max_creator_pools_24h": 2,
  "deprecated_programs": ["<舊程式 ID>"]
}
```

```bash
python -m solana_monitor scan --config my_filters.json
```

## 使用方式

```bash
# 列出所有過濾規則
python -m solana_monitor rules

# 離線示範：用範例資料跑一次過濾（不需網路）
python -m solana_monitor scan --pairs-file solana_monitor/examples/sample_pairs.json --no-rpc

# 從 DexScreener 抓最新 Solana 代幣並過濾（需網路；RPC 建議用付費節點）
export SOLANA_RPC_URL="https://<你的 RPC>"
python -m solana_monitor scan --limit 30

# 即時監聽新池（需要 pip install -r solana_monitor/requirements.txt）
export SOLANA_WS_URL="wss://<你的 RPC>"
python -m solana_monitor listen --programs raydium_amm_v4,pumpswap

# 保留的池子另外向 AVE 取合約風險報告存檔（需要 AVE Cloud 金鑰）
export AVE_API_KEY="<你的金鑰>"
python -m solana_monitor scan --ave

# 統計：被丟棄的原因、最近保留的池子
python -m solana_monitor report
```

需要能連到 `api.dexscreener.com`、你的 Solana RPC 節點，以及使用 `--ave` 時的 `data.ave-api.xyz`。若在 Claude Code 雲端環境執行，要先在環境設定的網路存取中允許這些網域。

## 資料庫（`solana_monitor/data/market.db`）

| 資料表 / 檢視表 | 內容 |
|---|---|
| `pool_snapshots` | 每次觀察到的池子（來源、DEX、代幣、流動性、交易次數等） |
| `mint_checks` | 鏈上 mint 檢查結果（權限、Token-2022 擴充、持幣集中度） |
| `decisions` | 保留或丟棄，以及風險分 |
| `rule_hits` | 觸發的規則、理由與對應案例 |
| `new_pool_events` | 監聽到的新池建立交易（建池者、代幣、初始流動性） |
| `external_reports` | 第三方報告原始資料（目前是 AVE 合約風險） |
| `rules` | 規則說明（與程式碼同步） |
| `v_kept` | 通過全部過濾的池子 |
| `v_drop_reasons` | 各規則丟棄了多少池子 |

`market.db` 是執行時產生的資料，已加入 `.gitignore`。

## 限制

- **監聽標記依賴程式日誌格式**：DEX 升級後日誌字串可能改變，需要更新 `config.py` 的 `create_markers`。
- **剛建立的池子沒有市場數據**：監聽到的新池只做鏈上檢查；等 DexScreener 收錄後，再用 `scan` 補市場數據。
- **持幣集中度的池子地址排除是啟發式的**：目前只內建 Raydium AMM v4、CPMM 的 authority 與銷毀地址，其他 DEX 的池子帳戶可能被算進集中度。可以在 `config.py` 的 `POOL_AUTHORITIES` 補充。
- **批量建池只算本工具看到的池子**：`serial_creator` 只統計監聽器啟動後記錄到的建池次數，剛啟動時會低估。
- **初始流動性是啟發式計算**：以建池交易後「不屬於建池者」的報價代幣餘額計算；少數 DEX 若在同一筆交易做其他轉帳，數字可能偏差。
- **貔貅盤判斷只看買賣次數**：沒有實際模擬賣出交易，可能漏判或誤判。
- **過濾只降低風險，不保證安全**：規則以已知手法為基礎，新型攻擊需要持續回填案例庫並新增規則。
