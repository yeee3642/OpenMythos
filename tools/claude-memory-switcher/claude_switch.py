#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
claude-switch -- one-click switcher between a clean Claude Code and your old one.

Claude Code keeps everything it "remembers" in two places in your home folder:

    ~/.claude/        CLAUDE.md, auto memory, conversation history, settings,
                      agents, commands, skills, plugins, todos, plans, ...
    ~/.claude.json    per-project state, MCP servers, onboarding, account info

Claude Desktop's "Code" feature runs the same Claude Code and reads the same
files, so a switch here applies to the terminal CLI, IDE extensions and
Claude Desktop alike.

This tool parks those files inside a named *profile* under
~/.claude-profiles/ and puts another profile's files in their place.  A switch
only renames files (no copying, no deleting), every step is written to a
journal first, and a failed or interrupted switch can always be rolled back
with `repair`.  Your login is shared by all profiles, so a clean profile is
still signed in.

Zero dependencies: Python 3.8+ standard library only.  macOS, Windows, Linux.

    python3 claude_switch.py                 interactive menu
    python3 claude_switch.py clean           park current state, start clean
    python3 claude_switch.py toggle          flip between the last two profiles
    python3 claude_switch.py status          show profiles
    python3 claude_switch.py --help          everything else
"""
from __future__ import annotations

import argparse
import datetime as _dt
import errno
import json
import locale
import os
import re
import shutil
import signal
import stat
import subprocess
import sys
import time
import unicodedata
import uuid
import zipfile
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

__version__ = "1.0.0"

IS_WINDOWS = os.name == "nt"
IS_MAC = sys.platform == "darwin"

STATE_FORMAT = 1
STATE_FILE = "switcher.json"
LOG_FILE = "switch.log"
LOCK_FILE = ".lock"
PROFILES_DIR = "profiles"
TRASH_DIR = "_trash"
CONFLICTS_DIR = "_conflicts"
MANIFEST_NAME = "claude-switch-manifest.json"

# Inside a profile folder: children of the config dir go to "config/", the
# global config file (~/.claude.json) goes to "claude.json", and Claude
# Desktop's list of Code sessions goes to "desktop/<where>".
SLOT_CONFIG = "config"
SLOT_GLOBAL = "claude.json"
SLOT_DESKTOP = "desktop"
SLOT_HOME = "home"

# CLAUDE.md files directly in the home folder are loaded for every project
# under it, so they are part of "your memory" as much as ~/.claude/CLAUDE.md.
HOME_MEMORY_FILES = ("CLAUDE.md", "CLAUDE.local.md")

# ~/.claude<suffix>.json: "" is the normal one; the others belong to non-default
# sign-in environments and exist only on machines that used them.
GLOBAL_SUFFIXES = ("", "-custom-oauth", "-staging-oauth", "-local-oauth")

# Written into the live config dir: which profile it belongs to.  It moves
# with the profile, so a mismatch means something else changed ~/.claude.
MARKER_FILE = ".claude-switch.json"

# Lock directories Claude Code holds (and touches every few seconds) while it
# writes its config or refreshes the login.  A fresh one means "running".
LOCK_DIRS_FRESH_SECONDS = 30

# Claude Desktop keeps its Code-tab session records (and the schedule of its
# local scheduled tasks) here, inside its app-data folder.  The transcripts
# they point to live in ~/.claude/projects, so the two are switched together.
DESKTOP_SESSIONS = "claude-code-sessions"

DEFAULT_OLD_NAME = "original"
DEFAULT_CLEAN_NAME = "clean"

# Children of ~/.claude that are NOT profile data and must never be parked:
# they stay in place and are shared by every profile.
PINNED_CHILDREN = {
    # Login tokens (Linux/Windows; macOS uses the Keychain).  Sharing them
    # keeps every profile signed in, and avoids a parked copy going stale when
    # the live one rotates its refresh token.
    ".credentials.json": "login",
    # Legacy "local" npm install of the claude binary (claude migrate-installer).
    # Moving it would break the `claude` command itself.
    "local": "install",
    # Where the Windows installer stages a new claude binary.
    "downloads": "install",
    # Lock files written by running IDE extensions (VS Code, JetBrains);
    # runtime state, not memory.
    "ide": "runtime",
    # One marker file per *running* Claude Code session, and the background
    # supervisor's lock.  They describe this machine right now, not a profile.
    "sessions": "runtime",
    "daemon.lock": "runtime",
    ".oauth_refresh.lock": "runtime",
    ".oauth_refresh.lock.owner": "runtime",
    ".design_oauth_refresh.lock": "runtime",
    "history.jsonl.lock": "runtime",
    ".update.lock": "runtime",
    "server.lock": "runtime",
    "computer-use.lock": "runtime",
    ".cc-writes": "runtime",
    # Per-machine device identity (its macOS Keychain twin is shared anyway).
    ".device-keys.json": "device",
    # Native-messaging host for the Claude in Chrome extension; the browser's
    # manifest points at this path.
    "chrome": "integration",
}

# Keys copied from the current ~/.claude.json into a fresh profile, so the
# clean Claude Code stays signed in and skips first-run onboarding.  Nothing
# here is memory: no projects, MCP servers, history or tips.
SEED_KEYS = (
    # account / auth
    "oauthAccount",
    "primaryApiKey",
    "customApiKeyResponses",
    "userID",
    "machineID",
    # onboarding / appearance
    "hasCompletedOnboarding",
    "lastOnboardingVersion",
    "theme",
    # how claude itself is installed (native installer / npm)
    "installMethod",
    "autoUpdates",
    "autoUpdatesProtectedForNative",
)
# Cached feature flags.  Not memory, but they decide things such as which
# credential store Claude Code reads on Windows, so a fresh profile without
# them could look signed out on its first start.
SEED_PREFIXES = ("cachedGrowthBook", "cachedStatsig", "cachedExperiment", "cachedDynamicConfig")

# Keys that follow the shared login when switching (see _sync_account).
ACCOUNT_KEYS = ("oauthAccount",)

SECRET_KEYS = ("primaryApiKey",)

# User settings that stop a signed-in account from pulling skills, plugins and
# connectors from claude.ai into a clean profile (--no-account-sync).
NO_ACCOUNT_SYNC_SETTINGS = {
    "syncClaudeAiSkills": False,
    "syncClaudeAiPlugins": False,
    "disableClaudeAiConnectors": True,
}

# Environment variables that authenticate Claude Code regardless of profile.
AUTH_ENV_VARS = ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "CLAUDE_CODE_OAUTH_TOKEN")

# settings.json "env" keys that point Claude Code at an API provider (what
# cc-switch and similar tools write).  A clean profile without them may not be
# able to connect at all, so they can be carried over on request.
_API_ENV_PREFIXES = ("ANTHROPIC_", "CLAUDE_CODE_USE_", "AWS_", "VERTEX_", "CLOUD_ML_")
_API_ENV_KEYS = ("API_TIMEOUT_MS", "HTTPS_PROXY", "HTTP_PROXY", "NO_PROXY", "https_proxy", "http_proxy", "no_proxy")
_API_TOP_KEYS = ("apiKeyHelper", "awsAuthRefresh", "awsCredentialExport")


def is_api_env_key(key: str) -> bool:
    return key.startswith(_API_ENV_PREFIXES) or key in _API_ENV_KEYS

_WIN_RESERVED = {
    "CON", "PRN", "AUX", "NUL",
    *("COM%d" % i for i in range(1, 10)),
    *("LPT%d" % i for i in range(1, 10)),
}


# --------------------------------------------------------------------------
# i18n
# --------------------------------------------------------------------------

# key: (English, Traditional Chinese)
MESSAGES: Dict[str, Tuple[str, str]] = {
    "title": ("Claude Code memory switcher", "Claude Code 記憶切換器"),
    "active": ("Active profile", "目前使用"),
    "none": ("(none)", "（無）"),
    "profiles": ("Profiles", "設定檔"),
    "col_name": ("name", "名稱"),
    "col_size": ("size", "大小"),
    "col_created": ("created", "建立"),
    "col_used": ("last used", "上次使用"),
    "col_note": ("note", "備註"),
    "marker_active": ("* = active", "* = 使用中"),
    "not_initialized": (
        "Not set up yet. Your current Claude Code will be saved as profile '{name}' the first time you switch.",
        "尚未初始化。第一次切換時，目前的 Claude Code 會被保存為設定檔「{name}」。",
    ),
    "initialized": (
        "Your current Claude Code (memory, history, settings) is now profile '{name}'.",
        "已把目前的 Claude Code（記憶、對話紀錄、設定）登記為設定檔「{name}」。",
    ),
    "already_initialized": ("Already set up; active profile is '{name}'.", "已經初始化過了；目前使用「{name}」。"),
    "note_original": ("your Claude Code before the first switch", "第一次切換前的 Claude Code（舊記憶）"),
    "note_clean": ("clean Claude Code", "乾淨的 Claude Code"),
    "note_clone": ("copy of '{src}'", "「{src}」的副本"),
    "note_import": ("imported from {file}", "從 {file} 匯入"),
    "created_profile": ("Created profile '{name}'.", "已建立設定檔「{name}」。"),
    "switched": ("Switched: '{old}' -> '{new}'. Start Claude Code / Claude Desktop again to use it.",
                 "切換完成：「{old}」→「{new}」。重新開啟 Claude Code／Claude Desktop 即可使用。"),
    "already_active": ("'{name}' is already the active profile.", "「{name}」已經是目前使用的設定檔。"),
    "no_such_profile": ("No profile named '{name}'.", "找不到設定檔「{name}」。"),
    "profile_folder_missing": (
        "The folder of profile '{name}' is missing ({path}); refusing to switch to an empty profile.",
        "設定檔「{name}」的資料夾不見了（{path}）；為避免切換到空白狀態，已停止。",
    ),
    "profile_exists": ("A profile named '{name}' already exists.", "已經有名為「{name}」的設定檔。"),
    "bad_name": (
        "Invalid profile name '{name}'. Use 1-64 letters, digits, CJK, '-', '_' or '.', not starting with '.' or '_'.",
        "設定檔名稱「{name}」不合法：請用 1～64 個字母、數字、中文、「-」「_」「.」，且不能以「.」或「_」開頭。",
    ),
    "journal_pending": (
        "A previous switch ({op}: '{src}' -> '{dst}') did not finish. Run `repair` first.",
        "上一次切換（{op}：「{src}」→「{dst}」）沒有完成，請先執行 `repair` 修復。",
    ),
    "no_journal": ("Nothing to repair: no unfinished switch.", "不需要修復：沒有未完成的切換。"),
    "repaired_forward": ("Finished the interrupted switch; active profile is '{name}'.",
                         "已完成中斷的切換，目前使用「{name}」。"),
    "repaired_back": ("Rolled back the interrupted switch; active profile is '{name}'.",
                      "已還原中斷的切換，目前使用「{name}」。"),
    "switch_failed_rolled_back": (
        "Switch failed and was rolled back; nothing changed. Reason: {err}",
        "切換失敗，已自動還原，沒有任何改變。原因：{err}",
    ),
    "switch_failed_stuck": (
        "Switch failed and could not be rolled back automatically. Reason: {err}\n"
        "No files were deleted. Close all Claude apps and run `repair`.",
        "切換失敗且無法自動還原。原因：{err}\n沒有任何檔案被刪除。請關閉所有 Claude 程式後執行 `repair`。",
    ),
    "claude_running": (
        "Claude is running. Quit it first so it is not writing to the files being switched:",
        "Claude 正在執行中。請先關閉，避免它在切換時寫入檔案：",
    ),
    "claude_running_hint": (
        "Exit Claude Code sessions (/exit), stop background sessions (claude daemon stop --any) and fully quit "
        "Claude Desktop (Cmd+Q / tray icon -> Quit; closing the window is not enough). Or pass --force.",
        "請結束 Claude Code 工作階段（輸入 /exit）、停止背景工作階段（claude daemon stop --any），"
        "並完全結束 Claude Desktop（Cmd+Q／系統匣圖示 → Quit，只關視窗不夠）。或加上 --force 強制切換。",
    ),
    "proc_desktop": ("Claude Desktop", "Claude Desktop"),
    "proc_cli": ("Claude Code", "Claude Code"),
    "proc_unknown": ("Claude (unknown kind)", "Claude（無法判斷類型）"),
    "proc_lock": ("Claude is writing its files right now (lock in use)", "Claude 正在寫入它的檔案（鎖定中）"),
    "started_inside_claude": (
        "This switcher was started from inside Claude (pid {pid}). Close Claude and run it from a normal terminal, "
        "or double-click the launcher, instead.",
        "這個切換器是從 Claude 裡面啟動的（pid {pid}）。請關閉 Claude，改從一般終端機執行，或直接雙擊啟動檔。",
    ),
    "marker_mismatch": (
        "The live Claude config belongs to profile '{profile}' of the store {store}, but this store ({our_store}) "
        "thinks '{active}' is live. Something else changed ~/.claude (another store, a restored backup?). "
        "Refusing to switch; check the paths, or pass --force.",
        "目前的 Claude 設定屬於倉庫 {store} 的設定檔「{profile}」，但這個倉庫（{our_store}）認為使用中的是「{active}」。"
        "可能有別的東西改過 ~/.claude（另一個倉庫、從備份還原？）。為安全起見不切換；請確認路徑，或加上 --force。",
    ),
    "other_store": (
        "~/.claude is already managed by another claude-switch store at {store}. Use --store {store}.",
        "~/.claude 已經由另一個 claude-switch 倉庫管理：{store}。請加上 --store {store}。",
    ),
    "store_python": (
        "This is the Microsoft Store Python, which hides folders it creates under AppData from other apps, "
        "so Claude Desktop would lose its session list. Install Python from https://www.python.org/downloads/ "
        "and run this again (the .bat launcher prefers it automatically).",
        "這是 Microsoft Store 版的 Python，它會把在 AppData 底下建立的資料夾藏起來，其他程式看不到，"
        "Claude Desktop 會因此找不到工作階段清單。請從 https://www.python.org/downloads/ 安裝 Python 後再執行"
        "（.bat 啟動檔會自動優先使用它）。",
    ),
    "ask_quit_desktop": ("Quit Claude Desktop now?", "要現在幫你關閉 Claude Desktop 嗎？"),
    "quitting_desktop": ("Asking Claude Desktop to quit...", "正在關閉 Claude Desktop…"),
    "desktop_still_running": ("Claude Desktop is still running; quit it from its menu / tray icon.",
                              "Claude Desktop 仍在執行，請從選單列或系統匣圖示結束它。"),
    "ask_retry": ("Press Enter to check again, 'f' to switch anyway, 'q' to cancel: ",
                  "按 Enter 重新檢查，輸入 f 強制切換，輸入 q 取消："),
    "ask_reopen_desktop": ("Open Claude Desktop again?", "要重新開啟 Claude Desktop 嗎？"),
    "cancelled": ("Cancelled.", "已取消。"),
    "dry_run_header": ("Dry run -- these moves would happen (nothing changed):",
                       "試跑模式——將會執行以下搬移（實際上沒有改變任何東西）："),
    "confirm_switch": ("Switch from '{old}' to '{new}'?", "要從「{old}」切換到「{new}」嗎？"),
    "confirm_delete": ("Move profile '{name}' ({size}) to the trash?", "要把設定檔「{name}」（{size}）移到垃圾桶嗎？"),
    "deleted": ("Moved '{name}' to {path}", "已把「{name}」移到 {path}"),
    "cannot_delete_active": ("Cannot delete the active profile '{name}'. Switch to another one first.",
                             "無法刪除正在使用的設定檔「{name}」，請先切換到其他設定檔。"),
    "renamed": ("Renamed '{old}' -> '{new}'.", "已重新命名：「{old}」→「{new}」。"),
    "exported": ("Exported '{name}' to {file} ({size}).", "已把「{name}」匯出到 {file}（{size}）。"),
    "export_secrets_note": (
        "Login tokens are never exported; API keys in claude.json were removed (use --include-secrets to keep).",
        "登入憑證不會被匯出；claude.json 內的 API 金鑰已移除（加上 --include-secrets 可保留）。",
    ),
    "imported": ("Imported {file} as profile '{name}'.", "已把 {file} 匯入為設定檔「{name}」。"),
    "bad_archive": ("Not a valid claude-switch archive: {why}", "不是有效的 claude-switch 備份檔：{why}"),
    "live_paths_changed": (
        "Claude's config location changed since setup:\n  was: {was}\n  now: {now}\n"
        "(Is CLAUDE_CONFIG_DIR set differently?) Refusing to switch; pass --force if this is intended.",
        "Claude 的設定位置和初始化時不同：\n  原本：{was}\n  現在：{now}\n"
        "（CLAUDE_CONFIG_DIR 環境變數是否不同？）為安全起見不切換；確定要這樣做請加 --force。",
    ),
    "config_dir_env_note": (
        "Note: CLAUDE_CONFIG_DIR is set. Claude Desktop does not see shell variables, so it still uses ~/.claude.",
        "注意：已設定 CLAUDE_CONFIG_DIR。Claude Desktop 讀不到終端機的環境變數，它仍會使用 ~/.claude。",
    ),
    "cross_device": (
        "The profile store ({store}) is on a different disk than {live}; switching would copy instead of rename. "
        "Use --store on the same disk.",
        "設定檔倉庫（{store}）和 {live} 不在同一個磁碟，切換會變成複製而不是改名。請用 --store 指定同一磁碟上的位置。",
    ),
    "store_inside_config": ("The profile store must not be inside {live}.", "設定檔倉庫不能放在 {live} 裡面。"),
    "busy": ("Another claude-switch is running (pid {pid}). Try again in a moment.",
             "另一個 claude-switch 正在執行（pid {pid}），請稍後再試。"),
    "pinned_skipped": ("Kept shared item in place (not part of any profile): {items}",
                       "以下共用項目保持原位（不屬於任何設定檔）：{items}"),
    "conflicts_moved": ("Unexpected leftovers were moved aside (nothing deleted): {path}",
                        "發現多餘的殘留檔案，已移到旁邊保存（沒有刪除）：{path}"),
    "shared_login_note": (
        "Login is shared by all profiles, so the clean profile is still signed in.",
        "登入狀態由所有設定檔共用，所以乾淨的設定檔仍是登入狀態。",
    ),
    "project_memory_note": (
        "Project files such as <repo>/CLAUDE.md and <repo>/.claude/ live in your repositories and are not switched. "
        "Run `doctor` to list them.",
        "專案資料夾裡的 CLAUDE.md、.claude/ 等檔案屬於各個專案，不會被切換。執行 `doctor` 可以列出它們。",
    ),
    "scan_header": ("Project-level memory/config files outside the profile (not switched):",
                    "設定檔以外、在專案資料夾內的記憶／設定檔（不會被切換）："),
    "scan_none": ("No project-level CLAUDE.md / CLAUDE.local.md / .claude found for known projects.",
                  "已知專案中沒有找到 CLAUDE.md／CLAUDE.local.md／.claude。"),
    "trash_empty": ("Trash is empty.", "垃圾桶是空的。"),
    "trash_list": ("Trash ({size}):", "垃圾桶（{size}）："),
    "confirm_empty_trash": ("Permanently delete everything in the trash ({size})?", "要永久刪除垃圾桶裡的所有東西（{size}）嗎？"),
    "trash_emptied": ("Trash emptied.", "已清空垃圾桶。"),
    "reset_done": ("Profile '{name}' was reset to a clean state; its old contents are in {path}",
                   "設定檔「{name}」已重置為乾淨狀態；舊內容在 {path}"),
    "yes_no": (" [y/N] ", " [y/N] "),
    "yes_no_default_yes": (" [Y/n] ", " [Y/n] "),
    "press_enter": ("Press Enter to continue...", "按 Enter 繼續…"),
    "menu_choose": ("Choose: ", "請選擇："),
    "menu_to_clean": ("Switch to a clean Claude Code", "切換到乾淨的 Claude Code"),
    "menu_reset_clean": ("Reset '{name}' to clean again (old contents go to trash)", "把「{name}」重新清空（舊內容移到垃圾桶）"),
    "menu_back": ("Switch back to '{name}'", "切回「{name}」"),
    "menu_switch_other": ("Switch to another profile...", "切換到其他設定檔…"),
    "menu_new": ("Create a new clean profile...", "建立新的乾淨設定檔…"),
    "menu_clone": ("Duplicate a profile...", "複製一個設定檔…"),
    "menu_export": ("Export (back up) a profile to a zip file...", "匯出（備份）設定檔成 zip…"),
    "menu_import": ("Import a profile from a zip file...", "從 zip 匯入設定檔…"),
    "menu_rename": ("Rename a profile...", "重新命名設定檔…"),
    "menu_delete": ("Delete a profile (to trash)...", "刪除設定檔（移到垃圾桶）…"),
    "menu_scan": ("Check what is NOT switched (project CLAUDE.md, logins, Desktop MCP)",
                  "檢查哪些東西「不會」被切換（專案 CLAUDE.md、登入方式、Desktop MCP）"),
    "menu_where": ("Show paths", "顯示路徑"),
    "menu_repair": ("Repair an unfinished switch", "修復未完成的切換"),
    "menu_quit": ("Quit", "離開"),
    "ask_profile": ("Profile number or name: ", "設定檔編號或名稱："),
    "ask_new_name": ("New profile name: ", "新設定檔名稱："),
    "ask_src_name": ("Profile to duplicate (number or name): ", "要複製的設定檔（編號或名稱）："),
    "ask_zip_path": ("Path of the zip file: ", "zip 檔路徑："),
    "ask_switch_now": ("Switch to it now?", "要現在切換過去嗎？"),
    "invalid_choice": ("Invalid choice.", "無效的選項。"),
    "where_config_dir": ("Claude config dir", "Claude 設定資料夾"),
    "where_global": ("Claude global config", "Claude 全域設定檔"),
    "where_store": ("Profile store", "設定檔倉庫"),
    "where_desktop_sessions": ("Claude Desktop Code-session list", "Claude Desktop 的 Code 工作階段清單"),
    "repair_choose": (
        "Unfinished switch '{src}' -> '{dst}' ({done}/{total} steps done). [f]inish it or [r]oll it back? ",
        "未完成的切換「{src}」→「{dst}」（已完成 {done}/{total} 步）。要 [f] 完成它 還是 [r] 還原？",
    ),
    "python_too_old": ("Python 3.8 or newer is required.", "需要 Python 3.8 以上版本。"),
    "error": ("Error: {err}", "錯誤：{err}"),
    "need_name": ("Please give a profile name.", "請提供設定檔名稱。"),
    "no_previous": ("No previous profile to toggle back to.", "沒有可以切回的上一個設定檔。"),
    "copy_missing": ("Nothing named '{item}' in the current Claude config; skipped.", "目前的 Claude 設定裡沒有「{item}」，已略過。"),
    "copied_items": ("Copied into the new profile: {items}", "已複製到新設定檔：{items}"),
    "no_account_sync_done": (
        "The new profile will not pull skills, plugins or connectors from your claude.ai account.",
        "新設定檔不會從你的 claude.ai 帳號同步技能、外掛或連接器。",
    ),
    "retention_warning": (
        "Heads-up: {n} conversation transcript(s) here are older than {days} days. Claude Code deletes transcripts "
        "older than its cleanupPeriodDays setting when it starts. To keep them, run `export` first or add "
        "\"cleanupPeriodDays\": 3650 to settings.json before starting Claude.",
        "提醒：這個設定檔有 {n} 份對話紀錄超過 {days} 天。Claude Code 啟動時會刪除超過 cleanupPeriodDays 天的對話紀錄。"
        "想保留的話，請先執行 `export` 備份，或在啟動 Claude 前於 settings.json 加入 \"cleanupPeriodDays\": 3650。",
    ),
    "doctor_auto_memory": (
        "! Auto memory is stored in {dir} (autoMemoryDirectory setting). That folder is outside the profile and is not switched.",
        "! 自動記憶存放在 {dir}（autoMemoryDirectory 設定），它在設定檔之外，不會被切換。",
    ),
    "doctor_settings_config_dir": (
        "! settings.json sets CLAUDE_CONFIG_DIR={dir}; Claude Code will use that folder instead of the switched one.",
        "! settings.json 設定了 CLAUDE_CONFIG_DIR={dir}；Claude Code 會改用那個資料夾，而不是被切換的這個。",
    ),
    "doctor_auth_env": (
        "! {var} is set in this shell: the terminal CLI uses it in every profile.",
        "! 這個終端機設定了 {var}：終端機版 CLI 在每個設定檔都會使用它。",
    ),
    "doctor_desktop_mcp": (
        "Claude Desktop has its own MCP servers in {file}; they are not part of a profile.",
        "Claude Desktop 在 {file} 有自己的 MCP 伺服器設定，它不屬於設定檔。",
    ),
    "doctor_settings_provider": (
        "! This profile's settings.json sets {vars} (an API provider / key). A new clean profile will not have it; "
        "create it with --copy settings.json to keep it.",
        "! 這個設定檔的 settings.json 設定了 {vars}（API 供應商／金鑰）。新的乾淨設定檔不會有這些；"
        "想保留的話，建立時加上 --copy settings.json。",
    ),
    "doctor_plugin_dir": (
        "! CLAUDE_CODE_PLUGIN_CACHE_DIR={dir}: plugins are stored there and are not switched.",
        "! CLAUDE_CODE_PLUGIN_CACHE_DIR={dir}：外掛存在那裡，不會被切換。",
    ),
    "doctor_cc_switch": (
        "cc-switch is installed: it writes its provider settings into whichever profile is active.",
        "偵測到 cc-switch：它會把供應商設定寫進「目前使用中」的設定檔。",
    ),
    "doctor_anthropic_profiles": (
        "Anthropic profiles in {dir} can also sign Claude Code in, in every profile.",
        "{dir} 裡的 Anthropic 設定也可能讓 Claude Code 登入，對所有設定檔都有效。",
    ),
    "doctor_desktop_login": (
        "Claude Desktop signs in on its own; switching profiles does not change its account.",
        "Claude Desktop 有自己的登入；切換設定檔不會改變它登入的帳號。",
    ),
    "doctor_cowork": (
        "Cowork sessions and Cowork memory ({dir}) belong to Claude Desktop and are not switched.",
        "Cowork 的工作階段與記憶（{dir}）屬於 Claude Desktop，不會被切換。",
    ),
    "doctor_keychain": (
        "macOS: your login (and MCP / plugin secrets) is in the Keychain and is shared by all profiles.",
        "macOS：登入資訊（以及 MCP／外掛的密鑰）存在「鑰匙圈」，由所有設定檔共用。",
    ),
    "ask_keep_api": (
        "Your current settings route Claude Code through an API provider ({keys}). Keep these in the clean profile? "
        "(Without them it may not be able to connect.)",
        "你目前的設定讓 Claude Code 透過 API 供應商連線（{keys}）。要把這些保留到乾淨版嗎？（沒有它們可能會無法連線）",
    ),
    "api_kept": ("Kept API-provider settings in the new profile: {keys}", "已把 API 供應商設定保留到新設定檔：{keys}"),
    "ask_keep_history": (
        "Set cleanupPeriodDays to 3650 in this profile so Claude Code keeps those old conversations?",
        "要在這個設定檔把 cleanupPeriodDays 設為 3650，讓 Claude Code 保留這些舊對話嗎？",
    ),
    "kept_history": ("Done: settings.json now has \"cleanupPeriodDays\": 3650.", "完成：settings.json 已設定 \"cleanupPeriodDays\": 3650。"),
    "doctor_config_dir_literal": (
        "! CLAUDE_CONFIG_DIR is {value!r}: Claude Code does not expand '~' and treats an empty value as the current "
        "folder. Use a full path, or unset it.",
        "! CLAUDE_CONFIG_DIR 是 {value!r}：Claude Code 不會展開「~」，空值則會被當成目前所在的資料夾。請改用完整路徑，或取消設定。",
    ),
    "doctor_relocated": (
        "! {var}={dir}: that data lives outside the profile and is not switched.",
        "! {var}={dir}：那些資料在設定檔之外，不會被切換。",
    ),
    "ask_no_account_sync": (
        "Also block skills/plugins/connectors that sync from your claude.ai account?",
        "也要阻止從 claude.ai 帳號同步過來的技能／外掛／連接器嗎？",
    ),
}

_LANG: Optional[str] = None


def _detect_lang() -> str:
    forced = os.environ.get("CLAUDE_SWITCH_LANG", "").strip().lower()
    if forced.startswith("zh"):
        return "zh"
    if forced.startswith("en"):
        return "en"
    candidates = [os.environ.get(k, "") for k in ("LC_ALL", "LC_MESSAGES", "LANG", "LANGUAGE")]
    for c in candidates:
        c = c.lower()
        if c and c not in ("c", "posix", "c.utf-8", "c.utf8"):
            return "zh" if c.startswith("zh") else "en"
    if IS_MAC:
        try:
            out = subprocess.run(
                ["defaults", "read", "-g", "AppleLanguages"],
                capture_output=True, text=True, timeout=5,
            ).stdout
            first = re.search(r'"?([A-Za-z]{2})[-_A-Za-z]*"?', out.replace("(", " "))
            if first:
                return "zh" if first.group(1).lower() == "zh" else "en"
        except Exception:
            pass
    if IS_WINDOWS:
        try:
            import ctypes

            langid = ctypes.windll.kernel32.GetUserDefaultUILanguage()  # type: ignore[attr-defined]
            return "zh" if (langid & 0x3FF) == 0x04 else "en"
        except Exception:
            pass
    try:
        loc = (locale.getlocale()[0] or "").lower()
        if loc.startswith("zh") or "chinese" in loc:
            return "zh"
    except Exception:
        pass
    return "en"


def set_lang(lang: Optional[str]) -> None:
    global _LANG
    if lang:
        _LANG = "zh" if lang.lower().startswith("zh") else "en"
    else:
        _LANG = _detect_lang()


def t(key: str, **kw: Any) -> str:
    if _LANG is None:
        set_lang(None)
    en, zh = MESSAGES[key]
    text = zh if _LANG == "zh" else en
    return text.format(**kw) if kw else text


def say(text: str = "") -> None:
    print(text, flush=True)


# --------------------------------------------------------------------------
# Errors
# --------------------------------------------------------------------------


class SwitchError(Exception):
    """User-facing error; the message is already translated."""

    exit_code = 1


class ClaudeRunning(SwitchError):
    exit_code = 3

    def __init__(self, procs: List["Proc"]):
        self.procs = procs
        lines = [t("claude_running")]
        lines += ["  - " + p.describe() for p in procs]
        lines.append(t("claude_running_hint"))
        if any(p.pid > 0 for p in procs):
            try:
                ancestors = set(ancestor_pids())
            except Exception:
                ancestors = set()
            inside = [p for p in procs if p.pid in ancestors]
            if inside:
                lines.append(t("started_inside_claude", pid=inside[0].pid))
        super().__init__("\n".join(lines))


class MoveConflict(OSError):
    pass


# --------------------------------------------------------------------------
# Small helpers
# --------------------------------------------------------------------------


def unique_path(p: Path) -> Path:
    """p, or p.2, p.3, ... -- the first one that does not exist yet."""
    cand, n = p, 2
    while lexists(cand):
        cand = p.with_name("%s.%d" % (p.name, n))
        n += 1
    return cand


def now_iso() -> str:
    return _dt.datetime.now().astimezone().replace(microsecond=0).isoformat()


def stamp() -> str:
    return _dt.datetime.now().strftime("%Y%m%d-%H%M%S")


def lexists(p: Path) -> bool:
    return os.path.lexists(str(p))


def _long(p: Path) -> str:
    """Path string usable for deep trees on Windows (MAX_PATH)."""
    s = os.path.abspath(str(p))
    if IS_WINDOWS and not s.startswith("\\\\?\\"):
        s = "\\\\?\\UNC\\" + s[2:] if s.startswith("\\\\") else "\\\\?\\" + s
    return s


def human_size(n: int) -> str:
    size = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024 or unit == "TB":
            return ("%d %s" % (size, unit)) if unit == "B" else ("%.1f %s" % (size, unit))
        size /= 1024
    return "%d B" % n


def tree_size(p: Path) -> int:
    """Size of a file or directory tree, never following symlinks."""
    total = 0
    try:
        st = os.lstat(_long(p))
    except OSError:
        return 0
    if not stat.S_ISDIR(st.st_mode):
        return st.st_size
    stack = [_long(p)]
    while stack:
        cur = stack.pop()
        try:
            with os.scandir(cur) as it:
                for entry in it:
                    try:
                        if entry.is_dir(follow_symlinks=False):
                            stack.append(entry.path)
                        else:
                            total += entry.stat(follow_symlinks=False).st_size
                    except OSError:
                        pass
        except OSError:
            pass
    return total


def _rm_onerror(func: Callable, path: str, _exc: Any) -> None:
    # Windows: clear the read-only bit and retry.
    try:
        os.chmod(path, stat.S_IWRITE | stat.S_IREAD | stat.S_IEXEC)
        func(path)
    except Exception:
        pass


def rmtree(p: Path) -> None:
    """Delete a file or tree.  Only used for our own temp folders and `trash --empty`."""
    if not lexists(p):
        return
    if os.path.islink(str(p)) or not os.path.isdir(str(p)):
        if not os.path.islink(str(p)):
            try:
                os.chmod(str(p), stat.S_IWRITE | stat.S_IREAD)
            except OSError:
                pass
        os.unlink(str(p))
        return
    if sys.version_info >= (3, 12):
        shutil.rmtree(_long(p), onexc=_rm_onerror)  # type: ignore[call-arg]
    else:
        shutil.rmtree(_long(p), onerror=_rm_onerror)


def only_empty_dirs(p: Path) -> bool:
    """True if p is missing or contains nothing but (nested) empty folders."""
    if not lexists(p):
        return True
    if os.path.islink(str(p)) or not os.path.isdir(str(p)):
        return False
    for _root, _dirs, files in os.walk(_long(p)):
        if files:
            return False
    return True


def copy_any(src: Path, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    if os.path.islink(str(src)):
        os.symlink(os.readlink(str(src)), str(dst))
    elif os.path.isdir(str(src)):
        shutil.copytree(_long(src), _long(dst), symlinks=True)
    else:
        shutil.copy2(_long(src), _long(dst))


_NOREPLACE: Dict[str, Any] = {}


def _rename_noreplace(src: str, dst: str) -> None:
    """rename(2) that fails instead of replacing an existing destination.

    Plain POSIX rename() silently replaces an existing file, or an existing
    *empty* directory.  Linux has renameat2(RENAME_NOREPLACE) and macOS has
    renamex_np(RENAME_EXCL); use them when available.  Windows os.rename never
    replaces.  Everywhere else the caller's lexists() check right before is
    the guard."""
    if IS_WINDOWS:
        os.rename(src, dst)
        return
    if "fn" not in _NOREPLACE:
        _NOREPLACE["fn"] = None
        try:
            import ctypes

            libc = ctypes.CDLL(None, use_errno=True)
            if IS_MAC and hasattr(libc, "renamex_np"):
                f = libc.renamex_np
                f.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint]
                _NOREPLACE["fn"] = lambda a, b: f(a, b, 0x00000004)  # RENAME_EXCL
            elif sys.platform.startswith("linux") and hasattr(libc, "renameat2"):
                f = libc.renameat2
                f.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
                _NOREPLACE["fn"] = lambda a, b: f(-100, a, -100, b, 1)  # AT_FDCWD, RENAME_NOREPLACE
            _NOREPLACE["ctypes"] = ctypes
        except Exception:
            _NOREPLACE["fn"] = None
    fn = _NOREPLACE["fn"]
    if fn is not None:
        if fn(os.fsencode(src), os.fsencode(dst)) == 0:
            return
        err = _NOREPLACE["ctypes"].get_errno()
        if err not in (errno.EINVAL, errno.ENOSYS, errno.ENOTSUP, getattr(errno, "EOPNOTSUPP", -1)):
            raise OSError(err, os.strerror(err), src, None, dst)
        # the filesystem does not support it: fall through
    os.rename(src, dst)


def _rename_with_retry(src: Path, dst: Path) -> None:
    """Rename, retried on Windows where antivirus and the search indexer open
    freshly written files for a moment (ACCESS_DENIED 5, SHARING_VIOLATION 32,
    LOCK_VIOLATION 33)."""
    attempts = 10 if IS_WINDOWS else 1
    for i in range(attempts):
        try:
            _rename_noreplace(str(src), str(dst))
            return
        except PermissionError as e:
            if i == attempts - 1 or getattr(e, "winerror", None) not in (5, 32, 33):
                raise
            time.sleep(min(0.05 * (2 ** i), 1.0))


def move(src: Path, dst: Path) -> None:
    """Rename src to dst on the same disk.  Never overwrites, never copies,
    never deletes."""
    if not lexists(src):
        raise FileNotFoundError(errno.ENOENT, "missing", str(src))
    if lexists(dst):
        raise MoveConflict(errno.EEXIST, "already exists", str(dst))
    dst.parent.mkdir(parents=True, exist_ok=True)
    _rename_with_retry(src, dst)


def write_json_atomic(path: Path, data: Any, mode: Optional[int] = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp-%d" % os.getpid())
    with open(str(tmp), "w", encoding="utf-8", newline="\n") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
        f.write("\n")
        f.flush()
        os.fsync(f.fileno())
    if mode is not None:
        try:
            os.chmod(str(tmp), mode)
        except OSError:
            pass
    attempts = 10 if IS_WINDOWS else 1
    for i in range(attempts):
        try:
            os.replace(str(tmp), str(path))
            return
        except PermissionError as e:
            if i == attempts - 1 or getattr(e, "winerror", None) not in (5, 32, 33):
                raise
            time.sleep(min(0.05 * (2 ** i), 1.0))


def read_json(path: Path) -> Any:
    with open(str(path), "r", encoding="utf-8") as f:
        return json.load(f)


def is_within(child: Path, parent: Path) -> bool:
    try:
        c = os.path.normcase(os.path.realpath(str(child)))
        p = os.path.normcase(os.path.realpath(str(parent)))
    except OSError:
        return False
    return c == p or c.startswith(p.rstrip(os.sep) + os.sep)


def same_device(a: Path, b: Path) -> bool:
    def dev(p: Path) -> Optional[int]:
        cur = p
        while not cur.exists():
            if cur.parent == cur:
                return None
            cur = cur.parent
        return os.stat(str(cur)).st_dev

    da, db = dev(a), dev(b)
    return da is None or db is None or da == db


def same_name(a: str, b: str) -> bool:
    """Profile names compare like folder names on macOS/Windows disks."""
    return norm_name(a).casefold() == norm_name(b).casefold()


def norm_name(name: str) -> str:
    return unicodedata.normalize("NFC", name or "")


def validate_name(name: str) -> str:
    name = norm_name(name).strip()
    ok = (
        0 < len(name) <= 64
        and not name.startswith((".", "_"))
        and not name.endswith((".", " "))
        and not re.search(r'[<>:"/\\|?*\x00-\x1f]', name)
        and name.split(".")[0].upper() not in _WIN_RESERVED
    )
    if not ok:
        raise SwitchError(t("bad_name", name=name))
    return name


def ask_yes_no(question: str, default: bool = False) -> bool:
    suffix = t("yes_no_default_yes") if default else t("yes_no")
    try:
        ans = input(question + suffix).strip().lower()
    except EOFError:
        return default
    if not ans:
        return default
    return ans in ("y", "yes", "是", "好", "對")


# --------------------------------------------------------------------------
# Where Claude keeps things
# --------------------------------------------------------------------------


class Paths:
    """Resolves the live Claude locations and the profile store."""

    def __init__(self, store: Optional[str] = None):
        self.home = Path.home()
        env_dir = os.environ.get("CLAUDE_CONFIG_DIR", "").strip()
        self.config_dir_from_env = bool(env_dir)
        self.config_dir = Path(os.path.expanduser(env_dir)).absolute() if env_dir else self.home / ".claude"
        base = self.config_dir if env_dir else self.home
        # (live file, name inside a profile folder); the first is ~/.claude.json
        self.global_files: List[Tuple[Path, Path]] = [
            (base / (".claude%s.json" % sfx), Path("claude%s.json" % sfx)) for sfx in GLOBAL_SUFFIXES
        ]
        self.global_config = self.global_files[0][0]
        # Written next to ~/.claude.json by Claude Desktop; holds the old account,
        # MCP servers and project list, so it belongs to the profile too.
        self.extra_files: List[Tuple[Path, Path]] = [
            (base / ".claude.json.backup", Path("claude.json.backup")),
        ]
        self.lock_dirs = [
            base / ".claude.json.lock",
            self.home / ".claude.lock",
            self.config_dir / ".oauth_refresh.lock",
            self.config_dir / ".design_oauth_refresh.lock",
            self.config_dir / "history.jsonl.lock",
        ]
        # Old versions kept the global config at <config dir>/.config.json.
        # Claude Code still prefers that file whenever it exists.  It lives in
        # the config dir, so it is switched along with everything else there.
        self.legacy_config = self.config_dir / ".config.json"
        store_env = os.environ.get("CLAUDE_SWITCH_HOME", "").strip()
        store = store or store_env
        self.store = Path(os.path.expanduser(store)).absolute() if store else self.home / ".claude-profiles"

    # -- store layout --------------------------------------------------
    @property
    def state_file(self) -> Path:
        return self.store / STATE_FILE

    @property
    def profiles_dir(self) -> Path:
        return self.store / PROFILES_DIR

    def profile_dir(self, name: str) -> Path:
        return self.profiles_dir / name

    def live_signature(self) -> Dict[str, str]:
        return {"config_dir": str(self.config_dir), "global_config": str(self.global_config)}

    def effective_config(self) -> Path:
        """The global config file Claude Code actually reads right now."""
        return self.legacy_config if lexists(self.legacy_config) else self.global_config

    def desktop_app_dirs(self) -> List[Tuple[str, Path]]:
        """(key, folder) for every place Claude Desktop may keep its app data."""
        if IS_MAC:
            return [("mac", self.home / "Library" / "Application Support" / "Claude")]
        if IS_WINDOWS:
            out: List[Tuple[str, Path]] = []
            appdata = os.environ.get("APPDATA")
            if appdata:
                out.append(("win", Path(appdata) / "Claude"))
            local = os.environ.get("LOCALAPPDATA")
            if local:
                # MSIX installs are virtualized under Packages\Claude_<publisher id>.
                try:
                    fams = sorted(d for d in (Path(local) / "Packages").glob("Claude_*") if d.is_dir())
                except OSError:
                    fams = []
                for d in fams:
                    out.append(("msix-" + d.name, d / "LocalCache" / "Roaming" / "Claude"))
            return out
        xdg = os.environ.get("XDG_CONFIG_HOME", "").strip()
        return [("linux", (Path(xdg) if xdg else self.home / ".config") / "Claude")]

    def desktop_dirs(self) -> List[Path]:
        return [d for _k, d in self.desktop_app_dirs()]

    def unit_slots(self) -> List[Tuple[Path, Path]]:
        """Things switched as a single unit: (live path, path inside a profile)."""
        slots = list(self.global_files) + list(self.extra_files)
        for name in HOME_MEMORY_FILES:
            slots.append((self.home / name, Path(SLOT_HOME) / name))
        for key, d in self.desktop_app_dirs():
            slots.append((d / DESKTOP_SESSIONS, Path(SLOT_DESKTOP) / key))
        return slots


# --------------------------------------------------------------------------
# Running-process detection
# --------------------------------------------------------------------------


class Proc:
    __slots__ = ("pid", "kind", "name", "exe")

    def __init__(self, pid: int, kind: str, name: str, exe: str = ""):
        self.pid, self.kind, self.name, self.exe = pid, kind, name, exe

    def describe(self) -> str:
        if self.kind == "lock":
            return "%s: %s" % (t("proc_lock"), self.exe)
        label = {"desktop": t("proc_desktop"), "cli": t("proc_cli")}.get(self.kind, t("proc_unknown"))
        return "%s (pid %d) %s" % (label, self.pid, self.exe or self.name)

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return "Proc(%d, %s, %r)" % (self.pid, self.kind, self.exe or self.name)


_NODE_CLAUDE = re.compile(r"@anthropic-ai/claude-code|claude-code/cli\.m?js|^\S*/claude(\s|$)")
_VERSION_EXE = re.compile(r"/\.local/share/claude/versions/[^/]+$")


def classify_posix(comm: str, args: str) -> Optional[str]:
    """Classify a process by its executable (`ps -o comm=`) and by the name
    it was started as (argv[0], the first word of `ps -o args=`).

    Only those two decide -- never words further along the command line -- so
    a shell that merely mentions a claude path is not mistaken for Claude.
    Both are needed: macOS reports the resolved executable in comm (e.g.
    .../versions/2.1.3), while argv[0] / process.title says "claude"."""
    comm = (comm or "").strip()
    args = (args or "").strip()
    argv0 = args.split(" ")[0]
    if "/Claude.app/Contents/" in comm or "/Claude.app/Contents/" in args.split(" -")[0]:
        return "desktop"
    for exe in (comm, argv0):
        if not exe:
            continue
        base = exe.rsplit("/", 1)[-1]
        if base == "claude-desktop":  # Linux build of Claude Desktop
            return "desktop"
        if base == "claude" or _VERSION_EXE.search(exe):
            return "cli"
    for exe in (comm, argv0):
        if exe.rsplit("/", 1)[-1] in ("node", "bun", "nodejs"):
            rest = args.split(" ", 1)[1] if " " in args else ""
            if _NODE_CLAUDE.search(rest):
                return "cli"
    return None


def _pid_table(output: str) -> Dict[int, str]:
    table: Dict[int, str] = {}
    for line in (output or "").splitlines():
        m = re.match(r"\s*(\d+)\s?(.*)$", line)
        if m:
            table[int(m.group(1))] = m.group(2).strip()
    return table


def parse_ps(comm_output: str, args_output: str, own_pid: int) -> List[Proc]:
    """Combine `ps -A -o pid= -o comm=` and `ps -A -o pid= -o args=` output."""
    comms, argss = _pid_table(comm_output), _pid_table(args_output)
    procs = []
    for pid in sorted(set(comms) | set(argss)):
        if pid == own_pid:
            continue
        comm, args = comms.get(pid, ""), argss.get(pid, "")
        kind = classify_posix(comm, args)
        if kind:
            name = (comm or args.split(" ")[0]).rsplit("/", 1)[-1]
            procs.append(Proc(pid, kind, name, (args or comm)[:160]))
    return procs


def classify_windows(name: str, exe: str, cmd: str) -> Optional[str]:
    n = (name or "").lower()
    e = (exe or "").lower().replace("/", "\\")
    c = (cmd or "").lower().replace("/", "\\")
    if n == "claude.exe":
        if "\\anthropicclaude\\" in e or "\\windowsapps\\" in e or "\\programs\\claude\\" in e:
            return "desktop"
        if not e:
            return "unknown"
        return "cli"
    if n in ("node.exe", "bun.exe") and ("@anthropic-ai\\claude-code" in c or "claude-code\\cli." in c):
        return "cli"
    return None


def parse_windows_cim(json_text: str, own_pid: int) -> List[Proc]:
    data = json.loads(json_text or "[]")
    if isinstance(data, dict):
        data = [data]
    procs = []
    for d in data or []:
        pid = int(d.get("ProcessId") or 0)
        if pid == own_pid:
            continue
        kind = classify_windows(d.get("Name") or "", d.get("ExecutablePath") or "", d.get("CommandLine") or "")
        if kind:
            procs.append(Proc(pid, kind, d.get("Name") or "", d.get("ExecutablePath") or ""))
    return procs


def parse_tasklist(csv_text: str, own_pid: int) -> List[Proc]:
    procs = []
    for line in csv_text.splitlines():
        cols = [c.strip('"') for c in line.split('","')]
        if len(cols) < 2 or not cols[1].strip('"').isdigit():
            continue
        name, pid = cols[0].strip('"'), int(cols[1].strip('"'))
        if pid != own_pid and name.lower() == "claude.exe":
            procs.append(Proc(pid, "unknown", name))
    return procs


def _run(cmd: List[str], timeout: float = 20) -> Optional[str]:
    try:
        kw: Dict[str, Any] = {}
        if IS_WINDOWS:
            kw["creationflags"] = 0x08000000  # CREATE_NO_WINDOW
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, **kw)
        if res.returncode == 0:
            return res.stdout
    except Exception:
        pass
    return None


def pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if IS_WINDOWS:
        try:
            import ctypes

            k32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
            h = k32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
            if not h:
                return False
            code = ctypes.c_ulong()
            k32.GetExitCodeProcess(h, ctypes.byref(code))
            k32.CloseHandle(h)
            return code.value == 259  # STILL_ACTIVE
        except Exception:
            return True
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def session_processes(config_dir: Path) -> List[Proc]:
    """Claude Code writes <config dir>/sessions/<pid>.json while a session
    runs.  Used when the process table cannot be read."""
    procs = []
    sdir = config_dir / "sessions"
    if not sdir.is_dir():
        return procs
    for f in sorted(sdir.glob("*.json")):
        try:
            info = read_json(f)
            pid = int(info.get("pid") or f.stem)
        except (OSError, ValueError, TypeError, AttributeError):
            continue
        if pid == os.getpid() or not pid_alive(pid):
            continue
        where_ = "%s %s" % (info.get("entrypoint") or "", info.get("cwd") or "")
        procs.append(Proc(pid, "cli", "claude", where_.strip()))
    return procs


def lock_holders(paths: "Paths") -> List[Proc]:
    """Claude Code's lock directories, when something touched them just now."""
    out = []
    now = time.time()
    for d in paths.lock_dirs:
        try:
            age = now - os.stat(str(d)).st_mtime
        except OSError:
            continue
        if age < LOCK_DIRS_FRESH_SECONDS:
            out.append(Proc(0, "lock", d.name, str(d)))
    return out


