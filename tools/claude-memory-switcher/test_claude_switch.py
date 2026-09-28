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
    """Everything a profile consists of, minus the tool's own marker file."""
    claude = snapshot(home / ".claude")
    claude.pop(cs.MARKER_FILE, None)
    return {
        "claude": claude,
        "claude.json": snapshot(home / ".claude.json"),
        "desktop": snapshot(desktop_sessions(home)),
    }


def isolated_env(h: Path) -> dict:
    """Every location the tool may touch, pointed inside the fake home, so the
    tests can never move a developer's real Claude data."""
    return {
        "HOME": str(h),
        "USERPROFILE": str(h),
        "APPDATA": str(h / "AppData" / "Roaming"),
        "LOCALAPPDATA": str(h / "AppData" / "Local"),
        "XDG_CONFIG_HOME": str(h / ".config"),
    }


# Variables the tool reacts to; the developer's (or CI's) own values must not
# leak into the tests.
CLEARED_ENV = ("CLAUDE_CONFIG_DIR", "CLAUDE_SWITCH_HOME", "CLAUDE_CODE_PLUGIN_CACHE_DIR",
               "CLAUDE_CODE_REMOTE_MEMORY_DIR", "CLAUDE_SECURESTORAGE_CONFIG_DIR", "ANTHROPIC_BASE_URL") + \
    cs.AUTH_ENV_VARS


@pytest.fixture
def home(tmp_path, monkeypatch):
    h = tmp_path / "home"
    h.mkdir()
    for k, v in isolated_env(h).items():
        monkeypatch.setenv(k, v)
    for var in CLEARED_ENV:
        monkeypatch.delenv(var, raising=False)
    cs.set_lang("en")
    return h


def desktop_key() -> str:
    return "mac" if sys.platform == "darwin" else "win" if os.name == "nt" else "linux"


def desktop_sessions(h: Path) -> Path:
    """Where Claude Desktop keeps its Code session list on this OS, for a fake
    home h laid out by isolated_env().  Computed from h alone -- never from the
    current environment -- so populate() can't write into a real home."""
    if sys.platform == "darwin":
        base = h / "Library" / "Application Support" / "Claude"
    elif os.name == "nt":
        base = h / "AppData" / "Roaming" / "Claude"
    else:
        base = h / ".config" / "Claude"
    return base / cs.DESKTOP_SESSIONS


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
    (c / ".device-keys.json").write_text('{"k": 1}')
    (c / "chrome").mkdir()
    (c / "chrome" / "chrome-native-host").write_text("#!/bin/sh\n")
    d = desktop_sessions(home) / "acct" / "org"
    d.mkdir(parents=True)
    (d / "local_1.json").write_text('{"cliSessionId": "abc"}')
    (d / "scheduled-tasks.json").write_text("[]")
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
@pytest.mark.parametrize("crash_at", list(range(13)))
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
    assert len(plan.moves) == 13  # 9 parked + 4 brought back (incl. the marker)

    def boom(i):
        if i == crash_at:
            raise KeyboardInterrupt  # stands in for a crash / power loss

    def no_rollback(*_a):
        raise RuntimeError("simulated crash: no rollback")

    monkeypatch.setattr(cs, "_rollback", no_rollback)
    with pytest.raises(cs.SwitchError):
        cs.execute_plan(paths, state, plan, fail_hook=boom)
    monkeypatch.undo()
    for k, v in isolated_env(home).items():
        monkeypatch.setenv(k, v)

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


@pytest.mark.parametrize("fail_at", list(range(1, 14)))
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
    assert len(cs.plan_switch(paths, cs.load_state(paths), "clean").moves) == 13
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


