# Claude Code 記憶切換器（claude-switch）

一鍵把目前 Claude Code 的「記憶和所有東西」抽離、保存起來，換成一個**全新、乾淨的 Claude Code**；
之後隨時可以**切回舊的記憶**，來回切換都不會遺失任何東西。

終端機的 `claude`、VS Code／JetBrains 外掛，以及 **Claude Desktop 裡的 Claude Code（Code 分頁）**
讀的是同一份設定，所以切換一次，全部一起生效。

- 只用 Python 3.8+ 標準函式庫，不需要安裝任何套件
- 支援 macOS、Windows、Linux
- 切換只是「改名搬家」，不複製、不刪除；每一步都先寫進日誌，失敗會自動還原
- 登入狀態由所有設定檔共用：切到乾淨版時仍是登入狀態，不必重新登入

> English summary at the [bottom](#english).

---

## 快速開始

1. 把整個 `claude-memory-switcher` 資料夾放到任何地方（例如「文件」）。
2. **先完全關閉** Claude Desktop（macOS：選單列 Claude → 結束；Windows：系統匣圖示 → Quit），
   並結束所有 `claude` 終端機工作階段（輸入 `/exit`）。忘了也沒關係，工具會偵測並提醒你，
   也可以幫你關掉 Claude Desktop。
3. 雙擊啟動：

| 系統 | 選單（全部功能） | 一鍵切換（乾淨版 ⇄ 舊記憶） |
|---|---|---|
| macOS | `claude-switch-menu.command` | `claude-switch-toggle.command` |
| Windows | `claude-switch-menu.bat` | `claude-switch-toggle.bat` |
| Linux | `claude-switch-menu.sh` | `claude-switch-toggle.sh` |

第一次按「一鍵切換」時，目前的 Claude Code 會被保存成設定檔 **original**（舊記憶），
並建立、切換到全新的設定檔 **clean**（乾淨版）。之後每按一次就在兩者之間來回切換。

> **macOS 第一次開啟**（從網路下載的檔案會被 Gatekeeper 擋下）：
> - **macOS 15（Sequoia）以後**：先雙擊一次，看到「無法驗證…」的提示後按「完成」；接著打開
>   **系統設定 → 隱私權與安全性**，往下捲到「已阻擋…」那一行按 **「強制打開」**（需要輸入密碼，
>   約一小時內有效），再雙擊一次即可。右鍵 →「打開」在 macOS 15 以後已經沒有用了。
> - **macOS 14 以前**：在檔案上按右鍵 →「打開」。
> - 或用終端機一次解除整個資料夾：輸入 `xattr -dr com.apple.quarantine `（最後有一個空白），
>   再把 `claude-memory-switcher` 資料夾拖進終端機視窗，按 Enter。
> - 若提示要安裝「命令列開發者工具」，照著安裝即可（它內含 Python 3）。
>
> **Windows**：需要先安裝 [Python 3](https://www.python.org/downloads/)，安裝時勾選
> 「Add python.exe to PATH」。

### 選單長這樣

```
== Claude Code 記憶切換器 1.0.0 ==
目前使用: original
設定檔  (* = 使用中)
       名稱      大小      建立              上次使用          備註
  * 1  original  182.4 MB  2026-09-28 10:02  2026-09-28 10:02  第一次切換前的 Claude Code（舊記憶）

  [1] 切換到乾淨的 Claude Code
  [4] 建立新的乾淨設定檔…
  [5] 複製一個設定檔…
  [6] 匯出（備份）設定檔成 zip…
  ...
  [x] 把目前的設定檔「original」清空成乾淨狀態（舊內容移到垃圾桶）…
```

清空（`[x]` 或 `reset`）一定會先問你；舊內容會先放進垃圾桶，不會直接刪除。

---

## 會切換哪些東西

Claude Code 的個人狀態放在下面這些地方，這個工具會把它們**整組**切換：

| 位置 | 內容 |
|---|---|
| `~/.claude/`（Windows：`%USERPROFILE%\.claude\`） | 全域記憶 `CLAUDE.md`、`rules/`、每個專案的**自動記憶** `projects/*/memory/`、**所有對話紀錄** `projects/*.jsonl`、輸入歷史 `history.jsonl`、`settings.json`、`agents/`、`commands/`、`skills/`、`plugins/`、`agent-memory/`、`plans/`、`todos/`、`file-history/`（/rewind 用）、`backups/`、Desktop 本機排程任務的提示 `scheduled-tasks/`…… |
| `~/.claude.json`（Windows：`%USERPROFILE%\.claude.json`）、`~/.claude.json.backup` | 每個專案的信任設定與狀態、**MCP 伺服器**（user／local 範圍）、onboarding、帳號資訊、各種快取 |
| 家目錄的 `~/CLAUDE.md`、`~/CLAUDE.local.md` | 放在家目錄的記憶檔，對家目錄底下所有專案都有效，所以也算你的記憶 |
| **Claude Desktop 的 Code 工作階段清單**：macOS `~/Library/Application Support/Claude/claude-code-sessions/`、Windows `%APPDATA%\Claude\claude-code-sessions\`（Microsoft Store 版在 `%LOCALAPPDATA%\Packages\Claude_*\LocalCache\Roaming\Claude\`）、Linux `~/.config/Claude/claude-code-sessions/` | Desktop「Code」分頁側邊欄的工作階段紀錄，以及本機排程任務的排程。它們指向 `~/.claude/projects/` 裡的對話，所以要一起切換：切到乾淨版後，Desktop 不會再列出舊的工作階段，舊的排程任務也不會在乾淨版裡執行。 |

如果有設定 `CLAUDE_CONFIG_DIR`，就改為切換那個資料夾（以及其中的 `.claude.json`）。
舊版的 `~/.claude/.config.json`，以及少見的 `~/.claude-custom-oauth.json` 等變體，也會一併切換。

### 刻意「不」切換、所有設定檔共用的東西

| 項目 | 原因 |
|---|---|
| 登入：`~/.claude/.credentials.json`（Windows／Linux）、macOS「鑰匙圈」、Windows 認證管理員 | 讓乾淨版也維持登入；也避免舊副本的權杖過期。MCP 的 OAuth 登入和外掛密鑰也存在這裡，所以同樣共用。 |
| `~/.claude/local/` | 舊版用 npm 安裝的 `claude` 程式本體，搬走會讓 `claude` 指令壞掉 |
| `~/.claude/ide/`、`~/.claude/sessions/`、`~/.claude/daemon.lock` | 執行中程式的鎖定檔／標記，屬於「這台電腦現在的狀態」，不是記憶 |
| `~/.claude/.device-keys.json`、`~/.claude/chrome/` | 這台電腦的裝置識別，以及 Claude in Chrome 擴充功能的連接程式（瀏覽器指向這個路徑） |
| `~/.local/bin/claude`、`~/.local/share/claude/`、Homebrew／WinGet／npm 安裝 | 程式本體，本來就不在 `~/.claude` 裡，完全不碰 |

### 不在上面這些位置、所以不受影響的東西

執行 `doctor`（選單 `[s]`）會檢查前四項在你電腦上是否存在並列出來；後面幾項它不會檢查，請自行留意：

- **專案資料夾裡的** `CLAUDE.md`、`CLAUDE.local.md`、`.claude/`、`.mcp.json`：屬於各個專案，乾淨版在那個專案裡仍會讀到它們。
  `doctor` 會列出任何一個設定檔用過的專案裡有哪些這類檔案（切到乾淨版後也會直接列出來）。
- 用 `autoMemoryDirectory` 設定（或 `CLAUDE_CODE_REMOTE_MEMORY_DIR`）把自動記憶放在別處時，那個資料夾不會被切換。
- 終端機的環境變數 `ANTHROPIC_API_KEY`、`ANTHROPIC_AUTH_TOKEN`、`CLAUDE_CODE_OAUTH_TOKEN`、`ANTHROPIC_BASE_URL`：終端機版在任何設定檔都會用它們。
- Claude Desktop 自己的登入、設定 `claude_desktop_config.json`（Desktop 的 MCP 伺服器，也會提供給 Code 分頁），以及 Cowork 的工作階段與記憶。
- （不檢查）Windows 上 Desktop 的 WSL 工作階段用的是 WSL 裡的 `~/.claude`：想連它一起切換，請在 WSL 裡也執行一次這個工具。
- （不檢查）公司／組織的管理設定（managed settings）。
- **claude.ai 網頁／App 的聊天記憶、Projects、連接器（connectors）**：存在 Anthropic 伺服器上，不在你的電腦裡。
  同一個帳號登入時，claude.ai 上啟用的技能和外掛會再同步下來；想要完全乾淨，建立設定檔時加
  `--no-account-sync`（或在選單回答「是」），工具會在乾淨版的 `settings.json` 設定
  `syncClaudeAiSkills: false`、`syncClaudeAiPlugins: false`、`disableClaudeAiConnectors: true`。

---

## 指令列用法

```bash
python3 claude_switch.py                  # 互動選單
python3 claude_switch.py clean            # 保存目前狀態，切到乾淨版（第一次會建立）
python3 claude_switch.py clean --reset    # 把乾淨版再清空一次（舊內容移到垃圾桶）
python3 claude_switch.py toggle           # 在最近兩個設定檔之間切換（只有一個設定檔時＝切到乾淨版）
python3 claude_switch.py switch original  # 切到指定設定檔
python3 claude_switch.py status           # 列出設定檔、大小、目前使用哪一個
python3 claude_switch.py new work --copy settings.json --copy agents   # 新的乾淨設定檔，帶上部分設定
python3 claude_switch.py clone original backup-copy                    # 複製一份設定檔
python3 claude_switch.py export original -o ~/Desktop                  # 備份成 zip
python3 claude_switch.py import ~/Desktop/claude-profile-original-….zip restored
python3 claude_switch.py rename clean 乾淨版
python3 claude_switch.py delete old-test  # 移到垃圾桶（不能刪除使用中的）
python3 claude_switch.py trash --empty    # 真正刪除垃圾桶
python3 claude_switch.py doctor           # 哪些東西不會被切換
python3 claude_switch.py repair           # 修復中斷的切換（會問你要「完成」還是「還原」；
                                          #   也可加 --forward / --rollback；非互動時預設還原）
python3 claude_switch.py where            # 顯示各個路徑
```

常用選項：

| 選項 | 作用 |
|---|---|
| `--dry-run` | 只顯示會搬移哪些檔案，不做任何改變 |
| `--quit-desktop` | 若 Claude Desktop 正在執行，先幫你結束它 |
| `--force` | Claude 仍在執行也照樣切換（不建議） |
| `--copy ITEM` | 建立乾淨設定檔時，從目前的 `~/.claude` 複製某個項目過去（可重複） |
| `--no-account-sync` | 乾淨設定檔不要從 claude.ai 帳號同步技能／外掛／連接器 |
| `--store DIR` | 設定檔倉庫位置（預設 `~/.claude-profiles`，也可用環境變數 `CLAUDE_SWITCH_HOME`；必須和 `~/.claude` 在同一個磁碟） |
| `--lang zh` / `--lang en` | 介面語言（預設依系統語言；也可設環境變數 `CLAUDE_SWITCH_LANG`） |
| `--keep-api-settings` / `--no-api-settings` | 建立乾淨設定檔時，是否保留 API 供應商設定（`ANTHROPIC_BASE_URL`、金鑰、`apiKeyHelper`、代理伺服器；cc-switch 寫的就是這些）。沒指定時，偵測到就會問你 |
| `-y` / `--yes` | 不要詢問確認 |

選項放在指令前面或後面都可以，例如 `trash --empty --yes` 和 `--yes trash --empty` 一樣。
`--dry-run` 適用於 `switch`、`toggle`、`clean`、`new`、`clone`、`reset`、`repair`，其他指令加了會直接拒絕。

---

## 安全設計

- **只改名、不複製、不刪除**：切換時把檔案「改名」搬進 `~/.claude-profiles/profiles/<名稱>/`，
  再把目標設定檔改名搬回原位。幾 GB 的對話紀錄也是瞬間完成，檔案時間戳不變。
  唯一會真正刪除檔案的是你自己執行 `trash --empty`。
- **Claude 在執行時拒絕切換**：Claude Code 每次執行都會改寫 `~/.claude.json`；
  如果切換時它還開著，就會把舊記憶寫進乾淨版。工具會偵測 `claude`、Claude Desktop、
  VS Code 外掛內建的 Claude Code、背景 daemon，確認都關閉了才動手。
- **日誌＋自動還原**：每一步搬移前都先記錄。任何一步失敗（例如 Windows 上檔案被占用）
  會立刻按相反順序還原，結果就像沒切換過一樣。就算中途斷電或強制關閉，下次執行 `repair`
  可以選擇「完成」或「還原」那次切換。
- **不覆蓋任何東西**：遇到不該存在的殘留檔案，會移到 `~/.claude-profiles/_conflicts/` 保存，並告訴你。
- **重置、刪除都先進垃圾桶**：`~/.claude-profiles/_trash/`。
- **設定位置被改過會拒絕**：若 `CLAUDE_CONFIG_DIR` 和第一次使用時不同，不會亂搬（可用 `--force`）。
- **不會同時執行兩個**：有鎖定檔保護。
- **匯出不含登入憑證**；`claude.json`、`settings.json` 裡看得出是金鑰或權杖的值（包括 MCP 伺服器的 env 與 headers）
  預設會改成 `<redacted>`。對話紀錄裡仍可能有你貼過的機密，請妥善保管 zip。匯入時會擋掉 zip 路徑穿越攻擊。

倉庫結構：

```
~/.claude-profiles/
  switcher.json(.bak)      目前使用哪個設定檔、各設定檔資訊、進行中的切換日誌（.bak 是上一版）
  switch.log               操作紀錄
  profiles/
    original/              ← 沒在使用時放它的內容：
      config/                ~/.claude 裡的東西
      claude.json            ~/.claude.json（以及 claude.json.backup 等）
      home/                  ~/CLAUDE.md、~/CLAUDE.local.md
      desktop/<mac|win|…>/   Claude Desktop 的 Code 工作階段清單
    clean/                 ← 使用中的設定檔，這裡是空的（它的內容就在原本的位置）
  _trash/  _conflicts/
```

`~/.claude/.claude-switch.json` 是工具寫的小標記，記錄目前的內容屬於哪個設定檔，用來避免弄混；可以放心留著。

---

## 常見問題

**切換後要做什麼？** 重新開啟 Claude Desktop 或 `claude` 即可。VS Code／JetBrains 請重新載入視窗，讓外掛重新連線。

**乾淨版第一次啟動時，要我再信任資料夾？** 正常。「信任這個資料夾」的紀錄存在 `~/.claude.json`，乾淨版是全新的。

**切回舊記憶後，很舊的對話不見了？** Claude Code 啟動時會自動刪除超過 `cleanupPeriodDays`（預設 30 天）的對話紀錄，
這是 Claude Code 本身的行為（依檔案時間計算，停放的時間也算）。切換時若發現有這種舊紀錄，工具會提醒你，
並問你要不要把該設定檔的 `cleanupPeriodDays` 設為 3650 天來保留它們；也可以先 `export` 備份。

**我用 cc-switch 或第三方 API（`ANTHROPIC_BASE_URL`）？** 建立乾淨版時工具會偵測到並問你要不要保留這些連線設定；
不保留的話，乾淨版會改用你的 Claude 帳號登入。cc-switch 之後會把設定寫進「目前使用中」的設定檔。

**Claude Desktop 側邊欄還看得到舊的工作階段？** Code 分頁的本機工作階段清單會跟著切換，
但有少數卡片是從伺服器同步的，可能仍會出現；點開打不開是正常的，切回 original 就能用。
Desktop 自己的登入不受影響，所以 Desktop 裡的乾淨版一樣是登入狀態。

**想用不同帳號？** 登入是共用的：在任一設定檔執行 `/login` 換帳號，所有設定檔都會跟著換。
切換時，帳號顯示資訊（`oauthAccount`）也會同步到新設定檔。

**不要用 `/logout` 來「清空」！** 登出會在伺服器端撤銷權杖，並刪除 MCP OAuth 登入，影響所有設定檔。

**怎麼完全移除這個工具？** 切回 `original`（`python3 claude_switch.py switch original`），
確認一切正常後刪除 `~/.claude-profiles` 資料夾即可。

**出錯了怎麼辦？** 先執行 `python3 claude_switch.py repair`。所有操作都記錄在 `~/.claude-profiles/switch.log`。
沒有任何檔案會被刪除，最壞情況也只是檔案在 `~/.claude-profiles/` 裡等你搬回來。

**備份檔要還原成另一個名稱？** 選單的「從 zip 匯入」發現名稱重複時會請你取新名字（預設 `<名稱>-restored`）；
在 macOS 可以直接把 zip 從 Finder 拖進終端機視窗。

---

## 開發

```bash
python3 -m pytest tools/claude-memory-switcher -q
```

測試會在暫存的假家目錄裡進行，包含：來回切換逐位元比對、在**每一個步驟**注入錯誤驗證自動還原、
在每一個步驟模擬當機後用 `repair` 往前完成／往後還原、Claude 執行中拒絕切換、zip 路徑穿越、
各平台的程序偵測解析等。

---

## English

**claude-switch** parks everything your Claude Code has accumulated (global and per-project memory, auto
memory, conversation history, settings, agents, commands, skills, plugins, MCP servers, per-project trust) in a
named profile and gives you a brand-new, clean Claude Code. It switches back to the old one just as easily. It
applies to the terminal CLI, the IDE extensions and the Code tab of **Claude Desktop**, because they all read
`~/.claude/` and `~/.claude.json`.

- Double-click `claude-switch-menu.*` for the menu or `claude-switch-toggle.*` for one-click clean ⇄ original.
  On macOS 15+, a downloaded `.command` is blocked the first time: allow it in System Settings → Privacy &
  Security → "Open Anyway" (right-click → Open no longer works there), or run `xattr -dr com.apple.quarantine`
  on the folder.
- CLI: `python3 claude_switch.py clean | toggle | switch NAME | status | new | clone | export | import | doctor | repair`.
- Switching only **renames** files into `~/.claude-profiles/profiles/<name>/`. Nothing is copied or deleted.
  Every move is journaled first. On a failure, the moves done so far are undone in reverse order. After a crash,
  `repair` either finishes the switch or rolls it back.
- The tool refuses to switch while Claude Code or Claude Desktop is running, because Claude rewrites
  `~/.claude.json` and would pollute the other profile. It can quit Claude Desktop for you.
- Claude Desktop's list of Code sessions (`<Desktop app data>/claude-code-sessions/`, which also holds the schedules
  of local scheduled tasks) is switched together with `~/.claude`. The transcripts those records point to live in
  `~/.claude/projects/`, so a clean profile shows no old sessions in Desktop's Code tab.
- Login is shared by all profiles: `.credentials.json` stays in place, and the macOS Keychain and Windows
  Credential Manager are not per-profile anyway. A clean profile is therefore still signed in. Claude Desktop has
  its own login and is not affected.
- `~/CLAUDE.md` / `~/CLAUDE.local.md` in the home folder (loaded for every project below it) are part of the profile.
  API-provider settings (`ANTHROPIC_BASE_URL`, keys, `apiKeyHelper`, proxies, as written by cc-switch) can be carried
  into a new clean profile (`--keep-api-settings`; asked interactively when detected).
- Not part of a profile: project-level `CLAUDE.md` / `.claude/`, `autoMemoryDirectory` outside `~/.claude`,
  API-key environment variables, Claude Desktop's own `claude_desktop_config.json`, managed settings, and
  anything server-side (claude.ai chat memory, connectors, synced skills/plugins). `doctor` lists what applies
  to your machine. `--no-account-sync` turns off claude.ai skill/plugin/connector sync in a clean profile.
- Options work before or after the command. `--dry-run` changes nothing (switch, toggle, clean, new, clone, reset,
  repair). Reset always asks first and moves the old contents to the trash.
- Zero dependencies, Python 3.8+, macOS / Windows / Linux. Tests:
  `python3 -m pytest tools/claude-memory-switcher -q`.
