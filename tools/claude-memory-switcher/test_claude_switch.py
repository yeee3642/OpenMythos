"""Tests for claude_switch.py.  Run:  python3 -m pytest tools/claude-memory-switcher -q"""
from __future__ import annotations

import json
import os
import sys
import zipfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import claude_switch as cs  # noqa: E402


# --------------------------------------------------------------------------
# fixtures / helpers
# --------------------------------------------------------------------------


def snapshot(root: Path) -> dict:
    """relative path -> file bytes / 'DIR' / 'LINK:target' for a whole tree."""
    out = {}
    if not os.path.lexists(root):
        return out
    if root.is_file():
        return {".": root.read_bytes()}
    for dirpath, dirnames, filenames in os.walk(root):
        for d in dirnames:
            p = Path(dirpath) / d
            rel = p.relative_to(root).as_posix()
            out[rel] = "LINK:" + os.readlink(p) if p.is_symlink() else "DIR"
        for f in filenames:
            p = Path(dirpath) / f
            rel = p.relative_to(root).as_posix()
            out[rel] = "LINK:" + os.readlink(p) if p.is_symlink() else p.read_bytes()
    return out


def live_snapshot(home: Path) -> dict:
    return {
        "claude": snapshot(home / ".claude"),
        "claude.json": snapshot(home / ".claude.json"),
    }


@pytest.fixture
def home(tmp_path, monkeypatch):
    h = tmp_path / "home"
    h.mkdir()
    monkeypatch.setenv("HOME", str(h))
    monkeypatch.setenv("USERPROFILE", str(h))
    for var in ("CLAUDE_CONFIG_DIR", "CLAUDE_SWITCH_HOME"):
        monkeypatch.delenv(var, raising=False)
    cs.set_lang("en")
    return h


def populate(home: Path) -> None:
    """A realistic-looking Claude Code installation with memory."""
    c = home / ".claude"
    (c / "projects" / "-Users-me-app" / "memory").mkdir(parents=True)
    (c / "projects" / "-Users-me-app" / "memory" / "MEMORY.md").write_text("remember: tabs\n")
    (c / "projects" / "-Users-me-app" / "abc.jsonl").write_text('{"type":"user"}\n')
    (c / "CLAUDE.md").write_text("# my global memory\n")
    (c / "settings.json").write_text('{"model": "opus"}\n')
    (c / "agents").mkdir()
    (c / "agents" / "reviewer.md").write_text("agent\n")
    (c / "todos").mkdir()
    (c / "history.jsonl").write_text('{"display":"hi"}\n')
    (c / ".credentials.json").write_text('{"claudeAiOauth": {"accessToken": "secret"}}')
    (c / "local").mkdir()
    (c / "local" / "claude").write_text("#!/bin/sh\n")
    (c / "ide").mkdir()
    (c / "ide" / "1234.lock").write_text("{}")
    (home / ".claude.json").write_text(json.dumps({
        "oauthAccount": {"emailAddress": "me@example.com", "organizationUuid": "org"},
        "userID": "abc123",
        "hasCompletedOnboarding": True,
        "lastOnboardingVersion": "2.0.0",
        "theme": "dark",
        "installMethod": "native",
        "primaryApiKey": "sk-ant-secret",
        "projects": {str(home / "app"): {"allowedTools": ["Bash"], "hasTrustDialogAccepted": True}},
        "mcpServers": {"fs": {"command": "npx"}},
        "numStartups": 42,
    }))


def opts(**kw) -> cs.Options:
    kw.setdefault("proc_finder", lambda: [])
    kw.setdefault("yes", True)
    return cs.Options(**kw)


def P(home: Path) -> cs.Paths:
    return cs.Paths()


def pinned_snapshot(home: Path) -> dict:
    c = home / ".claude"
    return {n: snapshot(c / n) for n in cs.PINNED_CHILDREN}


# --------------------------------------------------------------------------
# the one-click flow
# --------------------------------------------------------------------------