@pytest.mark.parametrize("fail_at", [4, 7, 11])
@pytest.mark.parametrize("undo_fail_after", [0, 1, 3])
@pytest.mark.parametrize("asked", ["forward", "back"])
def test_interrupted_rollback_is_resumed_not_reversed(home, monkeypatch, fail_at, undo_fail_after, asked):
    """A switch fails, then its rollback fails part-way too.  Whatever the user
    picks afterwards, repair must finish the rollback, and both profiles must
    come out exactly as they were."""
    populate(home)
    paths = P(home)
    cs.clean(paths, opts())
    (home / ".claude" / "CLAUDE.md").write_text("clean memory\n")
    (home / ".claude" / "settings.json").write_text("{}\n")
    cs.switch_to(paths, "original", opts())
    orig_live = live_snapshot(home)
    stored_clean = snapshot(paths.profile_dir("clean"))
    real_move = cs.move
    calls = {"n": 0, "undo": 0}

    def flaky(src, dst):
        calls["n"] += 1
        if calls["n"] == fail_at:
            raise PermissionError(13, "in use", str(src))
        if calls["n"] > fail_at:  # we are rolling back now
            if calls["undo"] == undo_fail_after:
                calls["undo"] += 1
                raise PermissionError(13, "in use during rollback", str(src))
            calls["undo"] += 1
        return real_move(src, dst)

    monkeypatch.setattr(cs, "move", flaky)
    with pytest.raises(cs.SwitchError) as ei:
        cs.switch_to(paths, "clean", opts())
    monkeypatch.setattr(cs, "move", real_move)
    state = cs.load_state(paths)
    if state["journal"] is None:  # the rollback happened to finish anyway
        assert "rolled back" in str(ei.value)
    else:
        assert state["journal"]["phase"] == "rollback"
        cs.repair(paths, opts(), direction=asked)
    state = cs.load_state(paths)
    assert state["journal"] is None and state["active"] == "original"
    assert live_snapshot(home) == orig_live
    assert snapshot(paths.profile_dir("clean")) == stored_clean
    assert not (paths.store / cs.CONFLICTS_DIR).exists()


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

    monkeypatch.setattr(cs, "_rollback", lambda *a: (_ for _ in ()).throw(RuntimeError("crash")))
    with pytest.raises(cs.SwitchError):
        cs.execute_plan(paths, state, plan, fail_hook=boom)
    monkeypatch.undo()
    for k, v in isolated_env(home).items():
        monkeypatch.setenv(k, v)
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
    paths.store.mkdir(parents=True, exist_ok=True)
    lock = paths.store / cs.LOCK_FILE
    # a live holder (our parent process) with the file still being held
    lock.write_text(json.dumps({"pid": os.getppid(), "time": 0, "nonce": "theirs"}))
    if os.name != "nt":  # on Windows only an open handle proves liveness
        with pytest.raises(cs.SwitchError) as ei:
            with cs.StoreLock(paths):
                pass
        assert str(lock) in str(ei.value)
    # a dead holder is taken over, however young the lock is
    lock.write_text(json.dumps({"pid": 999999999, "time": 9e12, "nonce": "theirs"}))
    with cs.StoreLock(paths):
        assert json.loads(lock.read_text())["pid"] == os.getpid()
    assert not lock.exists()
    # a lock left behind with our own (reused) pid is stale
    lock.write_text(json.dumps({"pid": os.getpid(), "time": 0}))
    with cs.StoreLock(paths):
        pass
    # an empty / half-written lock is respected for a grace period
    lock.write_text("")
    with pytest.raises(cs.SwitchError):
        with cs.StoreLock(paths):
            pass
    os.utime(lock, (1_000_000_000, 1_000_000_000))
    with cs.StoreLock(paths):
        pass


def test_lock_exit_only_removes_its_own_lock(home):
    paths = P(home)
    with cs.StoreLock(paths) as lk:
        (paths.store / cs.LOCK_FILE).write_text(json.dumps({"pid": 1, "nonce": "someone-else"}))
    assert (paths.store / cs.LOCK_FILE).exists()