def find_claude_processes(paths: Optional["Paths"] = None) -> List[Proc]:
    """Best-effort list of running Claude Code / Claude Desktop processes."""
    procs = scan_processes()
    if procs is None:
        procs = session_processes(paths.config_dir) if paths else []
    if paths:
        procs += lock_holders(paths)
    return procs


def ancestor_pids() -> List[int]:
    """PIDs of this process's parents, grandparents, ..."""
    parents: Dict[int, int] = {}
    if IS_WINDOWS:
        out = _run(["powershell", "-NoProfile", "-NonInteractive", "-Command",
                    "Get-CimInstance Win32_Process | Select-Object ProcessId,ParentProcessId | "
                    "ConvertTo-Json -Compress"], timeout=30)
        try:
            data = json.loads(out or "[]")
            for d in data if isinstance(data, list) else [data]:
                parents[int(d.get("ProcessId") or 0)] = int(d.get("ParentProcessId") or 0)
        except (ValueError, TypeError, AttributeError):
            return []
    else:
        for line in (_run(["ps", "-A", "-o", "pid=", "-o", "ppid="]) or "").splitlines():
            parts = line.split()
            if len(parts) == 2 and parts[0].isdigit() and parts[1].isdigit():
                parents[int(parts[0])] = int(parts[1])
    chain, pid = [], os.getpid()
    while pid in parents and parents[pid] not in chain and parents[pid] > 1 and len(chain) < 64:
        pid = parents[pid]
        chain.append(pid)
    return chain