def test_clean_parks_everything_and_keeps_login(home):
    populate(home)
    before = live_snapshot(home)
    pinned = pinned_snapshot(home)
    paths = P(home)

    cs.clean(paths, opts())

    state = cs.load_state(paths)
    assert state["active"] == "clean"
    assert state["previous"] == "original"
    # memory and history are gone from the live location...
    c = home / ".claude"
    assert not (c / "CLAUDE.md").exists()
    assert not (c / "projects").exists()
    assert not (c / "settings.json").exists()
    # ...but shared items stayed exactly where they were
    assert pinned_snapshot(home) == pinned
    # the fresh global config keeps the login + onboarding, nothing else
    cfg = json.loads((home / ".claude.json").read_text())
    assert cfg["oauthAccount"]["emailAddress"] == "me@example.com"
    assert cfg["hasCompletedOnboarding"] is True
    assert cfg["theme"] == "dark"
    assert "projects" not in cfg and "mcpServers" not in cfg and "numStartups" not in cfg
    # the old state is parked intact
    parked = paths.profile_dir("original")
    assert snapshot(parked / "claude.json")["."] == before["claude.json"]["."]
    assert (parked / "config" / "projects" / "-Users-me-app" / "memory" / "MEMORY.md").read_text() == "remember: tabs\n"
    assert not (parked / "config" / ".credentials.json").exists()


def test_round_trip_restores_byte_for_byte(home):
    populate(home)
    before = live_snapshot(home)
    paths = P(home)
    cs.clean(paths, opts())
    # use the clean profile a bit
    (home / ".claude" / "CLAUDE.md").write_text("clean memory\n")
    cs.switch_to(paths, "original", opts())
    assert live_snapshot(home) == before
    # and the clean profile kept its own changes
    cs.switch_to(paths, "clean", opts())
    assert (home / ".claude" / "CLAUDE.md").read_text() == "clean memory\n"
    for _ in range(3):
        cs.toggle(paths, opts())
        cs.toggle(paths, opts())
    cs.switch_to(paths, "original", opts())
    assert live_snapshot(home) == before


def test_store_is_empty_for_active_profile(home):
    populate(home)
    paths = P(home)
    cs.clean(paths, opts())
    assert cs.only_empty_dirs(paths.profile_dir("clean"))
    cs.switch_to(paths, "original", opts())
    assert cs.only_empty_dirs(paths.profile_dir("original"))


def test_clean_twice_reuses_profile_and_reset_empties_it(home):
    populate(home)
    paths = P(home)
    cs.clean(paths, opts())
    (home / ".claude" / "CLAUDE.md").write_text("clean memory\n")
    cs.switch_to(paths, "original", opts())
    cs.clean(paths, opts())  # back to the same clean profile
    assert (home / ".claude" / "CLAUDE.md").read_text() == "clean memory\n"
    cs.clean(paths, opts(), reset=True)  # reset while active
    state = cs.load_state(paths)
    assert state["active"] == "clean" and state["previous"] == "original"
    assert set(state["profiles"]) == {"original", "clean"}
    assert not (home / ".claude" / "CLAUDE.md").exists()
    trashed = list((paths.store / cs.TRASH_DIR).glob("*-clean"))
    assert len(trashed) == 1
    assert (trashed[0] / "config" / "CLAUDE.md").read_text() == "clean memory\n"
    # the original is untouched
    cs.switch_to(paths, "original", opts())
    assert (home / ".claude" / "CLAUDE.md").read_text() == "# my global memory\n"


def test_reset_inactive_profile(home):
    populate(home)
    paths = P(home)
    cs.clean(paths, opts())
    (home / ".claude" / "notes.md").write_text("x")
    cs.switch_to(paths, "original", opts())
    cs.reset_profile(paths, "clean", opts())
    state = cs.load_state(paths)
    assert state["active"] == "clean"
    assert not (home / ".claude" / "notes.md").exists()


def test_clean_on_machine_without_claude_state(home):
    paths = P(home)
    cs.clean(paths, opts())
    state = cs.load_state(paths)
    assert state["active"] == "clean"
    assert json.loads((home / ".claude.json").read_text()) == {}
    cs.switch_to(paths, "original", opts())
    assert not (home / ".claude.json").exists()


