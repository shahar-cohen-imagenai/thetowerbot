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
    ["../escape", "/etc/passwd", "a/b", "", "x" * 65, "has space", "dot.name", ".."],
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
