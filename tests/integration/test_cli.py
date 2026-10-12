"""The `catalog` CLI end to end (click CliRunner)."""

import json
import re
import threading

import pytest
from click.testing import CliRunner

from catalog.cli import cli, table
from catalog.services.operations import acquire_write_lock
from helpers import DATA, make_record, scalar


@pytest.fixture
def run(seeded, db_urls, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", db_urls.owner)
    runner = CliRunner()

    def invoke(*args, expect=0, input=None):
        result = runner.invoke(cli, list(args), input=input, catch_exceptions=False)
        assert result.exit_code == expect, result.output
        return result.output

    return invoke


def test_help(run):
    out = run("--help")
    for group in ("db", "import", "undo", "merge", "duplicates", "seller", "serve"):
        assert group in out


def test_db_commands(run):
    assert "legacy catalog loaded: yes" in run("db", "current")
    assert "0002_initial_data" in run("db", "history")


def test_db_upgrade_on_new_database(make_database, monkeypatch, tmp_path):
    from helpers import make_legacy_sqlite

    urls = make_database("catalog_cli_upgrade")
    runner = CliRunner()
    refused = runner.invoke(cli, ["--database-url", urls.owner, "db", "upgrade"])
    assert refused.exit_code == 1 and "make psqlmigrate" in refused.output
    legacy = make_legacy_sqlite(tmp_path / "l.db", [(1, "Thing", "Acme", "Tools")])
    ok = runner.invoke(
        cli, ["--database-url", urls.owner, "db", "upgrade", "--legacy-sqlite", str(legacy)]
    )
    assert ok.exit_code == 0, ok.output
    assert "verified: True" in ok.output
    again = runner.invoke(
        cli, ["--database-url", urls.owner, "db", "upgrade", "--legacy-sqlite", str(legacy)]
    )
    assert "already migrated" in again.output
    missing = runner.invoke(
        cli, ["--database-url", urls.owner, "db", "upgrade", "--legacy-sqlite", "/nope.db"]
    )
    assert missing.exit_code == 1 and "not found" in missing.output
    down = runner.invoke(cli, ["--database-url", urls.owner, "db", "downgrade", "0001_schema"])
    assert down.exit_code == 1 and "irreversible" in down.output


def test_import_flow(run, write_json):
    path = write_json(
        [
            make_record("iPhone 15 Pro", "ShopA", "Apple"),
            make_record("Gadget", "ShopB", "Acme", "Toys"),
        ]
    )
    out = run("import", "run", str(path))
    assert "offers_created" in out and "ShopA" in out
    md5 = run("import", "hash", str(path)).strip()
    assert len(md5) == 32
    assert md5 in run("import", "list")
    assert run("import", "list", "--format", "md5").strip() == md5
    assert md5 in run("import", "list", "--seller", "shopb", "--since", "1h")
    run("import", "list", "--seller", "nobody", expect=1)
    assert "import undo --md5" in run("import", "show", "--md5", md5)
    assert "ShopA" in run("import", "show", latest_import_id(run))
    run("import", "show", expect=2)
    # same file again: refused, then forced
    assert "already imported" in run("import", "run", str(path), expect=1)
    assert "unchanged" in run("import", "run", str(path), "--force")


def latest_import_id(run):
    return run("import", "list").splitlines()[2].split()[0]


def test_import_options(run, write_json):
    path = write_json(
        [make_record("Thing", "Ghost", record_id="66778899-0011-4223-3445-567788990234")]
    )
    out = run("import", "run", str(path), "--dry-run", "--id-validation", "strict")
    assert "dry run" in out and "strict;versions=4,7" in out
    out = run("import", "run", str(path), "--dry-run", "--id-versions", "7")
    assert "lenient;versions=7" in out
    run("import", "run", str(path), "--id-versions", "9", expect=2)
    out = run("import", "run", str(path), "--no-create-sellers")
    assert re.search(r"skipped_records\s+1", out)
    assert "UNKNOWN_SELLER" in out


def test_import_errors(run, tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text("{}")
    assert "JSON array" in run("import", "run", str(bad), expect=1)


def test_undo_and_revert(run, write_json, seeded):
    path = write_json([make_record("Gadget", "ShopA", "Acme")])
    run("import", "run", str(path))
    md5 = run("import", "hash", str(path)).strip()
    out = run("import", "undo", "--md5", md5, "--dry-run")
    assert "dry run: undo was NOT applied" in out
    out = run("import", "undo", "--md5", md5, input="n\n")
    assert "cancelled" in out
    out = run("import", "undo", "--md5", md5, "--seller", "ShopA", "--yes")
    assert "undo applied" in out
    assert not scalar(seeded, "SELECT active FROM catalog.product WHERE name = 'Gadget'")
    listing = run("undo", "list", "--since", "1d")
    undo_id = listing.splitlines()[2].split()[0]
    assert "revert with" in run("undo", "show", undo_id)
    run("undo", "show", "999", expect=1)
    assert "revert applied" in run("undo", "revert", undo_id, "--yes")
    assert scalar(seeded, "SELECT active FROM catalog.product WHERE name = 'Gadget'")
    run("undo", "revert", undo_id, "--yes", expect=1)  # nothing left
    assert "undo applied" in run("import", "undo", "--all-files", "--since", "1h", "--yes")


@pytest.mark.parametrize(
    ("args", "message"),
    [
        ((), "exactly one of --md5 or --all-files"),
        (("--all-files",), "--all-files requires --since"),
        (("--md5", "abc"), "full 32-character"),
        (("--all-files", "--since", "yesterday"), "invalid time"),
    ],
)
def test_undo_usage_errors(run, args, message):
    assert message in run("import", "undo", *args, expect=2)


def test_undo_errors(run):
    assert "no import found" in run("import", "undo", "--md5", "0" * 32, "--yes", expect=1)
    assert "unknown seller" in run(
        "import", "undo", "--all-files", "--since", "1h", "--seller", "ghost", expect=1
    )


def test_undo_conflict_exit_code(run, write_json):
    rid = make_record("x")["Id"]
    first = write_json([make_record("Gadget A", "ShopA", record_id=rid)])
    run("import", "run", str(first))
    run("import", "run", str(write_json([make_record("Gadget B", "ShopA", record_id=rid)])))
    md5 = run("import", "hash", str(first)).strip()
    out = run("import", "undo", "--md5", md5, "--yes", expect=4)
    assert "conflicting" in out
    out = run("import", "undo", "--md5", md5, "--yes", "--skip-conflicts")
    assert "conflict skipped" in out


def test_lock_busy_exit_code(run, seeded, write_json):
    path = write_json([make_record("Gadget", "ShopA")])
    holding, release = threading.Event(), threading.Event()

    def hold():
        with seeded.begin() as conn:
            acquire_write_lock(conn)
            holding.set()
            release.wait(10)

    worker = threading.Thread(target=hold)
    worker.start()
    holding.wait(10)
    try:
        assert "another write operation" in run("import", "run", str(path), "--no-wait", expect=3)
        run("merge", "product", "--keep", "1", "--duplicate", "2", "--yes", "--no-wait", expect=3)
    finally:
        release.set()
        worker.join()


def test_sellers(run, write_json, monkeypatch):
    run("import", "run", str(write_json([make_record("Gadget", "ShopA")])))
    out = run("seller", "list")
    assert "VTEX" in out and "ShopA" in out
    assert "renamed" in run("seller", "rename", "shopa", "Shop Alpha")
    assert "Shop Alpha" in run("seller", "list", "--show-inactive")
    assert "already uses" in run("seller", "rename", "Shop Alpha", "vtex", expect=1)
    assert "invalid seller name" in run("seller", "rename", "Shop Alpha", "<b>x</b>", expect=1)
    assert "unknown seller" in run("seller", "rename", "ghost", "x", expect=1)
    monkeypatch.setenv("HOUSE_SELLER_NAME", "Marketplace")
    from catalog.config import get_settings

    get_settings.cache_clear()
    assert "warning: HOUSE_SELLER_NAME" in run("seller", "list")


def test_duplicates_and_merges(run, write_json, seeded):
    run(
        "import",
        "run",
        str(
            write_json(
                [
                    make_record("Processor AMD Ryzen 9", "ShopA", "AMD"),
                    make_record("Processador AMD Ryzen 9", "ShopB", "AMD"),
                    make_record("Thing", "ShopA", "Hugo", "Home"),
                    make_record("Other", "ShopA", "Hugo Boss", "Home Decor"),
                ]
            )
        ),
    )
    out = run("duplicates", "list", "--entity", "product")
    assert "catalog merge product --keep" in out
    data = json.loads(run("duplicates", "list", "--entity", "brand", "--format", "json"))
    assert data and "merge_command" in data[0]
    run("duplicates", "list", "--entity", "brand", "--threshold", "2", expect=2)

    keep = scalar(seeded, "SELECT id FROM catalog.product WHERE name = 'Processor AMD Ryzen 9'")
    dup = scalar(seeded, "SELECT id FROM catalog.product WHERE name = 'Processador AMD Ryzen 9'")
    assert "dry run" in run(
        "merge", "product", "--keep", str(keep), "--duplicate", str(dup), "--dry-run"
    )
    assert "merge applied" in run(
        "merge", "product", "--keep", str(keep), "--duplicate", str(dup), "--yes"
    )
    merge_op = scalar(seeded, "SELECT max(id) FROM catalog.operation WHERE kind = 'merge'")
    assert "manual" in run("merge", "list")
    assert "unmerge applied" in run("merge", "undo", str(merge_op), "--yes")
    run("merge", "undo", str(merge_op), "--yes", expect=1)

    hugo = scalar(seeded, "SELECT id FROM catalog.brand WHERE name = 'Hugo'")
    boss = scalar(seeded, "SELECT id FROM catalog.brand WHERE name = 'Hugo Boss'")
    assert "merge applied" in run(
        "merge", "brand", "--keep", str(hugo), "--duplicate", str(boss), "--yes"
    )
    home = scalar(seeded, "SELECT id FROM catalog.category WHERE name = 'home'")
    decor = scalar(seeded, "SELECT id FROM catalog.category WHERE name = 'home decor'")
    assert "merge applied" in run(
        "merge", "category", "--keep", str(home), "--duplicate", str(decor), "--yes"
    )
    run("merge", "category", "--keep", str(home), "--duplicate", str(home), "--yes", expect=1)


def test_brand_merge_conflict_exit_code(run, write_json, seeded):
    run(
        "import",
        "run",
        str(
            write_json(
                [make_record("Gizmo", "ShopA", "Acme"), make_record("Gizmo", "ShopA", "Acme Corp")]
            )
        ),
    )
    keep = scalar(seeded, "SELECT id FROM catalog.brand WHERE name = 'Acme'")
    dup = scalar(seeded, "SELECT id FROM catalog.brand WHERE name = 'Acme Corp'")
    assert "--merge-products" in run(
        "merge", "brand", "--keep", str(keep), "--duplicate", str(dup), "--yes", expect=4
    )
    assert "merge applied" in run(
        "merge", "brand", "--keep", str(keep), "--duplicate", str(dup), "--merge-products", "--yes"
    )


def test_configuration_error(monkeypatch):
    monkeypatch.setenv("ID_VALIDATION_MODE", "paranoid")
    result = CliRunner().invoke(cli, ["db", "current"])
    assert result.exit_code == 1 and "configuration error" in result.output


def test_table_helper():
    out = table([(1, None), (22, "x")], ["id", "name"])
    assert out.splitlines()[0].startswith("id") and "22" in out


def test_sample_file_via_cli(run):
    out = run("import", "run", str(DATA / "ProductEntry.json"))
    assert re.search(r"invalid_records\s+3", out)