def test_copy_items_into_clean_profile(home):
    populate(home)
    paths = P(home)
    cs.clean(paths, opts(), copy_items=["settings.json", "agents", "nope", ".credentials.json", "../etc"])
    c = home / ".claude"
    assert (c / "settings.json").read_text() == '{"model": "opus"}\n'
    assert (c / "agents" / "reviewer.md").exists()
    assert not (c / "CLAUDE.md").exists()


def test_dry_run_changes_nothing(home):
    populate(home)
    before = live_snapshot(home)
    paths = P(home)
    cs.clean(paths, opts(dry_run=True))
    assert live_snapshot(home) == before
    assert not paths.store.exists()
    cs.clean(paths, opts())
    mid = live_snapshot(home)
    cs.switch_to(paths, "original", opts(dry_run=True))
    assert live_snapshot(home) == mid


# --------------------------------------------------------------------------
# safety
# --------------------------------------------------------------------------


def test_refuses_while_claude_running(home):
    populate(home)
    before = live_snapshot(home)
    paths = P(home)
    running = lambda: [cs.Proc(99, "cli", "claude", "/usr/local/bin/claude")]  # noqa: E731
    with pytest.raises(cs.ClaudeRunning):
        cs.clean(paths, opts(proc_finder=running))
    assert live_snapshot(home) == before
    assert not paths.store.exists()  # nothing created before the check
    cs.clean(paths, opts(proc_finder=running, force=True))
    assert cs.load_state(paths)["active"] == "clean"


def test_failure_mid_switch_rolls_back(home, monkeypatch):
    populate(home)
    before = live_snapshot(home)
    paths = P(home)
    real_move = cs.move
    calls = {"n": 0}

    def flaky_move(src, dst):
        calls["n"] += 1
        if calls["n"] == 4:
            raise PermissionError(13, "file is in use", str(src))
        return real_move(src, dst)

    monkeypatch.setattr(cs, "move", flaky_move)
    with pytest.raises(cs.SwitchError) as ei:
        cs.clean(paths, opts())
    assert "rolled back" in str(ei.value)
    monkeypatch.setattr(cs, "move", real_move)
    assert live_snapshot(home) == before
    state = cs.load_state(paths)
    assert state["active"] == "original" and state["journal"] is None
    # and a later attempt works
    cs.clean(paths, opts())
    assert cs.load_state(paths)["active"] == "clean"


@pytest.mark.parametrize("direction", ["forward", "back"])
@pytest.mark.parametrize("crash_at", list(range(10)))
def test_repair_after_hard_crash(home, monkeypatch, direction, crash_at):
    populate(home)
    before = live_snapshot(home)
    paths = P(home)
    cs.clean(paths, opts())
    # Same names in both profiles: a path is the source of one move and the
    # destination of a later one.
    (home / ".claude" / "CLAUDE.md").write_text("clean memory\n")
    (home / ".claude" / "settings.json").write_text("{}\n")
    clean_before = live_snapshot(home)
    cs.switch_to(paths, "original", opts())
    assert live_snapshot(home) == before
    state = cs.load_state(paths)
    plan = cs.plan_switch(paths, state, "clean")
    assert len(plan.moves) == 10
    if crash_at >= len(plan.moves):
        pytest.skip("crash point beyond plan")

    def boom(i):
        if i == crash_at:
            raise KeyboardInterrupt  # stands in for a crash / power loss

    def no_rollback(paths_, journal):
        raise RuntimeError("simulated crash: no rollback")

    monkeypatch.setattr(cs, "_rollback", no_rollback)
    with pytest.raises(cs.SwitchError):
        cs.execute_plan(paths, state, plan, fail_hook=boom)
    monkeypatch.undo()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))

    state = cs.load_state(paths)
    assert state["journal"] is not None
    with pytest.raises(cs.SwitchError):  # other operations are blocked
        cs.switch_to(paths, "clean", opts())

    cs.repair(paths, opts(), direction=direction)
    state = cs.load_state(paths)
    assert state["journal"] is None
    if direction == "back":
        assert state["active"] == "original"
        assert live_snapshot(home) == before
    else:
        assert state["active"] == "clean"
        assert live_snapshot(home) == clean_before
        cs.switch_to(paths, "original", opts())
        assert live_snapshot(home) == before
    assert not (paths.store / cs.CONFLICTS_DIR).exists()


