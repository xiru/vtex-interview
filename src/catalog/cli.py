"""``catalog`` command line interface (click).

Exit codes: 0 ok, 1 error, 2 usage error, 3 write lock busy (--no-wait), 4 conflicts.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterable, Sequence
from contextlib import contextmanager
from dataclasses import replace
from typing import Any

import click
from sqlalchemy import Connection, Engine, func, select, update

from catalog import migrate
from catalog.config import (
    HOUSE_SELLER_ID,
    ID_VALIDATION_MODES,
    Settings,
    SettingsError,
    get_settings,
    parse_versions,
)
from catalog.db import get_engine
from catalog.logconfig import configure_logging
from catalog.models import (
    import_batch,
    merge_detail,
    operation,
    seller,
    seller_product,
    undo_detail,
)
from catalog.services import duplicates as dup_service
from catalog.services import merge as merge_service
from catalog.services import undo as undo_service
from catalog.services.importer import (
    DuplicateFileError,
    ImportFileError,
    ImportOptions,
    file_digest,
    run_import,
)
from catalog.services.operations import LockBusyError, acquire_write_lock
from catalog.timewindow import parse_moment

EXIT_ERROR = 1
EXIT_LOCK_BUSY = 3
EXIT_CONFLICT = 4


class CliError(click.ClickException):
    exit_code = EXIT_ERROR


class LockBusyCliError(CliError):
    exit_code = EXIT_LOCK_BUSY


class ConflictCliError(CliError):
    exit_code = EXIT_CONFLICT


# --------------------------------------------------------------------------- helpers
def _settings(ctx: click.Context) -> Settings:
    return ctx.obj["settings"]


def _engine(ctx: click.Context) -> Engine:
    return get_engine(_settings(ctx).database_url)


def table(rows: Iterable[Sequence[Any]], headers: Sequence[str]) -> str:
    """Plain-text table."""
    data = [[("" if v is None else str(v)) for v in row] for row in rows]
    widths = [len(h) for h in headers]
    for row in data:
        widths = [max(w, len(v)) for w, v in zip(widths, row, strict=True)]
    line = "  ".join(h.ljust(w) for h, w in zip(headers, widths, strict=True))
    out = [line, "  ".join("-" * w for w in widths)]
    out += ["  ".join(v.ljust(w) for v, w in zip(row, widths, strict=True)) for row in data]
    return "\n".join(out)


def _moment(value: str | None, option: str):
    if value is None:
        return None
    try:
        return parse_moment(value)
    except ValueError as exc:
        raise click.BadParameter(str(exc), param_hint=option) from exc


def _seller_id(conn: Connection, name: str) -> int:
    found = conn.scalar(
        select(seller.c.id).where(
            func.catalog.name_key(seller.c.name) == func.catalog.name_key(name)
        )
    )
    if found is None:
        raise CliError(f"unknown seller {name!r}")
    return int(found)


def _warn_house_name(conn: Connection, settings: Settings) -> None:
    stored = conn.scalar(select(seller.c.name).where(seller.c.id == HOUSE_SELLER_ID))
    if stored is not None and stored != settings.house_seller_name:
        click.secho(
            f"warning: HOUSE_SELLER_NAME is {settings.house_seller_name!r} but seller "
            f"{HOUSE_SELLER_ID} is named {stored!r}; run "
            f"`catalog seller rename {stored!r} {settings.house_seller_name!r}` to apply it",
            fg="yellow",
            err=True,
        )


@contextmanager
def write_transaction(ctx: click.Context, wait: bool):
    """Transaction holding the catalog write lock; maps lock/conflict errors to exit codes."""
    engine = _engine(ctx)
    with engine.connect() as conn:
        trans = conn.begin()
        try:
            acquire_write_lock(conn, wait)
            yield conn, trans
            if trans.is_active:
                trans.commit()
        except LockBusyError as exc:
            raise LockBusyCliError(str(exc)) from exc
        except undo_service.UndoConflictError as exc:
            raise ConflictCliError(str(exc)) from exc
        except merge_service.MergeConflictError as exc:
            raise ConflictCliError(str(exc)) from exc
        except (undo_service.UndoError, merge_service.MergeError) as exc:
            raise CliError(str(exc)) from exc
        finally:
            if trans.is_active:
                trans.rollback()


def _apply_or_rollback(trans, dry_run: bool, yes: bool, what: str) -> bool:
    if dry_run:
        trans.rollback()
        click.echo(f"dry run: {what} was NOT applied")
        return False
    if not yes and not click.confirm(f"Apply this {what}?", default=False):
        trans.rollback()
        click.echo("cancelled; nothing changed")
        return False
    trans.commit()
    click.echo(f"{what} applied")
    return True


def _print_undo(result: undo_service.UndoResult) -> None:
    click.echo(
        f"undo operation {result.operation_id}: {result.reverted} change(s) reverted "
        f"for operation(s) {', '.join(map(str, result.target_operations))}"
    )
    for line in result.kept[:50]:
        click.echo(f"  kept active: {line}")
    for conflict in result.conflicts[:50]:
        click.echo(f"  conflict skipped: {conflict.describe()}")
    for note in result.notes[:50]:
        click.echo(f"  note: {note}")


no_wait_option = click.option(
    "--no-wait", is_flag=True, help="Fail (exit 3) instead of waiting for another write."
)


# --------------------------------------------------------------------------- root
@click.group(context_settings={"help_option_names": ["-h", "--help"]})
@click.option("--database-url", envvar="DATABASE_URL", help="Owner connection URL.")
@click.option("--log-level", default=None, help="DEBUG, INFO, WARNING, ERROR.")
@click.option("--log-file", default=None, type=click.Path(dir_okay=False), help="Also log here.")
@click.pass_context
def cli(ctx: click.Context, database_url: str | None, log_level: str | None, log_file: str | None):
    """Marketplace catalog consolidation."""
    try:
        settings = get_settings()
    except SettingsError as exc:
        raise CliError(f"configuration error: {exc}") from exc
    if database_url:
        settings = replace(settings, database_url=database_url)
    configure_logging(log_level or settings.log_level, log_file)
    ctx.obj = {"settings": settings}


def main() -> None:  # pragma: no cover - console entry point
    cli(prog_name="catalog")


# --------------------------------------------------------------------------- db
@cli.group()
def db():
    """Database schema migrations."""


@db.command("upgrade")
@click.argument("revision", default="head")
@click.option(
    "--legacy-sqlite",
    type=click.Path(dir_okay=False),
    help="Legacy SQLite catalog to load (initial migration only, opened read-only).",
)
@click.option(
    "--allow-empty",
    is_flag=True,
    default=None,
    help="Allow initializing an empty catalog without the legacy load.",
)
@click.pass_context
def db_upgrade(ctx, revision: str, legacy_sqlite: str | None, allow_empty: bool | None):
    """Apply migrations (PostgreSQL)."""
    url = _settings(ctx).database_url
    try:
        message = migrate.upgrade(url, revision, legacy_sqlite, allow_empty or None)
    except migrate.MigrationError as exc:
        raise CliError(str(exc)) from exc
    except Exception as exc:  # alembic wraps migration errors in various exception types
        raise CliError(f"migration failed: {exc}") from exc
    click.echo(message)
    if legacy_sqlite:
        with _engine(ctx).connect() as conn:
            summary = conn.scalar(
                select(operation.c.summary).where(operation.c.kind == "legacy_load")
            )
        if summary:
            click.echo("legacy load verification:")
            for key, value in summary.items():
                click.echo(f"  {key}: {value}")


@db.command("downgrade")
@click.argument("revision")
@click.pass_context
def db_downgrade(ctx, revision: str):
    """Revert migrations down to REVISION."""
    try:
        click.echo(migrate.downgrade(_settings(ctx).database_url, revision))
    except Exception as exc:
        raise CliError(f"downgrade failed: {exc}") from exc


@db.command("current")
@click.pass_context
def db_current(ctx):
    """Show the current revision and whether the legacy catalog is loaded."""
    state = migrate.get_state(_settings(ctx).database_url)
    click.echo(f"current: {state.current or '(none)'}")
    click.echo(f"head:    {state.head}")
    click.echo(f"legacy catalog loaded: {'yes' if state.legacy_loaded else 'no'}")


@db.command("history")
@click.pass_context
def db_history(ctx):
    """List migrations."""
    for line in migrate.history(_settings(ctx).database_url):
        click.echo(line)


# --------------------------------------------------------------------------- sellers
@cli.group("seller")
def seller_group():
    """Sellers."""


@seller_group.command("list")
@click.option("--show-inactive", is_flag=True)
@click.pass_context
def seller_list(ctx, show_inactive: bool):
    """List sellers with their number of active offers."""
    offers = (
        select(func.count())
        .where(seller_product.c.seller_id == seller.c.id, seller_product.c.active)
        .correlate(seller)
        .scalar_subquery()
    )
    query = select(seller.c.id, seller.c.name, seller.c.active, offers.label("offers"))
    if not show_inactive:
        query = query.where(seller.c.active)
    with _engine(ctx).connect() as conn:
        _warn_house_name(conn, _settings(ctx))
        rows = conn.execute(query.order_by(seller.c.id)).all()
    click.echo(table(rows, ["id", "name", "active", "offers"]))


@seller_group.command("rename")
@click.argument("old_name")
@click.argument("new_name")
@no_wait_option
@click.pass_context
def seller_rename(ctx, old_name: str, new_name: str, no_wait: bool):
    """Rename a seller (files using the old name will create a new seller)."""
    from catalog.validation import check_text_fields
    from catalog.validation.text import display_clean

    cleaned = display_clean(new_name)
    problem = check_text_fields({"name": cleaned})
    if problem or not cleaned:
        raise CliError(f"invalid seller name: {problem or 'empty'}")
    with write_transaction(ctx, not no_wait) as (conn, _trans):
        seller_id = _seller_id(conn, old_name)
        clash = conn.scalar(
            select(seller.c.id).where(
                func.catalog.name_key(seller.c.name) == func.catalog.name_key(cleaned),
                seller.c.id != seller_id,
            )
        )
        if clash is not None:
            raise CliError(f"another seller (id {clash}) already uses that name")
        conn.execute(update(seller).where(seller.c.id == seller_id).values(name=cleaned))
    click.echo(f"seller {seller_id} renamed to {cleaned!r}")
    click.echo("note: importing files that still use the old name will create a new seller")


# --------------------------------------------------------------------------- import
@cli.group("import")
def import_group():
    """Import seller catalog files, inspect and undo imports."""


@import_group.command("run")
@click.argument("file", type=click.Path(exists=True, dir_okay=False))
@click.option("--dry-run", is_flag=True, help="Process everything, then roll back.")
@click.option("--force", is_flag=True, help="Import even if this file (MD5) is already effective.")
@click.option("--no-create-sellers", is_flag=True, help="Reject records of unknown sellers.")
@click.option(
    "--id-validation",
    type=click.Choice(ID_VALIDATION_MODES),
    default=None,
    help="UUID validation mode (default: ID_VALIDATION_MODE).",
)
@click.option("--id-versions", default=None, help="Allowed UUID versions, e.g. 4,7.")
@no_wait_option
@click.pass_context
def import_run(ctx, file, dry_run, force, no_create_sellers, id_validation, id_versions, no_wait):
    """Import a JSON file of seller records (sellers come from each record)."""
    settings = _settings(ctx)
    policy = settings.id_policy
    try:
        if id_validation:
            policy = replace(policy, mode=id_validation)
        if id_versions:
            policy = replace(policy, versions=parse_versions(id_versions))
    except SettingsError as exc:
        raise click.BadParameter(str(exc)) from exc
    options = ImportOptions(
        policy=policy,
        dry_run=dry_run,
        force=force,
        create_sellers=not no_create_sellers,
        wait=not no_wait,
    )
    engine = _engine(ctx)
    with engine.connect() as conn:
        _warn_house_name(conn, settings)
    try:
        result = run_import(engine, file, options)
    except LockBusyError as exc:
        raise LockBusyCliError(str(exc)) from exc
    except (DuplicateFileError, ImportFileError) as exc:
        raise CliError(str(exc)) from exc
    label = "dry run" if result.dry_run else f"import operation {result.operation_id}"
    click.echo(f"{label}: {file} (md5 {result.md5}, {result.file_size} bytes)")
    click.echo(f"id validation: {policy.describe()}")
    click.echo(table(sorted(result.counters.items()), ["counter", "value"]))
    if result.per_seller:
        keys = [
            "products_created",
            "duplicate_products",
            "offers_created",
            "offers_updated",
            "unchanged",
            "invalid_records",
            "skipped_records",
        ]
        rows = [
            [name] + [c.get(k, 0) for k in keys] for name, c in sorted(result.per_seller.items())
        ]
        click.echo()
        click.echo(table(rows, ["seller"] + keys))


@import_group.command("hash")
@click.argument("file", type=click.Path(exists=True, dir_okay=False))
def import_hash(file):
    """Print the MD5 of a local file (what `import undo --md5` expects)."""
    md5, _ = file_digest(file)
    click.echo(md5)


def _sellers_label(names: list[str]) -> str:
    if len(names) <= 3:
        return ",".join(names)
    return f"{','.join(names[:2])} +{len(names) - 2} more"


def _imports_query():
    return select(
        operation.c.id,
        operation.c.status,
        operation.c.started_at,
        operation.c.summary,
        import_batch,
    ).join(import_batch, import_batch.c.operation_id == operation.c.id)


@import_group.command("list")
@click.option("--seller", "seller_name", help="Only imports containing this seller.")
@click.option("--since", help="e.g. 2d, 36h or an ISO timestamp.")
@click.option("--until")
@click.option("--md5")
@click.option("--format", "fmt", type=click.Choice(["table", "md5"]), default="table")
@click.option("--limit", default=100, show_default=True)
@click.pass_context
def import_list(ctx, seller_name, since, until, md5, fmt, limit):
    """List imports (newest first)."""
    query = _imports_query()
    start, end = _moment(since, "--since"), _moment(until, "--until")
    if start:
        query = query.where(operation.c.started_at >= start)
    if end:
        query = query.where(operation.c.started_at < end)
    if md5:
        query = query.where(import_batch.c.md5 == md5.strip().lower())
    with _engine(ctx).connect() as conn:
        if seller_name:
            canonical = conn.scalar(
                select(seller.c.name).where(
                    func.catalog.name_key(seller.c.name) == func.catalog.name_key(seller_name)
                )
            )
            if canonical is None:
                raise CliError(f"unknown seller {seller_name!r}")
            query = query.where(operation.c.summary["per_seller"].has_key(canonical))
        rows = conn.execute(query.order_by(operation.c.id.desc()).limit(limit)).all()
    if fmt == "md5":
        for digest in dict.fromkeys(r.md5 for r in rows):
            click.echo(digest)
        return
    click.echo(
        table(
            [
                (
                    r.id,
                    r.started_at.strftime("%Y-%m-%d %H:%M:%S"),
                    r.status,
                    r.md5,
                    r.file_name,
                    r.total_records,
                    r.products_created,
                    r.offers_created,
                    r.invalid_records,
                    _sellers_label(sorted((r.summary or {}).get("per_seller", {}))),
                )
                for r in rows
            ],
            [
                "id",
                "started",
                "status",
                "md5",
                "file",
                "records",
                "new products",
                "new offers",
                "invalid",
                "sellers",
            ],
        )
    )


@import_group.command("show")
@click.argument("operation_id", required=False, type=int)
@click.option("--md5", help="Show every import of this file.")
@click.pass_context
def import_show(ctx, operation_id, md5):
    """Show one import (by operation id) or every import of a file (--md5)."""
    if (operation_id is None) == (md5 is None):
        raise click.UsageError("give an operation id or --md5")
    query = _imports_query()
    if operation_id is not None:
        query = query.where(operation.c.id == operation_id)
    else:
        query = query.where(import_batch.c.md5 == md5.strip().lower())
    with _engine(ctx).connect() as conn:
        rows = conn.execute(query.order_by(operation.c.id)).all()
    if not rows:
        raise CliError("import not found")
    for r in rows:
        click.echo(f"import {r.id}: {r.status}, {r.file_name} ({r.file_path})")
        click.echo(
            f"  md5 {r.md5}, {r.file_size} bytes, started {r.started_at}, "
            f"id validation {r.id_validation}"
        )
        counters = {
            k: getattr(r, k)
            for k in (
                "total_records",
                "invalid_records",
                "skipped_records",
                "sellers_created",
                "products_created",
                "products_updated",
                "duplicate_products",
                "offers_created",
                "offers_updated",
                "unchanged",
            )
        }
        click.echo("  " + ", ".join(f"{k}={v}" for k, v in counters.items()))
        for name, values in sorted((r.summary or {}).get("per_seller", {}).items()):
            click.echo(f"  {name}: " + ", ".join(f"{k}={v}" for k, v in sorted(values.items())))
        click.echo(f"  undo with: catalog import undo --md5 {r.md5}")


@import_group.command("undo")
@click.option("--md5", "md5s", multiple=True, help="Full MD5 of an imported file (repeatable).")
@click.option("--all-files", is_flag=True, help="Every import in the --since window.")
@click.option("--seller", "seller_name", help="Only this seller's changes.")
@click.option("--since", help="e.g. 2d, 36h or an ISO timestamp.")
@click.option("--until")
@click.option("--dry-run", is_flag=True)
@click.option("--skip-conflicts", is_flag=True, help="Undo what is possible, report the rest.")
@click.option("--yes", is_flag=True, help="Do not ask for confirmation.")
@no_wait_option
@click.pass_context
def import_undo(
    ctx, md5s, all_files, seller_name, since, until, dry_run, skip_conflicts, yes, no_wait
):
    """Undo imports, selected by file MD5 (never by file name) or by time window."""
    if bool(md5s) == all_files:
        raise click.UsageError("use exactly one of --md5 or --all-files")
    if all_files and not since:
        raise click.UsageError("--all-files requires --since")
    start, end = _moment(since, "--since"), _moment(until, "--until")
    for value in md5s:
        try:
            undo_service.validate_md5(value)
        except undo_service.UndoError as exc:
            raise click.BadParameter(str(exc), param_hint="--md5") from exc
    with write_transaction(ctx, not no_wait) as (conn, trans):
        seller_id = _seller_id(conn, seller_name) if seller_name else None
        result = undo_service.undo_imports(
            conn, list(md5s), all_files, seller_id, start, end, skip_conflicts
        )
        targets = conn.execute(
            select(
                import_batch.c.operation_id,
                import_batch.c.md5,
                import_batch.c.file_name,
                operation.c.started_at,
            )
            .join(operation, operation.c.id == import_batch.c.operation_id)
            .where(import_batch.c.operation_id.in_(result.target_operations))
        ).all()
        click.echo("files:")
        for t in targets:
            click.echo(f"  {t.md5}  import {t.operation_id}  {t.file_name}  {t.started_at}")
        _print_undo(result)
        _apply_or_rollback(trans, dry_run, yes, "undo")


# --------------------------------------------------------------------------- undo
@cli.group("undo")
def undo_group():
    """Inspect undo operations and revert them (undo of an undo)."""


@undo_group.command("list")
@click.option("--since")
@click.pass_context
def undo_list(ctx, since):
    """List undo operations (import undos, unmerges and reverts)."""
    query = select(undo_detail, operation.c.status, operation.c.summary).join(
        operation, operation.c.id == undo_detail.c.operation_id
    )
    start = _moment(since, "--since")
    if start:
        query = query.where(operation.c.started_at >= start)
    with _engine(ctx).connect() as conn:
        rows = conn.execute(query.order_by(undo_detail.c.operation_id.desc())).all()
    click.echo(
        table(
            [
                (
                    r.operation_id,
                    r.created.strftime("%Y-%m-%d %H:%M:%S"),
                    r.undo_kind,
                    r.status,
                    r.changes_reverted,
                    r.conflicts_skipped,
                    ",".join(map(str, (r.summary or {}).get("targets", []))),
                    json.dumps(r.criteria, sort_keys=True),
                )
                for r in rows
            ],
            ["id", "created", "kind", "status", "reverted", "conflicts", "targets", "criteria"],
        )
    )


@undo_group.command("show")
@click.argument("operation_id", type=int)
@click.pass_context
def undo_show(ctx, operation_id):
    """Show what an undo reverted."""
    with _engine(ctx).connect() as conn:
        row = conn.execute(
            select(undo_detail, operation.c.status, operation.c.summary)
            .join(operation, operation.c.id == undo_detail.c.operation_id)
            .where(undo_detail.c.operation_id == operation_id)
        ).first()
    if row is None:
        raise CliError(f"undo {operation_id} not found")
    summary = row.summary or {}
    click.echo(f"undo {operation_id}: {row.undo_kind}, status {row.status}")
    click.echo(f"  criteria: {json.dumps(row.criteria, sort_keys=True)}")
    click.echo(f"  targets: {summary.get('targets', [])}")
    click.echo(
        f"  changes reverted: {row.changes_reverted}, conflicts skipped: {row.conflicts_skipped}"
    )
    for line in summary.get("kept", []):
        click.echo(f"  kept active: {line}")
    for line in summary.get("conflicts", []):
        click.echo(f"  conflict: {line}")
    click.echo(f"  revert with: catalog undo revert {operation_id}")


@undo_group.command("revert")
@click.argument("operation_id", type=int)
@click.option("--dry-run", is_flag=True)
@click.option("--yes", is_flag=True)
@no_wait_option
@click.pass_context
def undo_revert(ctx, operation_id, dry_run, yes, no_wait):
    """Revert an undo (all-or-nothing). Reverts can be reverted too."""
    with write_transaction(ctx, not no_wait) as (conn, trans):
        result = undo_service.revert_undo(conn, operation_id)
        _print_undo(result)
        _apply_or_rollback(trans, dry_run, yes, "revert")


# --------------------------------------------------------------------------- duplicates
@cli.group("duplicates")
def duplicates_group():
    """Find near-duplicate products, brands or categories."""


@duplicates_group.command("list")
@click.option("--entity", type=click.Choice(dup_service.ENTITIES), required=True)
@click.option("--threshold", default=0.5, show_default=True, type=float)
@click.option("--limit", default=50, show_default=True)
@click.option("--format", "fmt", type=click.Choice(["table", "json"]), default="table")
@click.pass_context
def duplicates_list(ctx, entity, threshold, limit, fmt):
    """Suggest merge candidates (pg_trgm similarity)."""
    try:
        with _engine(ctx).connect() as conn:
            found = dup_service.find_candidates(conn, entity, threshold, limit)
    except ValueError as exc:
        raise click.BadParameter(str(exc)) from exc
    if fmt == "json":
        click.echo(
            json.dumps(
                [
                    {**c.__dict__, "merge_command": dup_service.merge_command(entity, c)}
                    for c in found
                ],
                indent=2,
            )
        )
        return
    rows = [
        (
            c.score,
            c.keep_id,
            c.keep_name,
            c.keep_brand,
            c.keep_offers,
            c.duplicate_id,
            c.duplicate_name,
            c.duplicate_brand,
            c.duplicate_offers,
            dup_service.merge_command(entity, c),
        )
        for c in found
    ]
    click.echo(
        table(
            rows,
            [
                "score",
                "id",
                "name",
                "brand",
                "offers",
                "id",
                "similar name",
                "brand",
                "offers",
                "merge with",
            ],
        )
    )


# --------------------------------------------------------------------------- merge
@cli.group("merge")
def merge_group():
    """Merge duplicates and undo merges."""


def _run_merge(ctx, dry_run: bool, yes: bool, no_wait: bool, action: Callable):
    with write_transaction(ctx, not no_wait) as (conn, trans):
        result = action(conn)
        click.echo(
            f"merge operation {result.operation_id}: {result.entity_type} "
            f"{', '.join(map(str, result.merged_ids))} -> {result.kept_id}"
        )
        for note in result.notes:
            click.echo(f"  note: {note}")
        _apply_or_rollback(trans, dry_run, yes, "merge")


merge_options = [
    click.option("--keep", type=int, required=True, help="Id of the row to keep."),
    click.option("--dry-run", is_flag=True),
    click.option("--yes", is_flag=True),
    no_wait_option,
]


def _with_options(options):
    def decorate(func):
        for option in reversed(options):
            func = option(func)
        return func

    return decorate


@merge_group.command("product")
@_with_options(merge_options)
@click.option("--duplicate", type=int, multiple=True, required=True, help="Repeatable.")
@click.pass_context
def merge_product(ctx, keep, dry_run, yes, no_wait, duplicate):
    """Merge duplicate products into --keep (offers and categories move over)."""
    _run_merge(
        ctx,
        dry_run,
        yes,
        no_wait,
        lambda conn: merge_service.merge_products(conn, keep, list(duplicate)),
    )


@merge_group.command("brand")
@_with_options(merge_options)
@click.option("--duplicate", type=int, required=True)
@click.option("--merge-products", is_flag=True, help="Also merge products that would collide.")
@click.pass_context
def merge_brand(ctx, keep, dry_run, yes, no_wait, duplicate, merge_products):
    """Merge a duplicate brand into --keep."""
    _run_merge(
        ctx,
        dry_run,
        yes,
        no_wait,
        lambda conn: merge_service.merge_brands(conn, keep, duplicate, merge_products),
    )


@merge_group.command("category")
@_with_options(merge_options)
@click.option("--duplicate", type=int, required=True)
@click.pass_context
def merge_category(ctx, keep, dry_run, yes, no_wait, duplicate):
    """Merge a duplicate category into --keep."""
    _run_merge(
        ctx,
        dry_run,
        yes,
        no_wait,
        lambda conn: merge_service.merge_categories(conn, keep, duplicate),
    )


@merge_group.command("list")
@click.pass_context
def merge_list(ctx):
    """Merge history, including the spellings merged by the legacy load."""
    query = select(merge_detail, operation.c.status).join(
        operation, operation.c.id == merge_detail.c.operation_id
    )
    with _engine(ctx).connect() as conn:
        rows = conn.execute(query.order_by(merge_detail.c.id)).all()
    click.echo(
        table(
            [
                (
                    r.operation_id,
                    r.created.strftime("%Y-%m-%d %H:%M:%S"),
                    r.origin,
                    r.status,
                    r.entity_type,
                    r.kept_id,
                    r.merged_id if r.merged_id is not None else r.merged_name,
                )
                for r in rows
            ],
            ["operation", "created", "origin", "status", "entity", "kept", "merged"],
        )
    )


@merge_group.command("undo")
@click.argument("operation_id", type=int)
@click.option("--dry-run", is_flag=True)
@click.option("--yes", is_flag=True)
@no_wait_option
@click.pass_context
def merge_undo(ctx, operation_id, dry_run, yes, no_wait):
    """Undo a manual merge (all-or-nothing)."""
    with write_transaction(ctx, not no_wait) as (conn, trans):
        result = undo_service.undo_merge(conn, operation_id)
        _print_undo(result)
        _apply_or_rollback(trans, dry_run, yes, "unmerge")


# --------------------------------------------------------------------------- serve
@cli.command("serve")
@click.option("--host", default="127.0.0.1", show_default=True)
@click.option("--port", default=8000, show_default=True)
@click.pass_context
def serve(ctx, host, port):  # pragma: no cover - runs a server
    """Run the read-only REST API."""
    import uvicorn

    from catalog.api.app import create_app

    uvicorn.run(create_app(_settings(ctx)), host=host, port=port, log_level="info")


if __name__ == "__main__":  # pragma: no cover
    main()