def scan_processes() -> Optional[List[Proc]]:
    """Claude processes from the OS process table; None if it can't be read."""
    own = os.getpid()
    if IS_WINDOWS:
        ps = (
            "Get-CimInstance Win32_Process | "
            "Where-Object { $_.Name -in @('claude.exe','node.exe','bun.exe') } | "
            "Select-Object ProcessId,Name,ExecutablePath,CommandLine | ConvertTo-Json -Compress"
        )
        out = _run(["powershell", "-NoProfile", "-NonInteractive", "-Command", ps], timeout=30)
        if out is not None:
            try:
                return parse_windows_cim(out.strip() or "[]", own)
            except ValueError:
                pass
        out = _run(["tasklist", "/FO", "CSV", "/NH"])
        return parse_tasklist(out, own) if out is not None else None
    comm_out = _run(["ps", "-A", "-o", "pid=", "-o", "comm="])
    args_out = _run(["ps", "-A", "-o", "pid=", "-o", "args="])
    if (comm_out is None or args_out is None) and Path("/proc").is_dir():
        comm_lines, args_lines = [], []
        for d in Path("/proc").iterdir():
            if d.name.isdigit():
                try:
                    exe = os.readlink(str(d / "exe"))
                except OSError:
                    exe = ""
                try:
                    raw = (d / "cmdline").read_bytes().replace(b"\0", b" ").decode("utf-8", "replace")
                except OSError:
                    continue
                comm_lines.append("%s %s" % (d.name, exe))
                args_lines.append("%s %s" % (d.name, raw.strip()))
        comm_out, args_out = "\n".join(comm_lines), "\n".join(args_lines)
    if comm_out is None or args_out is None:
        return None
    return parse_ps(comm_out, args_out, own)