@pytest.mark.parametrize("fail_at", list(range(1, 11)))
def test_rollback_at_every_step(home, monkeypatch, fail_at):
    """An error at any move leaves both profiles exactly as they were."""
    populate(home)
    paths = P(home)
    cs.clean(paths, opts())
    (home / ".claude" / "CLAUDE.md").write_text("clean memory\n")
    (home / ".claude" / "settings.json").write_text("{}\n")
    clean_live = live_snapshot(home)
    cs.switch_to(paths, "original", opts())
    orig_live = live_snapshot(home)
    stored_clean = snapshot(paths.profile_dir("clean"))
    real_move = cs.move
    calls = {"n": 0}

    def flaky_move(src, dst):
        calls["n"] += 1
        if calls["n"] == fail_at:
            raise PermissionError(13, "file is in use", str(src))
        return real_move(src, dst)

    monkeypatch.setattr(cs, "move", flaky_move)
    with pytest.raises(cs.SwitchError):
        cs.switch_to(paths, "clean", opts())
    monkeypatch.setattr(cs, "move", real_move)
    assert live_snapshot(home) == orig_live
    assert snapshot(paths.profile_dir("clean")) == stored_clean
    assert cs.load_state(paths)["journal"] is None
    assert not (paths.store / cs.CONFLICTS_DIR).exists()
    cs.switch_to(paths, "clean", opts())
    assert live_snapshot(home) == clean_live


def test_repair_keeps_files_recreated_during_crash(home, monkeypatch):
    """Claude re-created ~/.claude.json after it was parked; nothing may be lost."""
    populate(home)
    before = live_snapshot(home)
    paths = P(home)
    cs.init_state(paths)
    cs.create_profile(paths, cs.load_state(paths), "clean")
    state = cs.load_state(paths)
    plan = cs.plan_switch(paths, state, "clean")
    n_park = sum(1 for s, _ in plan.moves if s.parent == home / ".claude" or s == home / ".claude.json")
    assert 0 < n_park < len(plan.moves)

    def boom(i):
        if i == n_park:  # everything parked, nothing unparked yet
            (home / ".claude.json").write_text('{"recreated": true}')
            raise KeyboardInterrupt

    monkeypatch.setattr(cs, "_rollback", lambda p, j: (_ for _ in ()).throw(RuntimeError("crash")))
    with pytest.raises(cs.SwitchError):
        cs.execute_plan(paths, state, plan, fail_hook=boom)
    monkeypatch.undo()
    monkeypatch.setenv("HOME", str(home))
    cs.repair(paths, opts(), direction="back")
    assert live_snapshot(home) == before
    kept = list((paths.store / cs.CONFLICTS_DIR).rglob(".claude.json"))
    assert kept and json.loads(kept[0].read_text()) == {"recreated": True}


def test_leftovers_in_active_store_are_moved_aside(home):
    populate(home)
    paths = P(home)
    cs.clean(paths, opts())
    stray = paths.profile_dir("clean") / "config" / "CLAUDE.md"
    stray.parent.mkdir(parents=True, exist_ok=True)
    stray.write_text("stray")
    (home / ".claude" / "CLAUDE.md").write_text("live")
    cs.switch_to(paths, "original", opts())
    assert (paths.profile_dir("clean") / "config" / "CLAUDE.md").read_text() == "live"
    aside = list((paths.store / cs.CONFLICTS_DIR).rglob("CLAUDE.md"))
    assert [p.read_text() for p in aside] == ["stray"]


def test_pinned_items_in_store_are_not_unparked(home):
    populate(home)
    paths = P(home)
    cs.clean(paths, opts())
    bogus = paths.profile_dir("original") / "config" / ".credentials.json"
    bogus.write_text("old token")
    cs.switch_to(paths, "original", opts())
    assert "secret" in (home / ".claude" / ".credentials.json").read_text()
    assert bogus.read_text() == "old token"


