"""The filesystem half of strategy.py.

Kept apart from tests/test_strategy.py on purpose: the model's tests are
pure, these need tmp_path, and a filesystem quirk should never look like a
validation bug.
"""

from __future__ import annotations

import json

import pytest

from strategy import ControlError, Strategy, StrategyStore, validate_name


@pytest.fixture
def store(tmp_path, monkeypatch) -> StrategyStore:
    """A store over tmp_path, whose templates all exist.

    save() runs validated(), so the fixture points TEMPLATE_DIR at a
    directory holding the files config.ACTIONS names - otherwise every save
    in this file would fail for a reason that has nothing to do with the
    store.
    """
    import config

    templates = tmp_path / "templates"
    templates.mkdir()
    for action in config.ACTIONS:
        (templates / action.template).write_bytes(b"")
    monkeypatch.setattr(config, "TEMPLATE_DIR", templates)
    return StrategyStore(tmp_path / "strategies")


def test_saving_then_loading_returns_an_equal_strategy(store) -> None:
    original = Strategy.from_config("mine")
    store.save(original)
    assert store.load("mine") == original


def test_save_creates_the_directory(tmp_path, store) -> None:
    # First run of a fresh clone: strategies/ does not exist yet, and a save
    # must not be the thing that discovers that.
    store.save(Strategy.from_config("mine"))
    assert (tmp_path / "strategies" / "mine.json").is_file()


def test_saved_json_is_human_readable(store) -> None:
    # These files are meant to be read in a diff and edited by hand, so the
    # formatting is part of the contract, not an accident of json.dumps.
    store.save(Strategy.from_config("mine"))
    text = store.path_for("mine").read_text()
    assert text.endswith("\n")
    assert "\n  " in text  # indented, not one line


def test_names_are_sorted_and_exclude_the_active_pointer(store) -> None:
    store.save(Strategy.from_config("zeta"))
    store.save(Strategy.from_config("alpha"))
    (store.directory / ".active").write_text("alpha\n")
    assert store.names() == ["alpha", "zeta"]


def test_names_is_empty_before_anything_is_saved(store) -> None:
    assert store.names() == []


def test_loading_an_absent_strategy_names_the_field(store) -> None:
    with pytest.raises(ControlError) as caught:
        store.load("ghost")
    assert caught.value.field == "name"


def test_loading_unparseable_json_names_the_file(store) -> None:
    store.directory.mkdir(parents=True, exist_ok=True)
    store.path_for("broken").write_text("{not json")
    with pytest.raises(ControlError) as caught:
        store.load("broken")
    assert caught.value.field == "name"
    assert "broken" in str(caught.value)