def quit_desktop(procs: List[Proc], wait: float = 20.0) -> bool:
    """Politely ask Claude Desktop to quit; True once it is gone."""
    if IS_MAC:
        _run(["osascript", "-e", 'if application "Claude" is running then tell application "Claude" to quit'],
             timeout=15)
    elif IS_WINDOWS:
        for p in procs:
            if p.kind == "desktop":
                _run(["taskkill", "/PID", str(p.pid)], timeout=15)
    else:
        return False
    deadline = time.time() + wait
    while time.time() < deadline:
        if not [p for p in (scan_processes() or []) if p.kind == "desktop"]:
            return True
        time.sleep(1)
    return False


def open_desktop(procs: List[Proc]) -> None:
    try:
        if IS_MAC:
            subprocess.Popen(["open", "-a", "Claude"])
        elif IS_WINDOWS:
            local = os.environ.get("LOCALAPPDATA", "")
            stub = Path(local) / "AnthropicClaude" / "claude.exe"
            exe = str(stub) if stub.exists() else next((p.exe for p in procs if p.kind == "desktop" and p.exe), "")
            if exe:
                subprocess.Popen([exe], creationflags=0x00000008)  # DETACHED_PROCESS
    except Exception:
        pass


# --------------------------------------------------------------------------
# State (switcher.json) and the store lock
# --------------------------------------------------------------------------