def test_config_location_change_is_refused(home, monkeypatch, tmp_path):
    populate(home)
    paths = P(home)
    cs.clean(paths, opts())
    other = tmp_path / "other-config"
    other.mkdir()
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(other))
    with pytest.raises(cs.SwitchError) as ei:
        cs.switch_to(cs.Paths(), "original", opts())
    assert "changed" in str(ei.value)


def test_store_inside_config_dir_refused(home):
    populate(home)
    paths = cs.Paths(store=str(home / ".claude" / "profiles"))
    with pytest.raises(cs.SwitchError):
        cs.clean(paths, opts())


def test_lock_blocks_second_instance(home):
    paths = P(home)
    with cs.StoreLock(paths):
        with pytest.raises(cs.SwitchError):
            with cs.StoreLock(paths):
                pass
    # a stale lock (dead pid) is taken over
    paths.store.mkdir(parents=True, exist_ok=True)
    (paths.store / cs.LOCK_FILE).write_text(json.dumps({"pid": 999999999, "time": 0}))
    with cs.StoreLock(paths):
        pass


def test_claude_config_dir_env(home, monkeypatch, tmp_path):
    cfg = tmp_path / "cfg"
    (cfg / "projects").mkdir(parents=True)
    (cfg / "CLAUDE.md").write_text("mem")
    (cfg / ".claude.json").write_text('{"hasCompletedOnboarding": true, "projects": {}}')
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(cfg))
    paths = cs.Paths()
    assert paths.global_config == cfg / ".claude.json"
    before = snapshot(cfg)
    cs.clean(paths, opts())
    assert sorted(os.listdir(cfg)) == [".claude.json"]
    assert json.loads((cfg / ".claude.json").read_text()) == {"hasCompletedOnboarding": True}
    cs.switch_to(paths, "original", opts())
    assert snapshot(cfg) == before


# --------------------------------------------------------------------------
# profile management
# --------------------------------------------------------------------------


def test_new_rename_delete_and_trash(home):
    populate(home)
    paths = P(home)
    cs.new_profile(paths, "work", opts())
    cs.new_profile(paths, "實驗", opts(), switch=True)
    assert cs.load_state(paths)["active"] == "實驗"
    with pytest.raises(cs.SwitchError):
        cs.new_profile(paths, "WORK", opts())  # case-insensitive clash
    cs.rename_profile(paths, "實驗", "lab")
    state = cs.load_state(paths)
    assert state["active"] == "lab"
    with pytest.raises(cs.SwitchError):
        cs.delete_profile(paths, "lab", opts())  # active
    trash = cs.delete_profile(paths, "work", opts())
    assert trash.exists() and "work" not in cs.load_state(paths)["profiles"]
    cs.trash(paths, opts(), empty=True)
    assert not trash.exists()


def test_case_only_rename(home):
    paths = P(home)
    cs.new_profile(paths, "work", opts())
    cs.rename_profile(paths, "work", "Work")
    assert "Work" in cs.load_state(paths)["profiles"]
    assert paths.profile_dir("Work").is_dir()


def test_clone_active_and_inactive(home):
    populate(home)
    paths = P(home)
    cs.init_state(paths)
    cs.new_profile(paths, "copy1", opts(), clone_from="original")
    stored = paths.profile_dir("copy1")
    assert (stored / "config" / "CLAUDE.md").read_text() == "# my global memory\n"
    assert not (stored / "config" / ".credentials.json").exists()
    assert not (stored / "config" / "local").exists()
    cs.switch_to(paths, "copy1", opts())
    cs.new_profile(paths, "copy2", opts(), clone_from="original")  # inactive source
    assert (paths.profile_dir("copy2") / "claude.json").exists()


@pytest.mark.parametrize("bad", ["", ".hidden", "_x", "a/b", "a\\b", "CON", "nul.txt", "x" * 65, "trail.", "a:b"])
def test_invalid_names(bad):
    cs.set_lang("en")
    with pytest.raises(cs.SwitchError):
        cs.validate_name(bad)


@pytest.mark.parametrize("good", ["original", "clean", "舊記憶", "work-2", "a.b_c"])
def test_valid_names(good):
    assert cs.validate_name(good) == good