def test_missing_state_uses_bak_or_rebuilds_from_marker(home):
    populate(home)
    paths = P(home)
    cs.clean(paths, opts())
    (home / ".claude" / "CLAUDE.md").write_text("clean memory")
    paths.state_file.unlink()  # .bak (one save behind) still there
    st = cs.load_state(paths)
    assert st["active"] == "clean" and "original" in st["profiles"]
    for f in (paths.state_file, paths.state_file.with_name(cs.STATE_FILE + ".bak")):
        if f.exists():
            f.unlink()
    st = cs.load_state(paths)  # rebuilt from folders + marker
    assert st["active"] == "clean" and set(st["profiles"]) == {"original", "clean"}
    cs.switch_to(paths, "original", opts())
    assert (home / ".claude" / "CLAUDE.md").read_text() == "# my global memory\n"
    # without a usable marker it refuses instead of re-initialising
    (home / ".claude" / cs.MARKER_FILE).unlink()
    paths.state_file.unlink()
    paths.state_file.with_name(cs.STATE_FILE + ".bak").unlink()
    with pytest.raises(cs.SwitchError):
        cs.clean(paths, opts())
    assert (paths.profile_dir("clean") / "config" / "CLAUDE.md").read_text() == "clean memory"


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
    assert sorted(os.listdir(cfg)) == [".claude-switch.json", ".claude.json"]
    assert json.loads((cfg / ".claude.json").read_text()) == {"hasCompletedOnboarding": True}
    cs.switch_to(paths, "original", opts())
    after = snapshot(cfg)
    after.pop(cs.MARKER_FILE)
    assert after == before


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
  609 /Users/me/.local/share/claude/ClaudeCode.app/Contents/MacOS/claude
  610 /Library/Frameworks/Python.framework/Versions/3.9/Resources/Python.app/Contents/MacOS/Python
  611 /Applications/Claude.app/Contents/Helpers/disclaimer
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
  609 claude daemon
  610 claude --resume
  611 /Applications/Claude.app/Contents/Helpers/disclaimer /Users/me/Library/Application Support/Claude/claude-code/2.1/claude