def test_save_is_atomic(store, monkeypatch) -> None:
    """A crash mid-write must not leave a profile that fails to parse.

    Simulated by making os.replace raise: save()'s finally clause always
    unlinks the temp file, so it never survives a failed replace either -
    what this proves is that the real path is left untouched, which is
    exactly the guarantee a write-then-rename gives and a write-in-place
    does not.
    """
    import os

    store.save(Strategy.from_config("mine"))
    before = store.path_for("mine").read_text()

    def boom(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(os, "replace", boom)
    original = Strategy.from_config("mine")
    with pytest.raises(OSError):
        store.save(Strategy.from_dict({**original.to_dict(), "interval": 9.0}))
    assert store.path_for("mine").read_text() == before


def test_save_leaves_no_temp_files_behind(store) -> None:
    store.save(Strategy.from_config("mine"))
    assert [p.name for p in store.directory.iterdir() if p.suffix != ".json"] == []


def test_save_rejects_a_strategy_with_a_missing_template(store, monkeypatch) -> None:
    import config

    monkeypatch.setattr(config, "TEMPLATE_DIR", store.directory / "empty")
    with pytest.raises(ControlError):
        store.save(Strategy.from_config("mine"))


@pytest.mark.parametrize(
    "name",
    [
        "../escape",
        "/etc/passwd",
        "a/b",
        "",
        "x" * 65,
        "has space",
        "dot.name",
        "..",
        "mine\n",  # `$` matches before a trailing "\n"; must use fullmatch
        "tab\tname",
    ],
)
def test_unsafe_names_are_refused_before_they_become_paths(name: str) -> None:
    """A name arrives off a URL and becomes a filename.

    /api/unknown/{name} already learned this: resolve-and-check is the
    fallback, refusing the name outright is the fix.
    """
    with pytest.raises(ControlError) as caught:
        validate_name(name)
    assert caught.value.field == "name"


@pytest.mark.parametrize("name", ["default", "crit-build", "early_game", "T5", "x" * 64])
def test_reasonable_names_are_accepted(name: str) -> None:
    assert validate_name(name) == name


def test_the_store_refuses_unsafe_names_on_every_path(store) -> None:
    with pytest.raises(ControlError):
        store.load("../../etc/passwd")
    with pytest.raises(ControlError):
        store.path_for("../escape")


def test_ensure_seeded_writes_the_shipped_defaults(store) -> None:
    seeded = store.ensure_seeded()
    assert seeded == Strategy.from_config("default")
    assert store.names() == ["default"]
    assert store.active_name() == "default"


def test_ensure_seeded_is_idempotent_and_does_not_overwrite(store) -> None:
    """A second launch must not undo your edits.

    This is the failure that would be worst to discover late: seeding on
    every start would silently reset a tuned profile back to config.ACTIONS.
    """
    store.ensure_seeded()
    edited = Strategy.from_config("default")
    edited = Strategy.from_dict({**edited.to_dict(), "interval": 9.0})
    store.save(edited)

    again = store.ensure_seeded()
    assert again.interval == 9.0
    assert store.load("default").interval == 9.0


def test_ensure_seeded_returns_the_active_profile_not_the_default(store) -> None:
    store.ensure_seeded()
    store.save(Strategy.from_config("crit"))
    store.set_active("crit")
    assert store.ensure_seeded().name == "crit"


def test_ensure_seeded_recovers_from_an_active_pointer_at_a_deleted_file(store) -> None:
    """The pointer can outlive its target - someone deletes a file by hand.

    Falling back to any surviving profile beats refusing to start: the bot
    is more useful running the wrong strategy than not running.
    """
    store.ensure_seeded()
    store.save(Strategy.from_config("crit"))
    store.set_active("crit")
    store.path_for("crit").unlink()

    recovered = store.ensure_seeded()
    assert recovered.name == "default"
    assert store.active_name() == "default"


def test_set_active_persists_across_stores(store) -> None:
    store.ensure_seeded()
    store.save(Strategy.from_config("crit"))
    store.set_active("crit")
    # A fresh store over the same directory is what a restart looks like.
    assert StrategyStore(store.directory).active_name() == "crit"


def test_set_active_refuses_a_name_with_no_file(store) -> None:
    store.ensure_seeded()
    with pytest.raises(ControlError) as caught:
        store.set_active("ghost")
    assert caught.value.field == "name"
    assert store.active_name() == "default"


def test_delete_removes_a_profile(store) -> None:
    store.ensure_seeded()
    store.save(Strategy.from_config("crit"))
    store.delete("crit")
    assert store.names() == ["default"]


def test_delete_refuses_the_active_profile(store) -> None:
    store.ensure_seeded()
    store.save(Strategy.from_config("crit"))
    with pytest.raises(ControlError) as caught:
        store.delete("default")
    assert caught.value.field == "name"
    assert store.names() == ["crit", "default"]


def test_delete_refuses_the_last_profile(store) -> None:
    # Both guards exist because both leave the bot with no policy to load,
    # which has no recovery short of hand-editing the directory.
    store.ensure_seeded()
    store.set_active("default")
    with pytest.raises(ControlError):
        store.delete("default")


def test_delete_refuses_an_absent_profile(store) -> None:
    store.ensure_seeded()
    store.save(Strategy.from_config("crit"))
    with pytest.raises(ControlError):
        store.delete("ghost")