# --------------------------------------------------------------------------
# export / import
# --------------------------------------------------------------------------


def test_export_import_round_trip(home, tmp_path):
    populate(home)
    paths = P(home)
    cs.init_state(paths)
    z = cs.export_profile(paths, "original", out=str(tmp_path / "b.zip"))
    with zipfile.ZipFile(z) as zf:
        names = zf.namelist()
        assert "config/CLAUDE.md" in names
        assert not any(".credentials.json" in n for n in names)
        assert not any(n.startswith("config/local") for n in names)
        cfg = json.loads(zf.read("claude.json"))
        assert "primaryApiKey" not in cfg and cfg["userID"] == "abc123"
    name = cs.import_profile(paths, str(z), "restored")
    stored = paths.profile_dir(name)
    assert (stored / "config" / "projects" / "-Users-me-app" / "memory" / "MEMORY.md").read_text() == "remember: tabs\n"
    cs.switch_to(paths, "restored", opts())
    assert (home / ".claude" / "CLAUDE.md").read_text() == "# my global memory\n"


def test_import_rejects_zip_slip(home, tmp_path):
    paths = P(home)
    z = tmp_path / "evil.zip"
    with zipfile.ZipFile(z, "w") as zf:
        zf.writestr(cs.MANIFEST_NAME, json.dumps({"tool": "claude-switch", "profile": "evil"}))
        zf.writestr("config/../../../escape.txt", "x")
        zf.writestr("/abs.txt", "x")
        zf.writestr("config/.credentials.json", "stolen")
        zf.writestr("other/file.txt", "x")
        zf.writestr("config/ok.md", "fine")
    name = cs.import_profile(paths, str(z))
    stored = paths.profile_dir(name)
    assert (stored / "config" / "ok.md").read_text() == "fine"
    assert not (stored / "config" / ".credentials.json").exists()
    assert not list(tmp_path.rglob("escape.txt"))
    assert not (stored / "other").exists()


def test_import_rejects_foreign_zip(home, tmp_path):
    paths = P(home)
    z = tmp_path / "x.zip"
    with zipfile.ZipFile(z, "w") as zf:
        zf.writestr("hello.txt", "x")
    with pytest.raises(cs.SwitchError):
        cs.import_profile(paths, str(z))


# --------------------------------------------------------------------------
# account sync
# --------------------------------------------------------------------------


def test_account_follows_shared_login(home):
    populate(home)
    paths = P(home)
    cs.clean(paths, opts())
    cfg = json.loads((home / ".claude.json").read_text())
    cfg["oauthAccount"] = {"emailAddress": "new@example.com"}
    (home / ".claude.json").write_text(json.dumps(cfg))
    cs.switch_to(paths, "original", opts())
    cfg = json.loads((home / ".claude.json").read_text())
    assert cfg["oauthAccount"] == {"emailAddress": "new@example.com"}
    assert cfg["projects"]  # the rest of the original is intact


# --------------------------------------------------------------------------
# process detection
# --------------------------------------------------------------------------

MAC_COMM = """\
  501 /Applications/Claude.app/Contents/MacOS/Claude
  502 /Applications/Claude.app/Contents/Frameworks/Claude Helper (Renderer).app/Contents/MacOS/Claude Helper (Renderer)
  600 /Users/me/.local/bin/claude
  601 node
  602 /bin/zsh
  603 /bin/sh
  604 /Users/me/Library/Application Support/Claude/claude-code/2.0.1/claude
  605 /Users/me/.local/share/claude/versions/2.1.3
  606 python3
  607 node
  608 /usr/bin/vim
"""
MAC_ARGS = """\
  501 /Applications/Claude.app/Contents/MacOS/Claude
  502 /Applications/Claude.app/Contents/Frameworks/Claude Helper (Renderer).app/Contents/MacOS/Claude Helper (Renderer) --type=renderer
  600 claude --resume
  601 node /usr/local/lib/node_modules/@anthropic-ai/claude-code/cli.js
  602 -zsh
  603 /bin/sh -c ln -sf /opt/claude-code/bin/claude /usr/bin/claude
  604 /Users/me/Library/Application Support/Claude/claude-code/2.0.1/claude --output-format stream-json
  605 claude
  606 python3 claude_switch.py clean
  607 node /Users/me/project/server.js
  608 vim /Users/me/notes/claude
"""