"""


def test_parse_ps_mac():
    procs = {p.pid: p.kind for p in cs.parse_ps(MAC_COMM, MAC_ARGS, own_pid=606)}
    assert procs == {501: "desktop", 502: "desktop", 600: "cli", 601: "cli", 604: "cli", 605: "cli",
                     609: "cli", 610: "cli", 611: "desktop"}


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


def test_windows_desktop_helpers():
    exe = r"C:\Program Files\WindowsApps\Claude_1.2.3.0_x64__pzs8sxrjxfjjc\app\Claude.exe"
    assert cs.msix_app_id(exe) == "Claude_pzs8sxrjxfjjc!Claude"
    assert cs.msix_app_id(r"C:\Users\me\AppData\Local\AnthropicClaude\app-1.0\claude.exe") is None
    procs = [cs.Proc(10, "desktop", "Claude.exe", exe, 4), cs.Proc(11, "desktop", "Claude.exe", exe, 10),
             cs.Proc(12, "cli", "claude.exe", "x", 10), cs.Proc(13, "desktop", "Claude.exe", exe, 11)]
    assert cs.desktop_roots(procs) == [10]
    data = [{"ProcessId": 10, "ParentProcessId": 4, "Name": "Claude.exe", "ExecutablePath": exe, "CommandLine": ""}]
    assert cs.parse_windows_cim(json.dumps(data), own_pid=0)[0].ppid == 4


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


# --------------------------------------------------------------------------
# real processes / real CLI (no mocks)
# --------------------------------------------------------------------------

SCRIPT = str(Path(__file__).resolve().parent / "claude_switch.py")


def test_process_table_is_readable_on_this_os():
    assert cs.scan_processes() is not None


def test_real_cli_round_trip_in_subprocess(tmp_path):
    import subprocess

    home = tmp_path / "home"
    home.mkdir()
    populate(home)
    before = live_snapshot(home)
    env = dict(os.environ, CLAUDE_SWITCH_LANG="en", PYTHONUTF8="1", **isolated_env(home))
    for var in CLEARED_ENV:
        env.pop(var, None)
    # Real detection; on a dev machine where Claude itself is running, force.
    extra = ["--force"] if cs.scan_processes() else []

    def run(*args):
        r = subprocess.run([sys.executable, SCRIPT, *args], env=env, capture_output=True, text=True,
                           encoding="utf-8", timeout=120)
        assert r.returncode == 0, (args, r.stdout, r.stderr)
        return r.stdout

    run("clean", *extra)
    assert not (home / ".claude" / "CLAUDE.md").exists()
    assert "clean" in run("--lang", "zh", "status")
    run("toggle", *extra)
    assert live_snapshot(home) == before
    run("doctor")


@pytest.mark.skipif(not os.environ.get("CI"), reason="spawns a fake 'claude' executable; CI only")
def test_detects_a_real_process_named_claude(tmp_path):
    """Start a process whose executable is called claude(.exe) and check that
    the platform's process scan reports it as Claude Code."""
    import shutil
    import subprocess
    import time

    if os.name == "nt":
        # python.exe needs its DLLs, so the copy must sit next to it.
        exe = Path(sys.executable).resolve()
        fake = exe.with_name("claude.exe")
        shutil.copy2(str(exe), str(fake))
        cmd = [str(fake), "-c", "import time; time.sleep(60)"]
    else:
        fake = tmp_path / "bin" / "claude"
        fake.parent.mkdir()
        # copyfile, not copy2: macOS system binaries carry flags (SF_RESTRICTED)
        # that an ordinary user may not copy.
        shutil.copyfile("/bin/sleep", str(fake))
        os.chmod(str(fake), 0o755)
        cmd = [str(fake), "60"]
    proc = subprocess.Popen(cmd)
    try:
        found = []
        for _ in range(20):
            found = [p for p in (cs.scan_processes() or []) if p.pid == proc.pid]
            if found:
                break
            time.sleep(0.5)
        if not found and os.name != "nt":
            for fmt in ("comm=", "args="):
                out = subprocess.run(["ps", "-p", str(proc.pid), "-o", fmt], capture_output=True, text=True).stdout
                print("ps -o %s -> %r" % (fmt, out))
        assert found and found[0].kind == "cli", cs.scan_processes()
        home = tmp_path / "home"
        home.mkdir()
        populate(home)
        env = dict(os.environ, CLAUDE_SWITCH_LANG="en", **isolated_env(home))
        r = subprocess.run([sys.executable, SCRIPT, "clean"], env=env, capture_output=True, text=True,
                           encoding="utf-8", timeout=120)
        assert r.returncode == 3, (r.stdout, r.stderr)
        assert (home / ".claude" / "CLAUDE.md").exists()
    finally:
        proc.kill()
        proc.wait()
        if os.name == "nt":
            try:
                fake.unlink()
            except OSError:
                pass


def test_desktop_session_list_switches_with_the_profile(home):
    assert cs.Paths().desktop_app_dirs()[0] == (desktop_key(), desktop_sessions(home).parent)
    populate(home)
    before = live_snapshot(home)
    paths = P(home)
    cs.clean(paths, opts())
    assert not desktop_sessions(home).exists()  # Desktop shows no old Code sessions
    key = desktop_key()
    parked = paths.profile_dir("original") / "desktop" / key / "acct" / "org" / "local_1.json"
    assert parked.read_text() == '{"cliSessionId": "abc"}'
    # Desktop creates a new session while "clean" is active
    new = desktop_sessions(home) / "acct" / "org" / "local_2.json"
    new.parent.mkdir(parents=True)
    new.write_text("{}")
    cs.switch_to(paths, "original", opts())
    assert live_snapshot(home) == before
    cs.switch_to(paths, "clean", opts())
    assert new.read_text() == "{}"


def test_desktop_data_from_another_os_stays_parked(home):
    populate(home)
    paths = P(home)
    cs.clean(paths, opts())
    foreign = paths.profile_dir("original") / "desktop" / "some-other-os"
    foreign.mkdir(parents=True)
    (foreign / "x.json").write_text("{}")
    cs.switch_to(paths, "original", opts())
    assert (foreign / "x.json").exists()


