# Ethereum / BSC 流動池資料收集與過濾（evm_monitor）

與 [solana_monitor](../solana_monitor/README.md) 相同的流程，套用在 EVM 鏈：收集新流動池 → 分階段過濾 → 保留 / 丟棄的決定與理由全部存進 SQLite。支援 **Ethereum** 與 **BNB Smart Chain（BSC）**，用 `--chain ethereum` 或 `--chain bsc` 切換（預設 BSC）。

## 監聽哪些工廠合約

| 鏈 | DEX | 工廠合約 | 事件 |
|---|---|---|---|
| BSC | PancakeSwap V2 | `0xcA143Ce32Fe78f1f7019d7d551a6402fC5350c73` | `PairCreated` |
| BSC | PancakeSwap V3 | `0x0BFbCF9fa4f9C56B0F40a671Ad40E0805A091865` | `PoolCreated` |
| Ethereum | Uniswap V2 | `0x5C69bEe701ef814a2B6a3EDD4B1652CB9cc5aA6f` | `PairCreated` |
| Ethereum | Uniswap V3 | `0x1F98431c8aD98523631AE4a59f267346ea31F984` | `PoolCreated` |
| Ethereum | SushiSwap V2 | `0xC0AEe478e3658e2610c5F7A4A2E1777cE9e4f2Ac` | `PairCreated` |

所有寫死的地址都通過 EIP-55 checksum 檢查（測試會驗證），事件 topic 也在本機以 keccak256 算過。

監聽有兩種模式：

- **輪詢（預設）**：只用標準函式庫，每隔幾秒對新區塊範圍呼叫 `eth_getLogs`；會等 2 個確認區塊再處理。
- **WebSocket**：`eth_subscribe("logs")`，延遲最低，需要 `pip install -r evm_monitor/requirements.txt` 與 WS 節點。

偵測到新池後，會讀取建池交易的發送者（建池者），以及建池區塊時池子裡的 WETH / WBNB / 穩定幣餘額（初始流動性）。

## 資料來源

| 來源 | 狀態 | 用途 |
|---|---|---|
| 自己監聽工廠合約（JSON-RPC） | ✅ `listen` | 最即時的新池資料 |
| DexScreener | ✅ `scan` | 流動性、交易次數、FDV |
| **GoPlus token security** | ✅ 預設開啟 | 貔貅、買賣稅、擁有者權限、LP 鎖倉、持幣分布。端點與欄位取自 GoPlus 官方 Python SDK：`https://api.gopluslabs.io/api/v1/token_security/{chain_id}`。免金鑰可用；有金鑰可設 `GOPLUS_API_KEY` |
| AVE | ✅ `--ave` | 保留的池子另外存 AVE 合約風險報告（`/contracts/{地址}-eth` / `-bsc`），需 `AVE_API_KEY` |
| GMGN | ⬜ | 需註冊 Ed25519 公鑰取得金鑰，官方只提供 CLI，見 solana_monitor README |

EVM 代幣的權限寫在合約程式碼裡，不像 Solana 直接寫在 mint 帳戶，所以合約安全判斷依賴 GoPlus 的靜態分析與模擬交易結果。

## 過濾掉什麼、留下什麼

任何一條「丟棄」規則成立就丟掉；「標記」只加風險分。全部通過的池子才保留。

### 第 0 階段：建池交易（只限監聽）

| 規則 | 動作 | 過濾什麼 |
|---|---|---|
| `low_initial_liquidity` | 丟棄 | 建池時池子裡的報價代幣太少（預設 < 1 WETH、< 3 WBNB、< 2,000 USDT / USDC / BUSD / DAI） |
| `serial_creator` | 丟棄 | 同一建池錢包 24 小時內已建 3 個以上的池子 |

### 第 1 階段：市場資料

| 規則 | 動作 | 過濾什麼 |
|---|---|---|
| `quote_not_allowed` | 丟棄 | 不是以 WETH / WBNB / USDT / USDC / BUSD / DAI 報價 |
| `low_liquidity` | 丟棄 | 流動性 < 10,000 美元 |
| `inactive_pool` | 丟棄 | 一小時內沒有交易 |
| `symbol_impersonation` | 丟棄 | 符號是 USDT、WETH、CAKE、PEPE 等，但合約地址不是正牌 |
| `honeypot_suspect` | 丟棄 | 一小時內 ≥ 20 次買入、0 次賣出 |
| `fdv_liquidity_ratio` | 標記 | FDV 超過流動性 50 倍 |