class StoreLock:
    def __init__(self, paths: Paths):
        self.path = paths.store / LOCK_FILE
        self.fd: Optional[int] = None

    def __enter__(self) -> "StoreLock":
        self.path.parent.mkdir(parents=True, exist_ok=True)
        for _ in range(2):
            try:
                self.fd = os.open(str(self.path), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
                os.write(self.fd, json.dumps({"pid": os.getpid(), "time": time.time()}).encode())
                return self
            except FileExistsError:
                try:
                    info = read_json(self.path)
                    pid, when = int(info.get("pid", 0)), float(info.get("time", 0))
                except Exception:
                    pid, when = 0, 0.0
                stale = not pid_alive(pid) or (time.time() - when) > 6 * 3600
                if stale:
                    try:
                        os.unlink(str(self.path))
                    except OSError:
                        pass
                    continue
                raise SwitchError(t("busy", pid=pid))
        raise SwitchError(t("busy", pid="?"))

    def __exit__(self, *exc: Any) -> None:
        if self.fd is not None:
            os.close(self.fd)
            self.fd = None
        try:
            os.unlink(str(self.path))
        except OSError:
            pass


def load_state(paths: Paths) -> Optional[Dict[str, Any]]:
    f = paths.state_file
    if not f.exists():
        return None
    try:
        state = read_json(f)
    except (OSError, ValueError):
        bak = f.with_name(f.name + ".bak")
        state = read_json(bak)  # let it raise if the backup is broken too
    if state.get("format") != STATE_FORMAT:
        raise SwitchError(t("error", err="unsupported %s format %r" % (STATE_FILE, state.get("format"))))
    state.setdefault("profiles", {})
    state.setdefault("journal", None)
    state.setdefault("previous", None)
    for pname, meta in state["profiles"].items():
        meta.setdefault("id", "name:" + pname)  # stable until the next save stores it
    _reconcile(paths, state)
    return state


def _reconcile(paths: Paths, state: Dict[str, Any]) -> None:
    """Pick up profile folders that exist on disk but not in the state."""
    if not paths.profiles_dir.is_dir():
        return
    for d in sorted(paths.profiles_dir.iterdir()):
        if d.is_dir() and not d.name.startswith((".", "_")) and \
                not any(same_name(d.name, n) for n in state["profiles"]):
            state["profiles"][d.name] = {"created": now_iso(), "note": "", "last_used": None,
                                         "id": uuid.uuid4().hex}


def save_state(paths: Paths, state: Dict[str, Any]) -> None:
    f = paths.state_file
    if f.exists():
        try:
            shutil.copy2(str(f), str(f.with_name(f.name + ".bak")))
        except OSError:
            pass
    write_json_atomic(f, state, mode=0o600)


def log_event(paths: Paths, text: str) -> None:
    try:
        paths.store.mkdir(parents=True, exist_ok=True)
        with open(str(paths.store / LOG_FILE), "a", encoding="utf-8") as f:
            f.write("%s  %s\n" % (now_iso(), text))
    except OSError:
        pass


def init_state(paths: Paths, name: str = DEFAULT_OLD_NAME, note: Optional[str] = None) -> Dict[str, Any]:
    name = validate_name(name)
    _check_store_location(paths)
    m = read_marker(paths)
    if m and os.path.normcase(str(m.get("store") or "")) != os.path.normcase(str(paths.store)) \
            and Path(str(m.get("store"))).joinpath(STATE_FILE).exists():
        raise SwitchError(t("other_store", store=m.get("store")))
    state = {
        "format": STATE_FORMAT,
        "tool": "claude-switch",
        "active": name,
        "previous": None,
        "live": paths.live_signature(),
        "journal": None,
        "profiles": {name: {"created": now_iso(), "note": note if note is not None else t("note_original"),
                            "last_used": now_iso(), "id": uuid.uuid4().hex}},
    }
    paths.profile_dir(name).mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(str(paths.store), 0o700)
    except OSError:
        pass
    save_state(paths, state)
    if paths.config_dir.is_dir() or lexists(paths.global_config):
        write_marker(paths, state)
    log_event(paths, "init active=%s config_dir=%s global=%s" % (name, paths.config_dir, paths.global_config))
    return state


def is_store_python() -> bool:
    """Microsoft Store Python redirects folders it creates under AppData into
    its own private cache, where Claude Desktop would never see them."""
    where_ = (sys.executable + "|" + getattr(sys, "base_exec_prefix", "")).lower()
    return "pythonsoftwarefoundation.python" in where_ or "\\windowsapps\\" in where_


def _check_store_location(paths: Paths) -> None:
    if is_within(paths.store, paths.config_dir):
        raise SwitchError(t("store_inside_config", live=paths.config_dir))


def _check_live_signature(paths: Paths, state: Dict[str, Any], force: bool) -> None:
    was = state.get("live") or {}
    now = paths.live_signature()
    if was and was != now and not force:
        raise SwitchError(t("live_paths_changed", was=json.dumps(was), now=json.dumps(now)))


def _find_profile(state: Dict[str, Any], name: str) -> str:
    if name in state["profiles"]:
        return name
    for n in state["profiles"]:
        if same_name(n, name):
            return n
    raise SwitchError(t("no_such_profile", name=name))


def _name_taken(paths: Paths, state: Dict[str, Any], name: str) -> bool:
    if any(same_name(n, name) for n in state["profiles"]):
        return True
    return lexists(paths.profile_dir(name))


# --------------------------------------------------------------------------
# Planning and executing a switch
# --------------------------------------------------------------------------


def _live_items(paths: Paths) -> List[Tuple[Path, Path]]:
    """(live path, path relative to a profile folder) for everything that is
    profile data right now."""
    items: List[Tuple[Path, Path]] = []
    units = paths.unit_slots()
    unit_lives = {os.path.normcase(str(live)) for live, _ in units}
    if paths.config_dir.is_dir():
        for child in sorted(os.listdir(str(paths.config_dir))):
            if child in PINNED_CHILDREN:
                continue
            live = paths.config_dir / child
            if os.path.normcase(str(live)) in unit_lives:  # CLAUDE_CONFIG_DIR/.claude.json
                continue
            items.append((live, Path(SLOT_CONFIG) / child))
    for live, rel in units:
        if lexists(live):
            items.append((live, rel))
    return items


def _stored_items(paths: Paths, name: str) -> Tuple[List[Tuple[Path, Path]], List[str]]:
    """(stored path, live path) for a parked profile, plus skipped pinned names."""
    pdir = paths.profile_dir(name)
    items: List[Tuple[Path, Path]] = []
    skipped: List[str] = []
    cfg = pdir / SLOT_CONFIG
    if cfg.is_dir():
        for child in sorted(os.listdir(str(cfg))):
            if child in PINNED_CHILDREN:
                skipped.append(child)
                continue
            items.append((cfg / child, paths.config_dir / child))
    units = paths.unit_slots()
    for live, rel in units:
        if lexists(pdir / rel):
            items.append((pdir / rel, live))
    # Desktop data from a different kind of install / OS: leave it parked.
    known = {rel.name for _live, rel in units if rel.parts[0] == SLOT_DESKTOP}
    ddir = pdir / SLOT_DESKTOP
    if ddir.is_dir():
        for key in sorted(os.listdir(str(ddir))):
            if key not in known:
                skipped.append("%s/%s" % (SLOT_DESKTOP, key))
    return items, skipped


class Plan:
    def __init__(self, src: str, dst: str):
        self.src, self.dst = src, dst
        self.moves: List[Tuple[Path, Path]] = []
        self.skipped: List[str] = []
        self.conflict_dir: Optional[Path] = None


def plan_switch(paths: Paths, state: Dict[str, Any], target: str, park_into: Optional[Path] = None) -> Plan:
    """Moves that park the active profile and bring `target` live.

    park_into: where to park the active profile (defaults to its own folder);
    used by `reset` to send the old contents straight to the trash."""
    active = state["active"]
    plan = Plan(active, target)
    park_dir = park_into or paths.profile_dir(active)
    aside_root = unique_path(paths.store / CONFLICTS_DIR / stamp())

    # 1) park: live -> profiles/<active>/...
    for live, rel in _live_items(paths):
        dst = park_dir / rel
        if lexists(dst):
            # A leftover in the active profile's folder.  Keep it, but out of the way.
            plan.moves.append((dst, aside_root / active / rel))
            plan.conflict_dir = aside_root
        plan.moves.append((live, dst))

    # 2) unpark: profiles/<target>/... -> live
    stored, skipped = _stored_items(paths, target)
    plan.skipped = skipped
    parked_now = {str(live) for live, _ in _live_items(paths)}
    for src, live in stored:
        if lexists(live) and str(live) not in parked_now:
            # Something live that we are not parking (should not happen).
            plan.moves.append((live, aside_root / "live" / live.name))
            plan.conflict_dir = aside_root
        plan.moves.append((src, live))
    return plan


def _journal_moves(j: Dict[str, Any]) -> List[Tuple[Path, Path]]:
    return [(Path(s), Path(d)) for s, d in j["moves"]]


class _Signals:
    """While moving files, turn SIGTERM / SIGHUP (e.g. the terminal or a parent
    Claude process going away) into an exception so the switch is rolled
    back instead of stopping half-way; while rolling back, ignore them."""

    SIGS = tuple(getattr(signal, n) for n in ("SIGTERM", "SIGHUP") if hasattr(signal, n))

    def __init__(self) -> None:
        self.saved: Dict[int, Any] = {}

    def _usable(self) -> bool:
        import threading

        return not IS_WINDOWS and threading.current_thread() is threading.main_thread()

    def _handler(self, signum: int, _frame: Any) -> None:
        raise KeyboardInterrupt("signal %d" % signum)

    def __enter__(self) -> "_Signals":
        if self._usable():
            for sig in self.SIGS:
                try:
                    self.saved[sig] = signal.signal(sig, self._handler)
                except (OSError, ValueError):
                    pass
        return self

    def hold(self) -> None:
        """Ignore signals (including Ctrl-C) until __exit__."""
        if not self._usable():
            return
        for sig in self.SIGS + (signal.SIGINT,):
            try:
                old = signal.signal(sig, signal.SIG_IGN)
                self.saved.setdefault(sig, old)
            except (OSError, ValueError):
                pass

    def __exit__(self, *exc: Any) -> None:
        for sig, old in self.saved.items():
            try:
                signal.signal(sig, old)
            except (OSError, ValueError, TypeError):
                pass


def execute_plan(paths: Paths, state: Dict[str, Any], plan: Plan, op: str = "switch",
                 new_active: Optional[str] = None, fail_hook: Optional[Callable[[int], None]] = None) -> None:
    """Run the moves with a persistent journal; roll back on failure."""
    new_active = new_active or plan.dst
    journal = {
        "op": op,
        "from": plan.src,
        "to": new_active,
        "started": now_iso(),
        "moves": [[str(s), str(d)] for s, d in plan.moves],
        "done": 0,
    }
    state["journal"] = journal
    save_state(paths, state)
    log_event(paths, "%s start %s -> %s (%d moves)" % (op, plan.src, new_active, len(plan.moves)))
    with _Signals() as sigs:
        try:
            for i, (s, d) in enumerate(plan.moves):
                if fail_hook:
                    fail_hook(i)
                move(s, d)
                journal["done"] = i + 1
                save_state(paths, state)
        except BaseException as e:  # noqa: BLE001 - includes KeyboardInterrupt
            sigs.hold()
            err = "%s: %s" % (type(e).__name__, e)
            log_event(paths, "%s failed at step %d: %s" % (op, journal["done"], err))
            try:
                _rollback(paths, journal)
            except Exception as e2:  # noqa: BLE001
                log_event(paths, "rollback failed: %s" % e2)
                raise SwitchError(t("switch_failed_stuck", err=err)) from e
            state["journal"] = None
            save_state(paths, state)
            log_event(paths, "%s rolled back" % op)
            if isinstance(e, KeyboardInterrupt):
                raise
            raise SwitchError(t("switch_failed_rolled_back", err=err)) from e
        sigs.hold()  # finishing touches must not be interrupted either
        _finish(paths, state, journal)


def _finish(paths: Paths, state: Dict[str, Any], journal: Dict[str, Any]) -> None:
    old, new = journal["from"], journal["to"]
    if old != new:
        state["previous"] = old
    state["active"] = new
    state["journal"] = None
    state["live"] = paths.live_signature()
    meta = state["profiles"].setdefault(new, {"created": now_iso(), "note": ""})
    meta.setdefault("id", uuid.uuid4().hex)
    meta["last_used"] = now_iso()
    save_state(paths, state)
    write_marker(paths, state)
    log_event(paths, "%s done: active=%s" % (journal["op"], new))


def write_marker(paths: Paths, state: Dict[str, Any]) -> None:
    active = state["active"]
    data = {
        "tool": "claude-switch",
        "profile": active,
        "id": state["profiles"][active].get("id"),
        "store": str(paths.store),
        "note": "Which claude-switch profile this folder belongs to. Safe to delete.",
    }
    try:
        write_json_atomic(paths.config_dir / MARKER_FILE, data)
    except OSError as e:
        log_event(paths, "could not write marker: %s" % e)


def read_marker(paths: Paths) -> Optional[Dict[str, Any]]:
    try:
        data = read_json(paths.config_dir / MARKER_FILE)
        return data if isinstance(data, dict) else None
    except (OSError, ValueError):
        return None


def _check_marker(paths: Paths, state: Dict[str, Any], force: bool) -> None:
    """Refuse when the live config belongs to another profile or store --
    e.g. two stores managing the same ~/.claude, or a restored backup."""
    m = read_marker(paths)
    if not m or force:
        return
    same_store = os.path.normcase(str(m.get("store") or "")) == os.path.normcase(str(paths.store))
    same_id = m.get("id") == state["profiles"].get(state["active"], {}).get("id")
    if not (same_store and same_id):
        raise SwitchError(t("marker_mismatch", profile=m.get("profile"), store=m.get("store"),
                            active=state["active"], our_store=paths.store))


def _aside(paths: Paths, p: Path) -> Path:
    dst = unique_path(paths.store / CONFLICTS_DIR / stamp() / "repair" / p.name)
    move(p, dst)
    return dst


# Journal semantics: journal["done"] == k means moves[0..k-1] are known to be
# complete.  moves[k] may or may not have happened (a crash can land between
# the rename and the journal write); moves after k have not started.  A path
# can be the source of one move and the destination of a later one (e.g. both
# profiles have CLAUDE.md), so recovery must only walk the moves on its own
# side of k, in order.


def _rollback(paths: Paths, journal: Dict[str, Any]) -> None:
    moves = _journal_moves(journal)
    done = min(int(journal.get("done", 0)), len(moves))
    last = min(done, len(moves) - 1)
    for i in range(last, -1, -1):
        s, d = moves[i]
        if lexists(d) and not lexists(s):
            move(d, s)
        elif lexists(d) and lexists(s):
            if i < done:
                # s was re-created (e.g. by a running Claude) after it was
                # moved.  Keep that newer copy aside and put the original back.
                _aside(paths, s)
                move(d, s)
            else:
                # moves[done] with both present: it never ran and d is a live
                # path that an earlier move had not parked yet.  Leave it.
                log_event(paths, "rollback: left %s and %s in place" % (s, d))
        # only s present: never moved (or already back); neither: nothing to do


def _roll_forward(paths: Paths, journal: Dict[str, Any]) -> None:
    moves = _journal_moves(journal)
    done = min(int(journal.get("done", 0)), len(moves))
    for i in range(done, len(moves)):
        s, d = moves[i]
        if lexists(s) and not lexists(d):
            move(s, d)
        elif lexists(s) and lexists(d):
            # d appeared (e.g. re-created by a running Claude) before we got
            # to it: keep it aside, then finish the move.
            _aside(paths, d)
            move(s, d)
        elif not lexists(s) and not lexists(d):
            log_event(paths, "repair: %s is gone (was going to %s)" % (s, d))
        # only d present: this move had already completed


# --------------------------------------------------------------------------
# High-level operations (used by the CLI, the menu and the tests)
# --------------------------------------------------------------------------


class Options:
    """Knobs shared by the commands."""

    def __init__(self, force: bool = False, dry_run: bool = False, yes: bool = False,
                 interactive: bool = False, quit_desktop: bool = False,
                 proc_finder: Optional[Callable[[], List[Proc]]] = None):
        self.force, self.dry_run, self.yes = force, dry_run, yes
        self.interactive, self.quit_desktop = interactive, quit_desktop
        self.proc_finder = proc_finder or find_claude_processes
        self.desktop_was_quit: List[Proc] = []
        self.running_ok = False  # user chose "switch anyway" once


def ensure_state(paths: Paths, name: str = DEFAULT_OLD_NAME, announce: bool = True) -> Dict[str, Any]:
    state = load_state(paths)
    if state is None:
        state = init_state(paths, name)
        if announce:
            say(t("initialized", name=name))
    return state


def _guard(paths: Paths, state: Dict[str, Any], opts: Options) -> None:
    j = state.get("journal")
    if j:
        raise SwitchError(t("journal_pending", op=j["op"], src=j["from"], dst=j["to"]))
    _check_live_signature(paths, state, opts.force)
    _check_store_location(paths)
    _check_marker(paths, state, opts.force)
    if IS_WINDOWS and is_store_python() and any(d.is_dir() for d in paths.desktop_dirs()):
        raise SwitchError(t("store_python"))
    for where_ in [paths.config_dir] + [live.parent for live, _ in paths.unit_slots()]:
        if not same_device(paths.store, where_):
            raise SwitchError(t("cross_device", store=paths.store, live=where_))
    _wait_for_claude_to_exit(opts)


def _wait_for_claude_to_exit(opts: Options) -> None:
    """Refuse (or, interactively, wait) while Claude Code / Desktop is running."""
    if opts.force or opts.running_ok:
        return
    offered_quit = False
    while True:
        procs = opts.proc_finder()
        if not procs:
            return
        desktop = [p for p in procs if p.kind == "desktop"]
        can_ask = opts.interactive and not opts.yes
        if not can_ask and not (desktop and opts.quit_desktop):
            raise ClaudeRunning(procs)
        say(t("claude_running"))
        for p in procs:
            say("  - " + p.describe())
        if desktop and (IS_MAC or IS_WINDOWS) and not offered_quit:
            offered_quit = True
            if opts.quit_desktop or ask_yes_no(t("ask_quit_desktop"), default=True):
                say(t("quitting_desktop"))
                if quit_desktop(desktop):
                    opts.desktop_was_quit = desktop
                    # Claude Code sessions started by Desktop exit with it; give them a moment.
                    time.sleep(1.5)
                    continue
                say(t("desktop_still_running"))
        if not can_ask:
            raise ClaudeRunning(opts.proc_finder() or procs)
        say(t("claude_running_hint"))
        try:
            ans = input(t("ask_retry")).strip().lower()
        except EOFError:
            ans = "q"
        if ans == "f":
            opts.running_ok = True
            return
        if ans == "q":
            raise SwitchError(t("cancelled"))


def _print_plan(plan: Plan) -> None:
    say(t("dry_run_header"))
    for s, d in plan.moves:
        say("  %s\n    -> %s" % (s, d))
    if plan.skipped:
        say(t("pinned_skipped", items=", ".join(plan.skipped)))


def switch_to(paths: Paths, target: str, opts: Options, confirm: bool = False) -> Dict[str, Any]:
    with StoreLock(paths):
        state = ensure_state(paths)
        target = _find_profile(state, target)
        if target == state["active"]:
            say(t("already_active", name=target))
            return state
        if not paths.profile_dir(target).is_dir():
            raise SwitchError(t("profile_folder_missing", name=target, path=paths.profile_dir(target)))
        if opts.dry_run:
            _print_plan(plan_switch(paths, state, target))
            return state
        if confirm and opts.interactive and not opts.yes:
            if not ask_yes_no(t("confirm_switch", old=state["active"], new=target), default=True):
                raise SwitchError(t("cancelled"))
        _guard(paths, state, opts)
        plan = plan_switch(paths, state, target)
        old = state["active"]
        execute_plan(paths, state, plan)
        try:
            _sync_account(paths, old)
        except OSError as e:  # the switch itself is complete; this is cosmetic
            log_event(paths, "account sync skipped: %s" % e)
        if plan.skipped:
            say(t("pinned_skipped", items=", ".join(plan.skipped)))
        if plan.conflict_dir:
            say(t("conflicts_moved", path=plan.conflict_dir))
        say(t("switched", old=old, new=target))
        if _retention_warning(paths) and opts.interactive and not opts.yes:
            if ask_yes_no(t("ask_keep_history"), default=True):
                _apply_settings(paths.config_dir / "settings.json", {"cleanupPeriodDays": 3650})
                say(t("kept_history"))
        _maybe_reopen_desktop(opts)
        return state


def _cleanup_days(paths: Paths) -> Optional[float]:
    """cleanupPeriodDays from the live user settings (Claude's default: 30)."""
    try:
        v = read_json(paths.config_dir / "settings.json").get("cleanupPeriodDays")
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            return float(v)
    except (OSError, ValueError, AttributeError):
        pass
    return 30.0


def _retention_warning(paths: Paths) -> int:
    """Claude Code deletes transcripts older than cleanupPeriodDays when it
    starts.  A profile that sat parked for a while can lose old conversations
    on its first start, so say so while there is still time to export."""
    days = _cleanup_days(paths)
    proj = paths.config_dir / "projects"
    if not days or days <= 0 or not proj.is_dir():
        return 0
    cutoff = time.time() - days * 86400
    old = 0
    for root, _dirs, files in os.walk(_long(proj)):
        for fn in files:
            if fn.endswith(".jsonl"):
                try:
                    if os.stat(os.path.join(root, fn)).st_mtime < cutoff:
                        old += 1
                except OSError:
                    pass
    if old:
        say(t("retention_warning", n=old, days=int(days)))
    return old


def _maybe_reopen_desktop(opts: Options) -> None:
    if opts.desktop_was_quit and opts.interactive and not opts.yes:
        if ask_yes_no(t("ask_reopen_desktop"), default=True):
            open_desktop(opts.desktop_was_quit)


def _sync_account(paths: Paths, old_profile: str) -> None:
    """The login is shared (credentials are pinned / in the Keychain), so the
    account shown by the incoming profile should match the one that is really
    signed in.  Copy it from the profile we just parked."""
    pdir = paths.profile_dir(old_profile)
    for i, (live, rel) in enumerate(paths.global_files):
        old_file, new_file = pdir / rel, live
        if i == 0:
            if (pdir / SLOT_CONFIG / ".config.json").is_file():
                old_file = pdir / SLOT_CONFIG / ".config.json"
            new_file = paths.effective_config()
        if not (old_file.is_file() and new_file.is_file()):
            continue
        try:
            old_cfg, new_cfg = read_json(old_file), read_json(new_file)
        except (OSError, ValueError):
            continue
        if not (isinstance(old_cfg, dict) and isinstance(new_cfg, dict)):
            continue
        changed = False
        for key in ACCOUNT_KEYS:
            if key in old_cfg and new_cfg.get(key) != old_cfg[key]:
                new_cfg[key] = old_cfg[key]
                changed = True
        if changed:
            mode = stat.S_IMODE(os.stat(str(new_file)).st_mode)
            write_json_atomic(new_file, new_cfg, mode=mode)


def _apply_settings(settings_file: Path, values: Dict[str, Any]) -> None:
    """Merge top-level keys into a Claude settings.json (created if missing)."""
    data: Dict[str, Any] = {}
    if settings_file.is_file():
        try:
            loaded = read_json(settings_file)
            if isinstance(loaded, dict):
                data = loaded
        except (OSError, ValueError):
            pass
    for k, v in values.items():
        if k == "env" and isinstance(v, dict) and isinstance(data.get("env"), dict):
            data["env"] = dict(data["env"], **v)
        else:
            data[k] = v
    write_json_atomic(settings_file, data)


def api_settings(paths: Paths) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """(env, top-level settings) in the live profile that route Claude Code to
    an API provider: settings.json "env" + apiKeyHelper, and the legacy "env"
    block of ~/.claude.json."""
    env: Dict[str, Any] = {}
    top: Dict[str, Any] = {}
    sources = [(paths.config_dir / "settings.json", True), (paths.effective_config(), False)]
    for f, is_settings in sources:
        try:
            data = read_json(f)
        except (OSError, ValueError):
            continue
        if not isinstance(data, dict):
            continue
        block = data.get("env")
        if isinstance(block, dict):
            for k, v in block.items():
                if is_api_env_key(k) and k not in env:
                    env[k] = v
        if is_settings:
            top.update({k: data[k] for k in _API_TOP_KEYS if k in data})
    return env, top


def _seed_global_config(paths: Paths, source: Optional[Path] = None) -> Dict[str, Any]:
    """Login, onboarding and install keys from the current global config."""
    cfg: Dict[str, Any] = {}
    try:
        source = source or paths.effective_config()
        if source.is_file():
            current = read_json(source)
            if isinstance(current, dict):
                cfg = {k: current[k] for k in SEED_KEYS if k in current}
                cfg.update({k: v for k, v in current.items() if k.startswith(SEED_PREFIXES)})
    except (OSError, ValueError):
        pass
    return cfg


def create_profile(paths: Paths, state: Dict[str, Any], name: str, note: Optional[str] = None,
                   copy_items: Iterable[str] = (), clone_from: Optional[str] = None,
                   no_account_sync: bool = False, keep_api: bool = False) -> str:
    """Create an inactive profile: fresh (default) or a copy of another one."""
    name = validate_name(name)
    if _name_taken(paths, state, name):
        raise SwitchError(t("profile_exists", name=name))
    tmp = paths.profiles_dir / ("_new-%s-%s" % (stamp(), os.getpid()))
    rmtree(tmp)
    tmp.mkdir(parents=True)
    try:
        if clone_from:
            src = _find_profile(state, clone_from)
            if src == state["active"]:
                for live, rel in _live_items(paths):
                    copy_any(live, tmp / rel)
            else:
                sdir = paths.profile_dir(src)
                for rel in [SLOT_CONFIG, SLOT_DESKTOP, SLOT_HOME] + \
                        [str(r) for _l, r in paths.global_files + paths.extra_files]:
                    if lexists(sdir / rel):
                        copy_any(sdir / rel, tmp / rel)
            (tmp / SLOT_CONFIG).mkdir(exist_ok=True)
        else:
            (tmp / SLOT_CONFIG).mkdir()
            for i, (live, rel) in enumerate(paths.global_files):
                if i == 0:
                    write_json_atomic(tmp / rel, _seed_global_config(paths), mode=0o600)
                elif lexists(live):
                    write_json_atomic(tmp / rel, _seed_global_config(paths, live), mode=0o600)
            copied = []
            for item in copy_items:
                item = item.strip().strip("/\\")
                if not item:
                    continue
                if item in PINNED_CHILDREN or "/" in item or "\\" in item or item in (".", ".."):
                    continue
                src_path = paths.config_dir / item
                if not lexists(src_path):
                    say(t("copy_missing", item=item))
                    continue
                copy_any(src_path, tmp / SLOT_CONFIG / item)
                copied.append(item)
            if copied:
                say(t("copied_items", items=", ".join(copied)))
            if keep_api:
                env, top = api_settings(paths)
                if env or top:
                    _apply_settings(tmp / SLOT_CONFIG / "settings.json", dict(top, env=env) if env else top)
                    say(t("api_kept", keys=", ".join(sorted(list(env) + list(top)))))
            if no_account_sync:
                _apply_settings(tmp / SLOT_CONFIG / "settings.json", NO_ACCOUNT_SYNC_SETTINGS)
                say(t("no_account_sync_done"))
        os.rename(str(tmp), str(paths.profile_dir(name)))
    except BaseException:
        rmtree(tmp)
        raise
    if note is None:
        note = t("note_clone", src=clone_from) if clone_from else t("note_clean")
    state["profiles"][name] = {"created": now_iso(), "note": note, "last_used": None, "id": uuid.uuid4().hex}
    save_state(paths, state)
    log_event(paths, "create %s (%s)" % (name, "clone of %s" % clone_from if clone_from else "fresh"))
    return name


def _decide_keep_api(paths: Paths, opts: Options, keep_api: Optional[bool]) -> bool:
    """Carry API-provider settings into a new clean profile?  Ask when the
    user did not say and such settings exist."""
    if keep_api is not None:
        return keep_api
    env, top = api_settings(paths)
    if not (env or top):
        return False
    if opts.interactive and not opts.yes:
        return ask_yes_no(t("ask_keep_api", keys=", ".join(sorted(list(env) + list(top)))), default=True)
    return False


def new_profile(paths: Paths, name: str, opts: Options, copy_items: Iterable[str] = (),
                clone_from: Optional[str] = None, switch: bool = False, no_account_sync: bool = False,
                keep_api: Optional[bool] = None) -> None:
    keep = False if clone_from else _decide_keep_api(paths, opts, keep_api)
    with StoreLock(paths):
        state = ensure_state(paths)
        create_profile(paths, state, name, copy_items=copy_items, clone_from=clone_from,
                       no_account_sync=no_account_sync, keep_api=keep)
        say(t("created_profile", name=name))
    if switch:
        switch_to(paths, name, opts)


def _dry_run_first_park(paths: Paths, target: str) -> None:
    """Dry run before anything exists: show what `clean` would park."""
    say(t("not_initialized", name=DEFAULT_OLD_NAME))
    say(t("dry_run_header"))
    for live, rel in _live_items(paths):
        say("  %s\n    -> %s" % (live, paths.profile_dir(DEFAULT_OLD_NAME) / rel))
    say("  (%s)" % t("created_profile", name=target))


def clean(paths: Paths, opts: Options, name: str = DEFAULT_CLEAN_NAME, old_name: str = DEFAULT_OLD_NAME,
          reset: bool = False, copy_items: Iterable[str] = (), no_account_sync: bool = False,
          keep_api: Optional[bool] = None) -> None:
    """The one-click action: save the current Claude Code, start a clean one."""
    name = validate_name(name)
    if opts.dry_run:
        state = load_state(paths)
        existing = next((n for n in (state or {}).get("profiles", {}) if same_name(n, name)), None)
        if state is None or existing is None:
            _dry_run_first_park(paths, name)
            return
        if reset:
            reset_profile(paths, existing, opts)
        else:
            switch_to(paths, existing, opts)
        return
    _wait_for_claude_to_exit(opts)  # before creating anything
    st = load_state(paths)
    will_create = reset or st is None or not any(same_name(n, name) for n in st["profiles"])
    keep = _decide_keep_api(paths, opts, keep_api) if will_create else False
    with StoreLock(paths):
        state = ensure_state(paths, old_name)
        existing = next((n for n in state["profiles"] if same_name(n, name)), None)
        if existing is None:
            create_profile(paths, state, name, copy_items=copy_items, no_account_sync=no_account_sync,
                           keep_api=keep)
            say(t("created_profile", name=name))
            reset = False  # brand new already
        target = existing or name
    if reset:
        reset_profile(paths, target, opts, copy_items=copy_items, no_account_sync=no_account_sync,
                      keep_api=keep)
    else:
        switch_to(paths, target, opts, confirm=True)
    say(t("shared_login_note"))
    say(t("project_memory_note"))
    for line in doctor(paths):
        if line.startswith("!"):
            say(line)


def reset_profile(paths: Paths, name: str, opts: Options, copy_items: Iterable[str] = (),
                  no_account_sync: bool = False, keep_api: bool = False) -> None:
    """Make `name` fresh again (and active); its old contents go to the trash."""
    with StoreLock(paths):
        state = ensure_state(paths)
        name = _find_profile(state, name)
        trash = unique_path(paths.store / TRASH_DIR / ("%s-%s" % (stamp(), name)))
        if opts.dry_run:
            say(t("dry_run_header"))
            src = [live for live, _ in _live_items(paths)] if state["active"] == name else [paths.profile_dir(name)]
            for s in src:
                say("  %s\n    -> %s" % (s, trash))
            return
        if state["active"] == name:
            _guard(paths, state, opts)
            previous = state.get("previous")
            note = state["profiles"][name].get("note") or t("note_clean")
            tmp_name = "reset-%s" % stamp()
            create_profile(paths, state, tmp_name, note=note, copy_items=copy_items,
                           no_account_sync=no_account_sync, keep_api=keep_api)
            plan = plan_switch(paths, state, tmp_name, park_into=trash)
            execute_plan(paths, state, plan, op="reset")
            # The fresh profile is live now under tmp_name; give it the old name.
            old_dir = paths.profile_dir(name)
            if only_empty_dirs(old_dir):
                rmtree(old_dir)
            else:  # leftovers: keep them with the rest of the old contents
                move(old_dir, trash / "_leftovers")
            os.rename(str(paths.profile_dir(tmp_name)), str(paths.profile_dir(name)))
            state["profiles"][name] = state["profiles"].pop(tmp_name)
            state["active"] = name
            state["previous"] = previous
            save_state(paths, state)
            write_marker(paths, state)
        else:
            if lexists(paths.profile_dir(name)):
                move(paths.profile_dir(name), trash)
            note = state["profiles"][name].get("note")
            del state["profiles"][name]
            create_profile(paths, state, name, note=note, copy_items=copy_items,
                           no_account_sync=no_account_sync, keep_api=keep_api)
        log_event(paths, "reset %s -> old contents in %s" % (name, trash))
        say(t("reset_done", name=name, path=trash))
        active = state["active"]
    if active != name:
        switch_to(paths, name, opts)


def toggle(paths: Paths, opts: Options) -> None:
    state = load_state(paths)
    if state is None:
        clean(paths, opts)
        return
    target = state.get("previous")
    if not target or target not in state["profiles"]:
        others = [n for n in state["profiles"] if n != state["active"]]
        if len(others) != 1:
            raise SwitchError(t("no_previous"))
        target = others[0]
    switch_to(paths, target, opts)


def delete_profile(paths: Paths, name: str, opts: Options) -> Optional[Path]:
    with StoreLock(paths):
        state = ensure_state(paths)
        name = _find_profile(state, name)
        if name == state["active"]:
            raise SwitchError(t("cannot_delete_active", name=name))
        pdir = paths.profile_dir(name)
        size = human_size(tree_size(pdir))
        if opts.interactive and not opts.yes and not ask_yes_no(t("confirm_delete", name=name, size=size)):
            raise SwitchError(t("cancelled"))
        trash = unique_path(paths.store / TRASH_DIR / ("%s-%s" % (stamp(), name)))
        if lexists(pdir):
            move(pdir, trash)
        del state["profiles"][name]
        if state.get("previous") == name:
            state["previous"] = None
        save_state(paths, state)
        log_event(paths, "delete %s -> %s" % (name, trash))
        say(t("deleted", name=name, path=trash))
        return trash


def rename_profile(paths: Paths, old: str, new: str) -> None:
    with StoreLock(paths):
        state = ensure_state(paths)
        old = _find_profile(state, old)
        new = validate_name(new)
        if not same_name(new, old) and _name_taken(paths, state, new):
            raise SwitchError(t("profile_exists", name=new))
        if state.get("journal"):
            j = state["journal"]
            raise SwitchError(t("journal_pending", op=j["op"], src=j["from"], dst=j["to"]))
        src = paths.profile_dir(old)
        if lexists(src):
            if same_name(new, old):  # case-only rename on case-insensitive disks
                tmp = paths.profiles_dir / ("_rename-%s" % stamp())
                os.rename(str(src), str(tmp))
                os.rename(str(tmp), str(paths.profile_dir(new)))
            else:
                move(src, paths.profile_dir(new))
        else:
            paths.profile_dir(new).mkdir(parents=True, exist_ok=True)
        state["profiles"][new] = state["profiles"].pop(old)
        for key in ("active", "previous"):
            if state.get(key) == old:
                state[key] = new
        save_state(paths, state)
        if state["active"] == new and read_marker(paths):
            write_marker(paths, state)
        log_event(paths, "rename %s -> %s" % (old, new))
        say(t("renamed", old=old, new=new))


def repair(paths: Paths, opts: Options, direction: Optional[str] = None) -> None:
    with StoreLock(paths):
        state = load_state(paths)
        j = (state or {}).get("journal")
        if not state or not j:
            say(t("no_journal"))
            return
        if direction is None:
            if opts.interactive and not opts.yes:
                ans = input(t("repair_choose", src=j["from"], dst=j["to"], done=j.get("done", 0),
                              total=len(j["moves"]))).strip().lower()
                direction = "back" if ans.startswith("r") else "forward" if ans.startswith("f") else None
                if direction is None:
                    raise SwitchError(t("cancelled"))
            else:
                direction = "back"
        _wait_for_claude_to_exit(opts)
        if direction == "forward":
            _roll_forward(paths, j)
            _finish(paths, state, j)
            say(t("repaired_forward", name=j["to"]))
        else:
            _rollback(paths, j)
            state["journal"] = None
            save_state(paths, state)
            log_event(paths, "repair: rolled back %s -> %s" % (j["from"], j["to"]))
            say(t("repaired_back", name=j["from"]))


# --------------------------------------------------------------------------
# Export / import
# --------------------------------------------------------------------------


def _profile_sources(paths: Paths, state: Dict[str, Any], name: str) -> List[Tuple[Path, str]]:
    """(filesystem path, archive path) pairs for a profile's top-level items."""
    if name == state["active"]:
        return [(live, rel.as_posix()) for live, rel in _live_items(paths)]
    pdir = paths.profile_dir(name)
    out = []
    cfg = pdir / SLOT_CONFIG
    if cfg.is_dir():
        for child in sorted(os.listdir(str(cfg))):
            if child not in PINNED_CHILDREN:
                out.append((cfg / child, "%s/%s" % (SLOT_CONFIG, child)))
    for _live, rel in paths.global_files + paths.extra_files:
        if lexists(pdir / rel):
            out.append((pdir / rel, rel.as_posix()))
    for name in HOME_MEMORY_FILES:
        if lexists(pdir / SLOT_HOME / name):
            out.append((pdir / SLOT_HOME / name, "%s/%s" % (SLOT_HOME, name)))
    ddir = pdir / SLOT_DESKTOP
    if ddir.is_dir():
        for key in sorted(os.listdir(str(ddir))):
            out.append((ddir / key, "%s/%s" % (SLOT_DESKTOP, key)))
    return out


def export_profile(paths: Paths, name: str, out: Optional[str] = None, include_secrets: bool = False) -> Path:
    state = ensure_state(paths)
    name = _find_profile(state, name)
    dest = Path(out).expanduser() if out else Path.cwd() / ("claude-profile-%s-%s.zip" % (name, stamp()))
    if dest.is_dir():
        dest = dest / ("claude-profile-%s-%s.zip" % (name, stamp()))
    if is_within(dest, paths.config_dir):
        raise SwitchError(t("error", err="refusing to write the archive inside %s" % paths.config_dir))
    skipped_links: List[str] = []
    tmp = dest.with_name(dest.name + ".partial")
    with zipfile.ZipFile(str(tmp), "w", zipfile.ZIP_DEFLATED, allowZip64=True) as z:
        global_arcs = {rel.as_posix() for _l, rel in paths.global_files}
        for src, arc in _profile_sources(paths, state, name):
            if arc in global_arcs and not include_secrets:
                try:
                    cfg = read_json(src)
                    if isinstance(cfg, dict):
                        for k in SECRET_KEYS:
                            cfg.pop(k, None)
                    z.writestr(arc, json.dumps(cfg, indent=2, ensure_ascii=False))
                    continue
                except (OSError, ValueError):
                    pass
            _zip_tree(z, src, arc, skipped_links)
        manifest = {
            "tool": "claude-switch",
            "version": __version__,
            "format": STATE_FORMAT,
            "profile": name,
            "note": state["profiles"][name].get("note", ""),
            "exported": now_iso(),
            "platform": sys.platform,
            "secrets_included": include_secrets,
            "skipped_symlinks": skipped_links,
        }
        z.writestr(MANIFEST_NAME, json.dumps(manifest, indent=2, ensure_ascii=False))
    try:
        os.chmod(str(tmp), 0o600)  # transcripts can contain secrets
    except OSError:
        pass
    os.replace(str(tmp), str(dest))
    log_event(paths, "export %s -> %s" % (name, dest))
    say(t("exported", name=name, file=dest, size=human_size(dest.stat().st_size)))
    if not include_secrets:
        say(t("export_secrets_note"))
    return dest


def _zip_tree(z: zipfile.ZipFile, src: Path, arc: str, skipped_links: List[str]) -> None:
    if os.path.islink(str(src)):
        skipped_links.append(arc)
        return
    if os.path.isfile(str(src)):
        z.write(_long(src), arc)
        return
    base = _long(src)
    z.writestr(arc.rstrip("/") + "/", "")
    for root, dirs, files in os.walk(base):
        rel_root = os.path.relpath(root, base)
        rel_root = "" if rel_root == "." else rel_root.replace(os.sep, "/") + "/"
        for d in list(dirs):
            full = os.path.join(root, d)
            if os.path.islink(full):
                skipped_links.append(arc + "/" + rel_root + d)
                dirs.remove(d)
            else:
                z.writestr(arc + "/" + rel_root + d + "/", "")
        for fn in files:
            full = os.path.join(root, fn)
            if os.path.islink(full):
                skipped_links.append(arc + "/" + rel_root + fn)
                continue
            z.write(full, arc + "/" + rel_root + fn)


def _safe_member(name: str) -> Optional[str]:
    """Return a safe relative path for an archive member, or None (zip-slip)."""
    n = name.replace("\\", "/")
    if n.startswith("/") or re.match(r"^[A-Za-z]:", n):
        return None
    parts = [p for p in n.split("/") if p not in ("", ".")]
    if any(p == ".." for p in parts):
        return None
    if not parts:
        return None
    globals_ = {"claude%s.json" % sfx for sfx in GLOBAL_SUFFIXES} | {"claude.json.backup"}
    if parts[0] in globals_ or parts[0] == MANIFEST_NAME:
        if len(parts) != 1:
            return None
    elif parts[0] == SLOT_HOME:
        if len(parts) != 2 or parts[1] not in HOME_MEMORY_FILES:
            return None
    elif parts[0] == SLOT_CONFIG:
        if len(parts) > 1 and parts[1] in PINNED_CHILDREN:
            return None
    elif parts[0] == SLOT_DESKTOP:
        if len(parts) < 2 or not re.match(r"^[A-Za-z0-9_.-]+$", parts[1]):
            return None
    else:
        return None
    return "/".join(parts)


def import_profile(paths: Paths, archive: str, name: Optional[str] = None) -> str:
    src = Path(archive).expanduser()
    if not src.is_file():
        raise SwitchError(t("bad_archive", why="file not found: %s" % src))
    with StoreLock(paths):
        state = ensure_state(paths)
        try:
            z = zipfile.ZipFile(str(src))
        except zipfile.BadZipFile as e:
            raise SwitchError(t("bad_archive", why=e))
        with z:
            try:
                manifest = json.loads(z.read(MANIFEST_NAME).decode("utf-8"))
            except KeyError:
                raise SwitchError(t("bad_archive", why="missing %s" % MANIFEST_NAME))
            if manifest.get("tool") != "claude-switch":
                raise SwitchError(t("bad_archive", why="unknown tool %r" % manifest.get("tool")))
            name = validate_name(name or manifest.get("profile") or src.stem)
            if _name_taken(paths, state, name):
                raise SwitchError(t("profile_exists", name=name))
            tmp = paths.profiles_dir / ("_import-%s-%s" % (stamp(), os.getpid()))
            tmp.mkdir(parents=True)
            try:
                for info in z.infolist():
                    rel = _safe_member(info.filename)
                    if rel is None:
                        continue
                    if rel == MANIFEST_NAME:
                        continue
                    target = tmp.joinpath(*rel.split("/"))
                    if info.is_dir():
                        Path(_long(target)).mkdir(parents=True, exist_ok=True)
                        continue
                    Path(_long(target.parent)).mkdir(parents=True, exist_ok=True)
                    with z.open(info) as fin, open(_long(target), "wb") as fout:
                        shutil.copyfileobj(fin, fout)
                    if len(rel.split("/")) == 1:  # claude*.json: private
                        try:
                            os.chmod(_long(target), 0o600)
                        except OSError:
                            pass
                (tmp / SLOT_CONFIG).mkdir(exist_ok=True)
                os.rename(str(tmp), str(paths.profile_dir(name)))
            except BaseException:
                rmtree(tmp)
                raise
        state["profiles"][name] = {"created": now_iso(), "note": t("note_import", file=src.name), "last_used": None,
                                   "id": uuid.uuid4().hex}
        save_state(paths, state)
        log_event(paths, "import %s as %s" % (src, name))
        say(t("imported", file=src, name=name))
        return name


# --------------------------------------------------------------------------
# Status, scan, where, trash
# --------------------------------------------------------------------------


def profile_size(paths: Paths, state: Dict[str, Any], name: str) -> int:
    return sum(tree_size(p) for p, _ in _profile_sources(paths, state, name))


def status(paths: Paths) -> None:
    say("== %s %s ==" % (t("title"), __version__))
    state = load_state(paths)
    if state is None:
        say(t("not_initialized", name=DEFAULT_OLD_NAME))
        say("%s: %s" % (t("where_config_dir"), paths.config_dir))
        say("%s: %s  (%s)" % (t("where_global"), paths.global_config, human_size(tree_size(paths.global_config))))
        return
    say("%s: %s" % (t("active"), state["active"]))
    j = state.get("journal")
    if j:
        say("!! " + t("journal_pending", op=j["op"], src=j["from"], dst=j["to"]))
    rows = []
    for i, (name, meta) in enumerate(sorted(state["profiles"].items(), key=lambda kv: kv[1].get("created") or "")):
        mark = "*" if name == state["active"] else " "
        rows.append((
            "%s %d" % (mark, i + 1), name, human_size(profile_size(paths, state, name)),
            (meta.get("created") or "")[:16].replace("T", " "),
            (meta.get("last_used") or "-")[:16].replace("T", " "),
            meta.get("note") or "",
        ))
    header = ("", t("col_name"), t("col_size"), t("col_created"), t("col_used"), t("col_note"))
    widths = [max(_width(r[c]) for r in rows + [header]) for c in range(len(header))]
    say(t("profiles") + "  (" + t("marker_active") + ")")
    for r in [header] + rows:
        say("  " + "  ".join(_pad(r[c], widths[c]) for c in range(len(r))).rstrip())
    if paths.config_dir_from_env:
        say(t("config_dir_env_note"))


def _width(s: str) -> int:
    import unicodedata

    return sum(2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1 for ch in s)


def _pad(s: str, w: int) -> str:
    return s + " " * (w - _width(s))


def ordered_profiles(state: Dict[str, Any]) -> List[str]:
    return [n for n, _ in sorted(state["profiles"].items(), key=lambda kv: kv[1].get("created") or "")]


def scan_projects(paths: Paths) -> List[Path]:
    """Project-level memory/config files for projects Claude has seen."""
    projects: List[str] = []
    try:
        cfg = read_json(paths.effective_config())
        if isinstance(cfg, dict) and isinstance(cfg.get("projects"), dict):
            projects = list(cfg["projects"].keys())
    except (OSError, ValueError):
        pass
    found = []
    for proj in sorted(set(projects)):
        root = Path(proj)
        for rel in ("CLAUDE.md", "CLAUDE.local.md", ".claude"):
            p = root / rel
            if lexists(p):
                found.append(p)
    return found


def doctor(paths: Paths) -> List[str]:
    """Things that affect a "clean" Claude Code but are not part of a profile."""
    notes: List[str] = []
    settings: Dict[str, Any] = {}
    try:
        loaded = read_json(paths.config_dir / "settings.json")
        if isinstance(loaded, dict):
            settings = loaded
    except (OSError, ValueError):
        pass
    amd = settings.get("autoMemoryDirectory")
    if isinstance(amd, str) and amd.strip():
        d = Path(os.path.expanduser(amd))
        if not is_within(d, paths.config_dir):
            notes.append(t("doctor_auto_memory", dir=d))
    env = settings.get("env") if isinstance(settings.get("env"), dict) else {}
    if env.get("CLAUDE_CONFIG_DIR"):
        notes.append(t("doctor_settings_config_dir", dir=env["CLAUDE_CONFIG_DIR"]))
    for var in AUTH_ENV_VARS + ("ANTHROPIC_BASE_URL",):
        if os.environ.get(var):
            notes.append(t("doctor_auth_env", var=var))
    api_env, api_top = api_settings(paths)
    if api_env or api_top:
        notes.append(t("doctor_settings_provider", vars=", ".join(sorted(list(api_env) + list(api_top)))))
    try:
        gcfg = read_json(paths.effective_config())
    except (OSError, ValueError):
        gcfg = {}
    genv = gcfg.get("env") if isinstance(gcfg, dict) and isinstance(gcfg.get("env"), dict) else {}
    if genv.get("CLAUDE_CONFIG_DIR"):
        notes.append(t("doctor_settings_config_dir", dir=genv["CLAUDE_CONFIG_DIR"]))
    raw = os.environ.get("CLAUDE_CONFIG_DIR")
    if raw is not None and (raw.strip() == "" or raw.strip().startswith("~")):
        notes.append(t("doctor_config_dir_literal", value=raw))
    for var in ("CLAUDE_CODE_REMOTE_MEMORY_DIR", "CLAUDE_SECURESTORAGE_CONFIG_DIR"):
        if os.environ.get(var):
            notes.append(t("doctor_relocated", var=var, dir=os.environ[var]))
    if os.environ.get("CLAUDE_CODE_PLUGIN_CACHE_DIR"):
        notes.append(t("doctor_plugin_dir", dir=os.environ["CLAUDE_CODE_PLUGIN_CACHE_DIR"]))
    if (paths.home / ".cc-switch").exists():
        notes.append(t("doctor_cc_switch"))
    if (paths.home / ".config" / "anthropic").is_dir():
        notes.append(t("doctor_anthropic_profiles", dir=paths.home / ".config" / "anthropic"))
    if paths.config_dir_from_env:
        notes.append(t("config_dir_env_note"))
    for d in paths.desktop_dirs():
        if not d.is_dir():
            continue
        notes.append(t("doctor_desktop_login"))
        f = d / "claude_desktop_config.json"
        try:
            cfg = read_json(f)
            if isinstance(cfg, dict) and cfg.get("mcpServers"):
                notes.append(t("doctor_desktop_mcp", file=f))
        except (OSError, ValueError):
            pass
        if (d / "local-agent-mode-sessions").is_dir():
            notes.append(t("doctor_cowork", dir=d / "local-agent-mode-sessions"))
    if IS_MAC:
        notes.append(t("doctor_keychain"))
    projects = scan_projects(paths)
    if projects:
        notes.append(t("scan_header"))
        notes.extend("  " + str(p) for p in projects)
    else:
        notes.append(t("scan_none"))
    return notes


def where(paths: Paths) -> None:
    say("%s: %s" % (t("where_config_dir"), paths.config_dir))
    say("%s: %s" % (t("where_global"), paths.global_config))
    say("%s: %s" % (t("where_store"), paths.store))
    for live, rel in paths.unit_slots():
        if rel.parts[0] == SLOT_DESKTOP:
            say("%s: %s%s" % (t("where_desktop_sessions"), live, "" if lexists(live) else "  (-)"))
    if paths.config_dir_from_env:
        say(t("config_dir_env_note"))


def trash(paths: Paths, opts: Options, empty: bool = False) -> None:
    tdir = paths.store / TRASH_DIR
    entries = sorted(tdir.iterdir()) if tdir.is_dir() else []
    if not entries:
        say(t("trash_empty"))
        return
    total = sum(tree_size(e) for e in entries)
    say(t("trash_list", size=human_size(total)))
    for e in entries:
        say("  %s  (%s)" % (e.name, human_size(tree_size(e))))
    if empty:
        if opts.interactive and not opts.yes and not ask_yes_no(t("confirm_empty_trash", size=human_size(total))):
            raise SwitchError(t("cancelled"))
        if not opts.interactive and not opts.yes:
            raise SwitchError(t("error", err="pass --yes to empty the trash non-interactively"))
        with StoreLock(paths):
            for e in entries:
                rmtree(e)
        log_event(paths, "trash emptied")
        say(t("trash_emptied"))


# --------------------------------------------------------------------------
# Interactive menu
# --------------------------------------------------------------------------


def _pick_profile(state: Dict[str, Any], prompt_key: str = "ask_profile",
                  exclude: Iterable[str] = ()) -> Optional[str]:
    names = [n for n in ordered_profiles(state) if n not in set(exclude)]
    for i, n in enumerate(names, 1):
        mark = "*" if n == state["active"] else " "
        say("  %s %d) %s  %s" % (mark, i, n, state["profiles"][n].get("note") or ""))
    try:
        ans = input(t(prompt_key)).strip()
    except EOFError:
        return None
    if not ans:
        return None
    if ans.isdigit() and 1 <= int(ans) <= len(names):
        return names[int(ans) - 1]
    return ans


def menu(paths: Paths, opts: Options) -> None:
    while True:
        say()
        status(paths)
        state = load_state(paths)
        active = state["active"] if state else None
        previous = state.get("previous") if state else None
        if state and (not previous or previous not in state["profiles"]):
            others = [n for n in ordered_profiles(state) if n != active]
            previous = DEFAULT_OLD_NAME if DEFAULT_OLD_NAME in others else (others[0] if len(others) == 1 else None)
        actions: List[Tuple[str, str, Callable[[], None]]] = []

        if state and state.get("journal"):
            actions.append(("r", t("menu_repair"), lambda: repair(paths, opts)))
        if active == DEFAULT_CLEAN_NAME:
            actions.append(("1", t("menu_reset_clean", name=DEFAULT_CLEAN_NAME),
                            lambda: clean(paths, opts, reset=True)))
        else:
            actions.append(("1", t("menu_to_clean"), lambda: clean(paths, opts)))
        if previous and previous != active and not (previous == DEFAULT_CLEAN_NAME and active != DEFAULT_CLEAN_NAME):
            actions.append(("2", t("menu_back", name=previous), lambda p=previous: switch_to(paths, p, opts)))

        def _other() -> None:
            st = ensure_state(paths)
            name = _pick_profile(st)
            if name:
                switch_to(paths, name, opts, confirm=True)

        def _new() -> None:
            name = input(t("ask_new_name")).strip()
            if not name:
                return
            no_sync = ask_yes_no(t("ask_no_account_sync"), default=False)
            new_profile(paths, name, opts, no_account_sync=no_sync)
            if ask_yes_no(t("ask_switch_now"), default=True):
                switch_to(paths, name, opts)

        def _clone() -> None:
            st = ensure_state(paths)
            src = _pick_profile(st, "ask_src_name")
            if not src:
                return
            name = input(t("ask_new_name")).strip()
            if name:
                new_profile(paths, name, opts, clone_from=src)

        def _export() -> None:
            st = ensure_state(paths)
            name = _pick_profile(st)
            if name:
                export_profile(paths, name, out=str(Path.home() / "Desktop") if (Path.home() / "Desktop").is_dir() else None)

        def _import() -> None:
            f = input(t("ask_zip_path")).strip().strip('"').strip("'")
            if f:
                import_profile(paths, f)

        def _rename() -> None:
            st = ensure_state(paths)
            old = _pick_profile(st)
            if not old:
                return
            new = input(t("ask_new_name")).strip()
            if new:
                rename_profile(paths, old, new)

        def _delete() -> None:
            st = ensure_state(paths)
            name = _pick_profile(st, exclude=[st["active"]])
            if name:
                delete_profile(paths, name, opts)

        def _scan() -> None:
            for line in doctor(paths):
                say(line)

        if state and len(state["profiles"]) > 1:
            actions.append(("3", t("menu_switch_other"), _other))
        actions += [
            ("4", t("menu_new"), _new),
            ("5", t("menu_clone"), _clone),
            ("6", t("menu_export"), _export),
            ("7", t("menu_import"), _import),
            ("8", t("menu_rename"), _rename),
            ("9", t("menu_delete"), _delete),
            ("s", t("menu_scan"), _scan),
            ("w", t("menu_where"), lambda: where(paths)),
            ("0", t("menu_quit"), lambda: None),
        ]
        say()
        for key, label, _ in actions:
            say("  [%s] %s" % (key, label))
        try:
            choice = input(t("menu_choose")).strip().lower()
        except (EOFError, KeyboardInterrupt):
            say()
            return
        if choice in ("0", "q", "quit", "exit"):
            return
        match = [a for a in actions if a[0] == choice]
        if not match:
            say(t("invalid_choice"))
            continue
        # "switch anyway" and "quit Desktop" answers apply to one action only.
        opts.running_ok = False
        opts.desktop_was_quit = []
        try:
            match[0][2]()
        except SwitchError as e:
            say(str(e))
        except KeyboardInterrupt:
            say()
            say(t("cancelled"))
        try:
            input(t("press_enter"))
        except (EOFError, KeyboardInterrupt):
            return


# --------------------------------------------------------------------------
# Command line
# --------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="claude_switch.py",
        description="Switch Claude Code (CLI + Claude Desktop) between a clean state and saved profiles "
                    "(memory, CLAUDE.md, history, settings, plugins, MCP servers).",
    )
    p.add_argument("--version", action="version", version="%(prog)s " + __version__)
    p.add_argument("--store", help="profile store folder (default ~/.claude-profiles, or $CLAUDE_SWITCH_HOME)")
    p.add_argument("--lang", choices=["en", "zh"], help="message language (default: from system locale)")
    p.add_argument("-y", "--yes", action="store_true", help="do not ask for confirmation")

    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--force", action="store_true",
                        help="switch even if Claude is running or the config location changed")
    common.add_argument("--quit-desktop", action="store_true", help="quit Claude Desktop first if it is running")
    common.add_argument("--dry-run", action="store_true", help="only show what would be moved")

    sub = p.add_subparsers(dest="cmd", metavar="COMMAND")
    sub.add_parser("menu", help="interactive menu (default)")
    sub.add_parser("status", aliases=["list", "ls"], help="show profiles")
    s = sub.add_parser("init", help="register the current Claude Code as a profile")
    s.add_argument("--name", default=DEFAULT_OLD_NAME)
    s = sub.add_parser("clean", parents=[common], help="save the current Claude Code and switch to a clean one")
    s.add_argument("--name", default=DEFAULT_CLEAN_NAME, help="clean profile name (default: clean)")
    s.add_argument("--old-name", default=DEFAULT_OLD_NAME, help="name for your current state on first use")
    s.add_argument("--reset", action="store_true", help="empty the clean profile again (old contents to trash)")
    s.add_argument("--copy", action="append", default=[], metavar="ITEM",
                   help="copy an item of ~/.claude into the clean profile, e.g. settings.json (repeatable)")
    s.add_argument("--no-account-sync", action="store_true",
                   help="also stop skills/plugins/connectors from claude.ai syncing into the clean profile")
    s.add_argument("--keep-api-settings", dest="keep_api", action="store_const", const=True, default=None,
                   help="carry API-provider settings (ANTHROPIC_BASE_URL, keys, proxy...) into it")
    s.add_argument("--no-api-settings", dest="keep_api", action="store_const", const=False,
                   help="do not carry API-provider settings (and do not ask)")
    s = sub.add_parser("switch", aliases=["use"], parents=[common], help="switch to a profile")
    s.add_argument("name")
    sub.add_parser("toggle", parents=[common], help="switch to the previous profile")
    s = sub.add_parser("new", parents=[common], help="create a new clean profile")
    s.add_argument("name")
    s.add_argument("--copy", action="append", default=[], metavar="ITEM",
                   help="copy an item of ~/.claude into it, e.g. settings.json (repeatable)")
    s.add_argument("--switch", action="store_true", help="switch to it right away")
    s.add_argument("--no-account-sync", action="store_true",
                   help="stop skills/plugins/connectors from claude.ai syncing into it")
    s.add_argument("--keep-api-settings", dest="keep_api", action="store_const", const=True, default=None,
                   help="carry API-provider settings (ANTHROPIC_BASE_URL, keys, proxy...) into it")
    s.add_argument("--no-api-settings", dest="keep_api", action="store_const", const=False,
                   help="do not carry API-provider settings (and do not ask)")
    s = sub.add_parser("clone", parents=[common], help="duplicate a profile")
    s.add_argument("source")
    s.add_argument("name")
    s = sub.add_parser("reset", parents=[common], help="make a profile clean again (old contents to trash)")
    s.add_argument("name")
    s.add_argument("--copy", action="append", default=[], metavar="ITEM")
    s.add_argument("--no-account-sync", action="store_true")
    s = sub.add_parser("rename", help="rename a profile")
    s.add_argument("old")
    s.add_argument("new")
    s = sub.add_parser("delete", aliases=["rm"], help="move an inactive profile to the trash")
    s.add_argument("name")
    s = sub.add_parser("export", help="save a profile to a zip file")
    s.add_argument("name", nargs="?", help="profile (default: active)")
    s.add_argument("-o", "--output", help="zip file or folder")
    s.add_argument("--include-secrets", action="store_true", help="keep API keys stored in claude.json")
    s = sub.add_parser("import", help="add a profile from a zip made by export")
    s.add_argument("file")
    s.add_argument("name", nargs="?")
    s = sub.add_parser("repair", parents=[common], help="finish or roll back an interrupted switch")
    g = s.add_mutually_exclusive_group()
    g.add_argument("--forward", action="store_const", const="forward", dest="direction")
    g.add_argument("--rollback", action="store_const", const="back", dest="direction")
    sub.add_parser("doctor", aliases=["scan", "check"],
                   help="list what a profile does not cover (project CLAUDE.md, env logins, Desktop MCP...)")
    sub.add_parser("where", help="show the paths in use")
    s = sub.add_parser("trash", help="list (or --empty) the trash")
    s.add_argument("--empty", action="store_true")
    return p