def test_global_config_variants_are_switched_and_seeded(home):
    populate(home)
    (home / ".claude-custom-oauth.json").write_text(json.dumps(
        {"oauthAccount": {"emailAddress": "fed@x"}, "projects": {"/p": {}}}))
    before = live_snapshot(home)
    variant_before = (home / ".claude-custom-oauth.json").read_text()
    paths = P(home)
    cs.clean(paths, opts())
    seeded = json.loads((home / ".claude-custom-oauth.json").read_text())
    assert seeded == {"oauthAccount": {"emailAddress": "fed@x"}}
    assert not (home / ".claude-staging-oauth.json").exists()
    cs.switch_to(paths, "original", opts())
    assert live_snapshot(home) == before
    assert (home / ".claude-custom-oauth.json").read_text() == variant_before


def test_export_import_includes_desktop_sessions(home, tmp_path):
    populate(home)
    paths = P(home)
    cs.init_state(paths)
    z = cs.export_profile(paths, "original", out=str(tmp_path / "d.zip"))
    key = desktop_key()
    with zipfile.ZipFile(z) as zf:
        assert "desktop/%s/acct/org/local_1.json" % key in zf.namelist()
        assert not any("device-keys" in n or n.startswith("config/chrome") for n in zf.namelist())
    cs.import_profile(paths, str(z), "copy")
    assert (paths.profile_dir("copy") / "desktop" / key / "acct" / "org" / "local_1.json").exists()


def test_marker_detects_a_second_store(home, tmp_path):
    populate(home)
    cs.clean(P(home), opts())
    other = cs.Paths(store=str(tmp_path / "other-store"))
    with pytest.raises(cs.SwitchError) as ei:
        cs.clean(other, opts())  # init must refuse: ~/.claude belongs to the first store
    assert "another" in str(ei.value)


def test_marker_mismatch_refuses_switch(home):
    populate(home)
    paths = P(home)
    cs.clean(paths, opts())
    m = json.loads((home / ".claude" / cs.MARKER_FILE).read_text())
    m["id"] = "somebody-else"
    (home / ".claude" / cs.MARKER_FILE).write_text(json.dumps(m))
    with pytest.raises(cs.SwitchError):
        cs.switch_to(paths, "original", opts())
    cs.switch_to(paths, "original", opts(force=True))
    assert json.loads((home / ".claude" / cs.MARKER_FILE).read_text())["profile"] == "original"


def test_rename_keeps_marker_in_sync(home):
    populate(home)
    paths = P(home)
    cs.clean(paths, opts())
    cs.rename_profile(paths, "clean", "乾淨版")
    cs.switch_to(paths, "original", opts())  # would refuse if the marker were stale
    cs.switch_to(paths, "乾淨版", opts())


def test_nfd_name_finds_nfc_profile(home):
    import unicodedata

    paths = P(home)
    cs.new_profile(paths, "café", opts())
    nfd = unicodedata.normalize("NFD", "café")
    assert cs._find_profile(cs.load_state(paths), nfd) == "café"
    with pytest.raises(cs.SwitchError):
        cs.new_profile(paths, nfd.upper(), opts())


def test_fresh_lock_dir_counts_as_running(home):
    paths = P(home)
    lock = home / ".claude.json.lock"
    lock.mkdir()
    assert [p.kind for p in cs.lock_holders(paths)] == ["lock"]
    os.utime(lock, (1_000_000_000, 1_000_000_000))  # stale
    assert cs.lock_holders(paths) == []


def test_sigterm_mid_switch_rolls_back(home, monkeypatch):
    if os.name == "nt":
        pytest.skip("POSIX signals")
    import signal

    populate(home)
    before = live_snapshot(home)
    paths = P(home)

    def boom(i):
        if i == 3:
            os.kill(os.getpid(), signal.SIGTERM)

    cs.init_state(paths)
    cs.create_profile(paths, cs.load_state(paths), "clean")
    state = cs.load_state(paths)
    with pytest.raises(KeyboardInterrupt):
        cs.execute_plan(paths, state, cs.plan_switch(paths, state, "clean"), fail_hook=boom)
    assert live_snapshot(home) == before
    assert cs.load_state(paths)["journal"] is None
    assert signal.getsignal(signal.SIGTERM) == signal.SIG_DFL  # restored