def test_parse_ps_mac():
    procs = {p.pid: p.kind for p in cs.parse_ps(MAC_COMM, MAC_ARGS, own_pid=606)}
    assert procs == {501: "desktop", 502: "desktop", 600: "cli", 601: "cli", 604: "cli", 605: "cli"}


def test_parse_ps_linux_short_comm():
    comm = "  10 claude\n  11 node\n  12 bash\n"
    args = "  10 /opt/claude-code/bin/claude --verbose\n  11 node /usr/local/bin/claude\n  12 bash -c claude\n"
    assert {p.pid: p.kind for p in cs.parse_ps(comm, args, own_pid=1)} == {10: "cli", 11: "cli"}


def test_parse_windows_cim():
    data = [
        {"ProcessId": 1, "Name": "claude.exe",
         "ExecutablePath": r"C:\Users\me\AppData\Local\AnthropicClaude\app-0.14.0\claude.exe", "CommandLine": ""},
        {"ProcessId": 2, "Name": "claude.exe",
         "ExecutablePath": r"C:\Users\me\.local\bin\claude.exe", "CommandLine": "claude"},
        {"ProcessId": 3, "Name": "node.exe", "ExecutablePath": r"C:\nodejs\node.exe",
         "CommandLine": r'"node" "C:\Users\me\AppData\Roaming\npm\node_modules\@anthropic-ai\claude-code\cli.js"'},
        {"ProcessId": 4, "Name": "node.exe", "ExecutablePath": r"C:\nodejs\node.exe", "CommandLine": "node server.js"},
        {"ProcessId": 5, "Name": "claude.exe",
         "ExecutablePath": r"C:\Program Files\WindowsApps\Claude_1.0.0_x64__abc\app\claude.exe", "CommandLine": ""},
    ]
    procs = {p.pid: p.kind for p in cs.parse_windows_cim(json.dumps(data), own_pid=0)}
    assert procs == {1: "desktop", 2: "cli", 3: "cli", 5: "desktop"}
    one = cs.parse_windows_cim(json.dumps(data[1]), own_pid=0)  # single object, not a list
    assert [p.pid for p in one] == [2]


def test_parse_tasklist():
    out = '"claude.exe","4242","Console","1","100,000 K"\n"explorer.exe","1","Console","1","1 K"\n'
    assert [(p.pid, p.kind) for p in cs.parse_tasklist(out, own_pid=0)] == [(4242, "unknown")]


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------


def test_cli_end_to_end(home, capsys):
    populate(home)
    before = live_snapshot(home)
    no_procs = lambda: []  # noqa: E731
    assert cs.main(["--lang", "en", "status"], proc_finder=no_procs) == 0
    assert cs.main(["--lang", "en", "clean"], proc_finder=no_procs) == 0
    assert cs.main(["--lang", "en", "toggle"], proc_finder=no_procs) == 0
    assert live_snapshot(home) == before
    assert cs.main(["--lang", "zh", "switch", "clean"], proc_finder=no_procs) == 0
    assert cs.main(["--lang", "en", "switch", "nope"], proc_finder=no_procs) == 1
    running = lambda: [cs.Proc(1, "desktop", "Claude")]  # noqa: E731
    assert cs.main(["--lang", "en", "switch", "original"], proc_finder=running) == 3
    assert cs.main(["--lang", "en", "switch", "original"], proc_finder=no_procs) == 0
    assert live_snapshot(home) == before
    out = capsys.readouterr().out
    assert "original" in out and "切換完成" in out


# --------------------------------------------------------------------------
# findings from the Claude Code docs / binary
# --------------------------------------------------------------------------