def main(argv: Optional[List[str]] = None, proc_finder: Optional[Callable[[], List[Proc]]] = None) -> int:
    if sys.version_info < (3, 8):  # pragma: no cover
        print(MESSAGES["python_too_old"][0])
        return 1
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")  # type: ignore[attr-defined]
        except Exception:
            pass
    args = build_parser().parse_args(argv)
    set_lang(args.lang)
    paths = Paths(args.store)
    interactive = sys.stdin.isatty() and sys.stdout.isatty()
    opts = Options(
        force=getattr(args, "force", False),
        dry_run=getattr(args, "dry_run", False),
        yes=args.yes,
        interactive=interactive,
        quit_desktop=getattr(args, "quit_desktop", False),
        proc_finder=proc_finder or (lambda: find_claude_processes(paths)),
    )
    cmd = args.cmd or ("menu" if interactive else "status")
    try:
        if cmd == "menu":
            if not interactive:
                status(paths)
            else:
                menu(paths, opts)
        elif cmd in ("status", "list", "ls"):
            status(paths)
        elif cmd == "init":
            with StoreLock(paths):
                st = load_state(paths)
                if st:
                    say(t("already_initialized", name=st["active"]))
                else:
                    init_state(paths, args.name)
                    say(t("initialized", name=args.name))
        elif cmd == "clean":
            clean(paths, opts, name=args.name, old_name=args.old_name, reset=args.reset, copy_items=args.copy,
                  no_account_sync=args.no_account_sync, keep_api=args.keep_api)
        elif cmd in ("switch", "use"):
            switch_to(paths, args.name, opts)
        elif cmd == "toggle":
            toggle(paths, opts)
        elif cmd == "new":
            new_profile(paths, args.name, opts, copy_items=args.copy, switch=args.switch,
                        no_account_sync=args.no_account_sync, keep_api=args.keep_api)
        elif cmd == "clone":
            new_profile(paths, args.name, opts, clone_from=args.source)
        elif cmd == "reset":
            reset_profile(paths, args.name, opts, copy_items=args.copy, no_account_sync=args.no_account_sync)
        elif cmd == "rename":
            rename_profile(paths, args.old, args.new)
        elif cmd in ("delete", "rm"):
            delete_profile(paths, args.name, opts)
        elif cmd == "export":
            st = ensure_state(paths)
            export_profile(paths, args.name or st["active"], out=args.output, include_secrets=args.include_secrets)
        elif cmd == "import":
            import_profile(paths, args.file, args.name)
        elif cmd == "repair":
            repair(paths, opts, direction=args.direction)
        elif cmd in ("doctor", "scan", "check"):
            for line in doctor(paths):
                say(line)
        elif cmd == "where":
            where(paths)
        elif cmd == "trash":
            trash(paths, opts, empty=args.empty)
    except SwitchError as e:
        print(str(e), file=sys.stderr)
        return e.exit_code
    except KeyboardInterrupt:
        print(t("cancelled"), file=sys.stderr)
        return 130
    return 0


if __name__ == "__main__":
    sys.exit(main())