def test_home_backup_file_moves_with_profile(home):
    populate(home)
    (home / ".claude.json.backup").write_text('{"mcpServers": {"old": {}}}')
    paths = P(home)
    cs.clean(paths, opts())
    assert not (home / ".claude.json.backup").exists()
    cs.switch_to(paths, "original", opts())
    assert (home / ".claude.json.backup").read_text() == '{"mcpServers": {"old": {}}}'


def test_home_claude_md_is_part_of_the_profile(home, tmp_path):
    populate(home)
    (home / "CLAUDE.md").write_text("home-level memory")
    before = live_snapshot(home)
    paths = P(home)
    cs.clean(paths, opts())
    assert not (home / "CLAUDE.md").exists()
    z = cs.export_profile(paths, "original", out=str(tmp_path / "h.zip"))
    with zipfile.ZipFile(z) as zf:
        assert zf.read("home/CLAUDE.md") == b"home-level memory"
    cs.switch_to(paths, "original", opts())
    assert (home / "CLAUDE.md").read_text() == "home-level memory"
    assert live_snapshot(home) == before


def test_keep_api_settings(home):
    populate(home)
    (home / ".claude" / "settings.json").write_text(json.dumps({
        "model": "opus", "permissions": {"allow": ["Bash"]}, "apiKeyHelper": "~/bin/key.sh",
        "env": {"ANTHROPIC_BASE_URL": "https://proxy.example", "ANTHROPIC_AUTH_TOKEN": "tok", "DEBUG": "1"}}))
    cfg = json.loads((home / ".claude.json").read_text())
    cfg["env"] = {"HTTPS_PROXY": "http://127.0.0.1:7890"}
    (home / ".claude.json").write_text(json.dumps(cfg))
    paths = P(home)
    env, top = cs.api_settings(paths)
    assert env == {"ANTHROPIC_BASE_URL": "https://proxy.example", "ANTHROPIC_AUTH_TOKEN": "tok",
                   "HTTPS_PROXY": "http://127.0.0.1:7890"}
    assert top == {"apiKeyHelper": "~/bin/key.sh"}
    cs.clean(paths, opts(), keep_api=True, no_account_sync=True)
    new = json.loads((home / ".claude" / "settings.json").read_text())
    assert new["env"] == env and new["apiKeyHelper"] == "~/bin/key.sh"
    assert new["syncClaudeAiSkills"] is False
    assert "model" not in new and "permissions" not in new


def test_api_settings_not_kept_by_default_noninteractive(home):
    populate(home)
    (home / ".claude" / "settings.json").write_text('{"env": {"ANTHROPIC_BASE_URL": "https://x"}}')
    paths = P(home)
    cs.clean(paths, opts())
    assert not (home / ".claude" / "settings.json").exists()
    assert not any("ANTHROPIC_BASE_URL" in n for n in cs.doctor(paths))  # clean profile has none
    cs.switch_to(paths, "original", opts())
    assert any("ANTHROPIC_BASE_URL" in n for n in cs.doctor(paths))


def test_apply_settings_merges_env(tmp_path):
    f = tmp_path / "settings.json"
    f.write_text('{"env": {"A": "1"}, "x": 1}')
    cs._apply_settings(f, {"env": {"B": "2"}, "y": 2})
    assert json.loads(f.read_text()) == {"env": {"A": "1", "B": "2"}, "x": 1, "y": 2}


# --------------------------------------------------------------------------
# findings from the adversarial review (UX / docs / macOS lenses)
# --------------------------------------------------------------------------