def test_legacy_config_json_is_switched_and_used_for_seed(home):
    populate(home)
    c = home / ".claude"
    (c / ".config.json").write_text(json.dumps({"oauthAccount": {"emailAddress": "legacy@x"}, "projects": {"/a": {}}}))
    before = live_snapshot(home)
    paths = P(home)
    assert paths.effective_config() == c / ".config.json"
    cs.clean(paths, opts())
    assert not (c / ".config.json").exists()  # parked with the rest
    seeded = json.loads((home / ".claude.json").read_text())
    assert seeded["oauthAccount"] == {"emailAddress": "legacy@x"} and "projects" not in seeded
    cs.switch_to(paths, "original", opts())
    assert live_snapshot(home) == before


def test_seed_copies_cached_feature_flags_only(home):
    populate(home)
    cfg = json.loads((home / ".claude.json").read_text())
    cfg.update({"cachedGrowthBookFeatures": {"tengu_windows_credman": True}, "cachedChangelog": "x",
                "firstStartTime": "t"})
    (home / ".claude.json").write_text(json.dumps(cfg))
    cs.clean(P(home), opts())
    seeded = json.loads((home / ".claude.json").read_text())
    assert seeded["cachedGrowthBookFeatures"] == {"tengu_windows_credman": True}
    assert "cachedChangelog" not in seeded and "firstStartTime" not in seeded


def test_runtime_items_stay_put(home):
    populate(home)
    c = home / ".claude"
    (c / "sessions").mkdir()
    (c / "sessions" / "123.json").write_text("{}")
    (c / "daemon.lock").write_text("1")
    cs.clean(P(home), opts())
    assert (c / "sessions" / "123.json").exists() and (c / "daemon.lock").exists()


def test_no_account_sync_writes_settings(home):
    populate(home)
    cs.clean(P(home), opts(), copy_items=["settings.json"], no_account_sync=True)
    settings = json.loads((home / ".claude" / "settings.json").read_text())
    assert settings == {"model": "opus", "syncClaudeAiSkills": False, "syncClaudeAiPlugins": False,
                        "disableClaudeAiConnectors": True}


def test_session_files_detect_running_claude(home):
    sdir = home / ".claude" / "sessions"
    sdir.mkdir(parents=True)
    (sdir / ("%d.json" % os.getppid())).write_text(json.dumps({"pid": os.getppid(), "cwd": "/w",
                                                               "entrypoint": "cli"}))
    (sdir / "999999999.json").write_text(json.dumps({"pid": 999999999}))  # dead
    (sdir / "broken.json").write_text("{")
    procs = cs.session_processes(home / ".claude")
    assert [p.pid for p in procs] == [os.getppid()]


def test_retention_warning(home, capsys):
    populate(home)
    old = home / ".claude" / "projects" / "-Users-me-app" / "abc.jsonl"
    os.utime(old, (1_000_000_000, 1_000_000_000))  # 2001
    paths = P(home)
    cs.clean(paths, opts())
    capsys.readouterr()
    cs.switch_to(paths, "original", opts())
    assert "older than 30 days" in capsys.readouterr().out
    (home / ".claude" / "settings.json").write_text('{"cleanupPeriodDays": 100000}')
    cs.switch_to(paths, "clean", opts())
    cs.switch_to(paths, "original", opts())
    assert "older than" not in capsys.readouterr().out


def test_doctor(home, monkeypatch):
    populate(home)
    (home / ".claude" / "settings.json").write_text(json.dumps({
        "autoMemoryDirectory": "~/elsewhere/memory", "env": {"CLAUDE_CONFIG_DIR": "/x"}}))
    (home / "app").mkdir()
    (home / "app" / "CLAUDE.md").write_text("project memory")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk")
    out = "\n".join(cs.doctor(P(home)))
    assert "elsewhere" in out and "CLAUDE_CONFIG_DIR=/x" in out and "ANTHROPIC_API_KEY" in out
    assert str(home / "app" / "CLAUDE.md") in out


def test_messages_have_both_languages_and_matching_fields():
    import string

    for key, (en, zh) in cs.MESSAGES.items():
        assert en and zh, key
        f_en = {f for _, f, _, _ in string.Formatter().parse(en) if f}
        f_zh = {f for _, f, _, _ in string.Formatter().parse(zh) if f}
        assert f_en == f_zh, key