### 第 2 階段：合約安全（GoPlus）

| 規則 | 動作 | 過濾什麼 |
|---|---|---|
| `security_check_failed` | 丟棄 | 查不到合約安全資料（寧可錯殺） |
| `honeypot` | 丟棄 | 判定為貔貅、不能買，或不能全部賣出 |
| `high_tax` | 丟棄 | 買或賣稅 > 10% |
| `tax_modifiable` | 丟棄 | 擁有者未放棄，且可以修改稅率或對個別地址設稅 |
| `owner_privileges` | 丟棄 | 可收回所有權、直接改餘額、隱藏擁有者、自毀；擁有者未放棄時的增發權限 |
| `transfer_restrictions` | 丟棄 | 擁有者未放棄，且可以暫停交易或設黑名單 |
| `not_open_source` | 丟棄 | 合約原始碼未驗證 |
| `scam_history` | 丟棄 | 同一部署者發過貔貅、空投詐騙或仿冒代幣 |
| `lp_unlocked` | 丟棄 | 鎖倉或銷毀的 LP < 50%（可隨時撤池） |
| `holder_concentration` | 丟棄 / 標記 | 前 10 大持有人（排除池子、鎖倉、銷毀地址）> 50% 丟棄，> 30% 標記 |
| `upgradeable_proxy` | 標記 | 可升級代理合約 |
| `external_call` / `trading_cooldown` | 標記 | 轉帳時呼叫外部合約、交易冷卻限制 |
| `creator_holds_supply` | 標記 | 部署者持幣 > 10% |

### 第 3 階段：案例庫

`protocol_recent_incident`（標記）：DEX 在 `ethereum_hacks` / `bsc_hacks` 案例庫中 180 天內有協議層級的資安事件。這兩個案例庫目前還沒有資料，建好後自動生效。

所有門檻都能用 `--config my_filters.json` 覆寫，欄位見 `evm_monitor/config.py` 的 `FilterConfig`。

## 使用方式

```bash
python -m evm_monitor --chain bsc rules

# 離線示範（不需網路）
python -m evm_monitor --chain bsc scan --pairs-file evm_monitor/examples/sample_pairs_bsc.json --no-security

# 從 DexScreener 抓最新代幣 + GoPlus 檢查
python -m evm_monitor --chain bsc scan --limit 30

# 監聽 PancakeSwap V2 / V3 新池（輪詢）
export BSC_RPC_URL="https://<你的 BSC 節點>"
python -m evm_monitor --chain bsc listen

# 監聽 Uniswap（WebSocket）
export ETH_WS_URL="wss://<你的以太坊節點>"
python -m evm_monitor --chain ethereum listen

python -m evm_monitor --chain bsc report
```

需要能連到 RPC 節點、`api.dexscreener.com`、`api.gopluslabs.io`，以及使用 `--ave` 時的 `data.ave-api.xyz`。

資料存在 `evm_monitor/data/market_<鏈>.db`（已加入 `.gitignore`）。資料表與 solana_monitor 相同，只是鏈上檢查結果存在 `token_checks`（GoPlus 原始回應在 `raw_json`）。

## 限制

- **依賴 GoPlus**：GoPlus 本身也可能誤判或漏判，而且資料需要時間更新，剛建立的代幣可能查不到（會被 `security_check_failed` 丟棄）。
- **沒有在本機模擬賣出**：若要更準確地判斷貔貅，可以用分叉節點（例如 anvil）實際模擬一次買賣。
- **初始流動性**以建池區塊結束時的池子餘額計算；如果流動性在之後的區塊才加入，會被 `low_initial_liquidity` 誤判為過低。
- **批量建池**只統計監聽器啟動後看到的池子。
- **公共 RPC 限流**：`eth_getLogs` 的區塊範圍與頻率常被限制，長時間監聽建議使用付費節點。