def test_dry_run_never_writes(home, tmp_path):
    populate(home)
    before = live_snapshot(home)
    paths = P(home)
    no = lambda: []  # noqa: E731
    assert cs.main(["switch", "original", "--dry-run"], proc_finder=no) == 0
    assert cs.main(["--dry-run", "reset", "original"], proc_finder=no) == 0
    assert cs.main(["toggle", "--dry-run"], proc_finder=no) == 0
    assert not paths.store.exists() and live_snapshot(home) == before
    cs.clean(paths, opts())
    snap = snapshot(paths.store)
    assert cs.main(["new", "work", "--dry-run"], proc_finder=no) == 0
    assert cs.main(["clone", "original", "c1", "--dry-run"], proc_finder=no) == 0
    assert cs.main(["export", "original", "--dry-run"], proc_finder=no) == 2  # not supported: refused
    assert not paths.profile_dir("work").exists() and not paths.profile_dir("c1").exists()
    # repair --dry-run shows, does not repair
    state = cs.load_state(paths)
    plan = cs.plan_switch(paths, state, "original")

    def boom(i):
        if i == 3:
            raise KeyboardInterrupt

    import pytest as _pt
    orig_rb = cs._rollback
    cs._rollback = lambda *a: (_ for _ in ()).throw(RuntimeError("crash"))
    try:
        with _pt.raises(cs.SwitchError):
            cs.execute_plan(paths, state, plan, fail_hook=boom)
    finally:
        cs._rollback = orig_rb
    mid = live_snapshot(home)
    assert cs.main(["repair", "--dry-run"], proc_finder=no) == 0
    assert live_snapshot(home) == mid and cs.load_state(paths)["journal"] is not None
    assert cs.main(["repair", "--rollback"], proc_finder=no) == 0


def test_toggle_goes_clean_when_only_one_profile(home):
    populate(home)
    paths = P(home)
    cs.init_state(paths)  # e.g. after exporting or browsing the menu first
    cs.toggle(paths, opts())
    assert cs.load_state(paths)["active"] == "clean"
    cs.toggle(paths, opts())
    assert cs.load_state(paths)["active"] == "original"


def test_export_without_setup_does_not_set_up(home, tmp_path):
    populate(home)
    paths = P(home)
    z = cs.export_profile(paths, "original", out=str(tmp_path / "newdir") + os.sep)
    assert z.parent == tmp_path / "newdir" and z.suffix == ".zip"
    assert not paths.store.exists()
    assert not (home / ".claude" / cs.MARKER_FILE).exists()


def test_export_redacts_tokens_in_settings_and_mcp(home, tmp_path):
    populate(home)
    (home / ".claude" / "settings.json").write_text(json.dumps({
        "model": "opus", "env": {"ANTHROPIC_API_KEY": "sk-SETTINGS", "ANTHROPIC_BASE_URL": "https://x", "DEBUG": "1"}}))
    cfg = json.loads((home / ".claude.json").read_text())
    cfg["mcpServers"] = {"gh": {"command": "gh-mcp", "env": {"GITHUB_TOKEN": "ghp_SECRET", "LOG": "1"}},
                         "web": {"type": "http", "url": "https://m", "headers": {"Authorization": "Bearer SECRET"}}}
    (home / ".claude.json").write_text(json.dumps(cfg))
    (home / ".claude.json.backup").write_text(json.dumps({"primaryApiKey": "sk-BACKUP"}))
    paths = P(home)
    z = cs.export_profile(paths, "original", out=str(tmp_path / "r.zip"))
    with zipfile.ZipFile(z) as zf:
        blob = b"".join(zf.read(n) for n in zf.namelist() if not n.endswith("/"))
        settings = json.loads(zf.read("config/settings.json"))
    for secret in (b"sk-SETTINGS", b"ghp_SECRET", b"Bearer SECRET", b"sk-BACKUP", b"sk-ant-secret"):
        assert secret not in blob, secret
    assert settings["env"]["ANTHROPIC_BASE_URL"] == "https://x" and settings["env"]["DEBUG"] == "1"
    z2 = cs.export_profile(paths, "original", out=str(tmp_path / "s.zip"), include_secrets=True)
    with zipfile.ZipFile(z2) as zf:
        assert b"ghp_SECRET" in zf.read("claude.json")


def test_import_bad_zip_is_a_clean_error(home, tmp_path):
    populate(home)
    paths = P(home)
    z = cs.export_profile(paths, "original", out=str(tmp_path / "ok.zip"))
    data = bytearray(z.read_bytes())
    i = data.find(b"# my global memory")
    bad = tmp_path / "bad.zip"
    if i < 0:  # compressed: damage the middle of the archive instead
        i = len(data) // 3
    data[i:i + 8] = b"XXXXXXXX"
    bad.write_bytes(bytes(data))
    with pytest.raises(cs.SwitchError):
        cs.import_profile(paths, str(bad), "broken")
    assert not paths.profile_dir("broken").exists()
    assert not list(paths.profiles_dir.glob("_import-*"))


def test_dragged_paths(tmp_path):
    f = tmp_path / "My Files" / "b (1).zip"
    f.parent.mkdir()
    f.write_text("x")
    assert cs.dragged_path('"%s"' % f) == str(f)
    if os.name != "nt":
        escaped = str(f).replace(" ", "\\ ").replace("(", "\\(").replace(")", "\\)") + " "
        assert cs.dragged_path(escaped) == str(f)


def test_options_before_or_after_command(home):
    populate(home)
    no = lambda: []  # noqa: E731
    assert cs.main(["status", "--lang", "zh"], proc_finder=no) == 0
    assert cs.main(["--force", "toggle"], proc_finder=no) == 0
    assert cs.main(["toggle", "--yes", "--force"], proc_finder=no) == 0
    cs.delete_profile(P(home), "clean", opts())
    assert cs.main(["trash", "--empty", "--yes"], proc_finder=no) == 0
    assert cs.main(["--yes", "trash", "--empty"], proc_finder=no) == 0


def test_reset_asks_first(home, monkeypatch):
    populate(home)
    paths = P(home)
    cs.clean(paths, opts())
    (home / ".claude" / "CLAUDE.md").write_text("weeks of work")
    monkeypatch.setattr("builtins.input", lambda *_: "n")
    with pytest.raises(cs.SwitchError):
        cs.reset_profile(paths, "clean", opts(interactive=True, yes=False))
    assert (home / ".claude" / "CLAUDE.md").read_text() == "weeks of work"
    monkeypatch.setattr("builtins.input", lambda *_: "y")
    cs.reset_profile(paths, "clean", opts(interactive=True, yes=False))
    assert not (home / ".claude" / "CLAUDE.md").exists()


def test_doctor_lists_projects_known_to_any_profile_and_not_switched_ones(home):
    populate(home)
    proj = home / "proj"
    proj.mkdir()
    (proj / "CLAUDE.md").write_text("project memory")
    (proj / ".mcp.json").write_text("{}")
    (home / "CLAUDE.md").write_text("home memory")
    cfg = json.loads((home / ".claude.json").read_text())
    cfg["projects"] = {str(proj): {}, str(home): {}}
    (home / ".claude.json").write_text(json.dumps(cfg))
    paths = P(home)
    cs.clean(paths, opts())  # the clean profile knows no projects
    found = [str(p) for p in cs.scan_projects(paths)]
    assert str(proj / "CLAUDE.md") in found and str(proj / ".mcp.json") in found
    assert str(home / "CLAUDE.md") not in found and str(home / ".claude") not in found


def test_corrupt_state_and_backup_is_a_clean_error(home):
    populate(home)
    paths = P(home)
    cs.clean(paths, opts())
    paths.state_file.write_text("{")
    paths.state_file.with_name(cs.STATE_FILE + ".bak").write_text("{")
    assert cs.main(["status"], proc_finder=lambda: []) == 1


def test_clean_warns_when_creation_options_cannot_apply(home, capsys):
    populate(home)
    paths = P(home)
    cs.clean(paths, opts())
    cs.switch_to(paths, "original", opts())
    capsys.readouterr()
    cs.clean(paths, opts(), copy_items=["agents"], no_account_sync=True)
    assert "already exists" in capsys.readouterr().out


def test_messages_have_both_languages_and_matching_fields():
    import string

    for key, (en, zh) in cs.MESSAGES.items():
        assert en and zh, key
        f_en = {f for _, f, _, _ in string.Formatter().parse(en) if f}
        f_zh = {f for _, f, _, _ in string.Formatter().parse(zh) if f}
        assert f_en == f_zh, key
