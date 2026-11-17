import os
import traceback
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Callable, Optional

import typer
from dotenv import load_dotenv
from rich.table import Table
from rich.text import Text
from typing_extensions import Annotated

from isqa.common import __save_json__
from isqa.configs import console, logger, settings
from isqa.exceptions import IsQAWrongCLIParams
from isqa.helpers import (
    __print_and_fail__,
    check_if_label_exists,
    get_assignee_data,
    get_board,
    get_board_label_names,
    describe_issue,
    get_gitlab_scope,
    issue_reference,
    add_issue_comment,
    has_assignee,
    issue_has_comment_with,
    is_overdue,
    label_exists,
    sort_board_lists,
    parse_priority_labels,
    parse_sort_by,
    print_issue_to_console,
    print_issues_in_label,
    send_notification_from_cli,
    update_issue_labels,
)
from isqa.notifier import (
    send_email_due_date_expired,
    send_email_missing_assignee,
    send_email_missing_label,
)
from isqa.version import package_summary, package_version

app = typer.Typer(
    help="`isqa` utility for quality assurance of GitLab issues. "
    "Helps managing issues of a project or of a whole group."
)


class MigrationConditionChoice(str, Enum):
    due_date = "due-date"


def _today_utc():
    return datetime.now(timezone.utc).date()


def _due_date_expired(issue, today) -> bool:
    if not issue.due_date:
        return False
    return datetime.strptime(issue.due_date, "%Y-%m-%d").date() < today


@app.callback(invoke_without_command=True)
def main(
    ctx: typer.Context,
    dry_run: Annotated[
        bool,
        typer.Option(
            "--dry-run",
            "-d",
            help="Simulate execution without making changes.",
        ),
    ] = False,
    verbose: Annotated[
        bool,
        typer.Option(
            "--verbose",
            "-v",
            help="Enable verbose outputs.",
        ),
    ] = False,
    env_file: Annotated[
        Path,
        typer.Option(
            "--env-file",
            "-e",
            help="Specify a path to a .env file to load environment variables.",
            exists=True,
            file_okay=True,
            dir_okay=False,
            writable=False,
            readable=True,
            resolve_path=True,
        ),
    ] = None,
    group: Annotated[
        Optional[str],
        typer.Option(
            "--group",
            "-g",
            help="ID or full path of a GitLab group. Overrides 'GITLAB_GROUP_ID' "
            "and makes all commands work on the issues/boards of the whole group.",
        ),
    ] = None,
    project: Annotated[
        Optional[str],
        typer.Option(
            "--project",
            "-p",
            help="ID or full path of a GitLab project. Overrides 'GITLAB_PROJECT_ID'.",
        ),
    ] = None,
    version: Annotated[
        bool,
        typer.Option(
            "--version",
            help="Show version.",
        ),
    ] = False,
) -> None:
    """
    This function runs *before* any other command (subcommand)
    or when the app is called without a subcommand.
    """
    settings.dry_run = dry_run
    settings.verbose = verbose

    if settings.verbose:
        console.print(
            Text("✅", style="bold green"),
            "Verbose mode:",
            Text("enabled", style="bold green"),
        )
        console.print(
            Text("✅", style="bold green"),
            "Dry run mode:",
            Text(
                f"{settings.dry_run}",
                style=f"bold {'red' if not settings.dry_run else 'green'}",
            ),
        )
    elif settings.dry_run:
        console.print(
            Text("✅", style="bold green"),
            "Dry run mode:",
            Text("enabled", style="bold green"),
        )

    from isqa.constants import DEFAULT_ENV_EXTRA_CONFIG

    load_dotenv(DEFAULT_ENV_EXTRA_CONFIG, override=True)

    if env_file:
        console.print(
            "Loading environment variables from:",
            Text(f"{env_file}", style="bold blue"),
        )
        success = load_dotenv(env_file, override=True)
        if not success:
            typer.echo(f"Warning: Could not load variables from {env_file}", err=True)
    else:
        load_dotenv()

    # CLI parameters win over the environment
    if group:
        os.environ["GITLAB_GROUP_ID"] = group
    if project:
        os.environ["GITLAB_PROJECT_ID"] = project
        if not group:
            os.environ.pop("GITLAB_GROUP_ID", None)

    if version:
        if settings.verbose:
            table = Table()
            table.add_column("Field", justify="right", style="cyan", no_wrap=True)
            table.add_column("Value", justify="left", style="yellow", no_wrap=True)
            summary = package_summary()
            for item in summary:
                table.add_row(item["field"], item["value"])
            console.print(table)
        else:
            console.print(f"{package_version()}", style="yellow")
        raise typer.Exit(0)

    # If a subcommand was provided, don't exit; continue to the subcommand.
    # Otherwise, Typer will handle exiting or showing the help page.
    if ctx.invoked_subcommand is not None:
        return


def _save_json_if_verbose(data):
    if settings.verbose:
        console.print(
            "JSON have seen saved into: ",
            Text(__save_json__(data), style="bold cyan"),
        )


@app.command()
def list_issues(
    state: str = typer.Option(
        "opened",
        "--state",
        "-s",
        help="Filter issues by state: opened, closed, or all.",
    ),
):
    """
    Lists all issues of the project or group.
    """

    scope, scope_name = get_gitlab_scope()

    console.print(
        "Get",
        Text(state, style="bold yellow"),
        "issues for:",
        Text(scope_name, style="bold green"),
    )

    try:
        issues = scope.issues.list(state=state, get_all=True, iterator=True)
        issue_data = []
        for issue in issues:
            issue_data.append(issue.asdict())
            print_issue_to_console(issue)

        if not issue_data:
            console.print(
                Text("No issues found for the specified criteria.", style="bold blue")
            )
            return

        console.print("\nTotal issues:", Text(f"{len(issue_data)}", style="bold cyan"))
        _save_json_if_verbose(issue_data)

    except Exception as e:
        __print_and_fail__(f"An unexpected error occurred: {e}")


@app.command()
def list_board_issues(
    board_id: Optional[int] = typer.Option(
        None,
        "--board-id",
        "-b",
        help="Specific Board ID to inspect. Defaults to the first board found.",
    ),
    board_name: Optional[str] = typer.Option(
        None,
        "--board-name",
        "-n",
        help="Specific Board name to inspect. Defaults to the first board found.",
    ),
    label: Optional[str] = typer.Option(
        None,
        "--label",
        "-l",
        help="Specific label name to show. Defaults to all labels found.",
    ),
    without_labels: Annotated[
        bool,
        typer.Option(
            "--without-labels",
            "-w",
            help="Lists also issues without labels.",
        ),
    ] = False,
):
    """
    Lists all issues with a label used as columns on a GitLab board
    (project board or group board). Only the first board is processed.
    A specific board ID or name could be provided.
    """
    scope, _ = get_gitlab_scope()

    if label is not None:
        check_if_label_exists(scope, label)

    try:
        board = get_board(scope, board_id, board_name)
        if board is None:
            return

        label_names = get_board_label_names(board)
        if not label_names:
            console.print(
                Text(f"No label lists found for board {board.name}.", style="bold yellow")
            )
            return

        board_data = {"board_id": board.id, "board_name": board.name, "lists": {}}

        for list_label_name in label_names:
            if label is not None and list_label_name.lower() != label.lower():
                continue

            label_issues = list(
                scope.issues.list(
                    labels=[list_label_name],
                    state="opened",
                    order_by="relative_position",
                    sort="asc",
                    get_all=True,
                    iterator=True,
                )
            )
            print_issues_in_label(label_issues, list_label_name)
            board_data["lists"][list_label_name] = [i.asdict() for i in label_issues]

        # print issues without labels
        if without_labels:
            key = "issues-without-labels"
            board_labels = set(label_names)
            issues_without_labels = [
                issue
                for issue in scope.issues.list(state="opened", get_all=True, iterator=True)
                if not board_labels.intersection(issue.labels)
            ]
            print_issues_in_label(issues_without_labels, label_name=key)
            board_data["lists"][key] = [i.asdict() for i in issues_without_labels]

        _save_json_if_verbose(board_data)

    except Exception as e:
        __print_and_fail__(f"An unexpected error occurred: {e}")


@app.command()
def migrate_issues(
    state: str = typer.Option(
        "opened",
        "--state",
        "-s",
        help="Filter issues by state: opened, closed, or all.",
    ),
    from_label: Optional[str] = typer.Option(
        None, "--from-label", "-f", help="Specific label name to migrate `from`."
    ),
    to_label: Optional[str] = typer.Option(
        None, "--to-label", "-t", help="Specific label name to migrate `to`."
    ),
    check_condition: Optional[MigrationConditionChoice] = typer.Option(
        None,
        "--check-condition",
        help="Specific condition to check before migrating labels.",
    ),
):
    """
    Migrates issues from one label to another label. Each issue must meet
    specific conditions (e.g., 'due-date' = due date is before today).
    """

    if not from_label or not to_label:
        raise IsQAWrongCLIParams("--from-label and --to-label must be provided")
    if not check_condition:
        raise IsQAWrongCLIParams("--check-condition must be provided")

    scope, scope_name = get_gitlab_scope()

    check_if_label_exists(scope, from_label)
    check_if_label_exists(scope, to_label)

    console.print(
        "Get",
        Text(state, style="bold yellow"),
        "issues with label",
        Text(from_label, style="bold blue"),
        "for:",
        Text(scope_name, style="bold green"),
    )

    try:
        issues = scope.issues.list(
            state=state, labels=[from_label], get_all=True, iterator=True
        )
        issue_data = []
        today = _today_utc()

        for issue in issues:
            if from_label not in (issue.labels or []):
                continue

            condition_satisfied = False
            if check_condition == MigrationConditionChoice.due_date:
                condition_satisfied = _due_date_expired(issue, today)

            if not condition_satisfied:
                continue

            print_issue_to_console(issue)
            issue_data.append(issue.asdict())

            if settings.dry_run:
                console.print(
                    Text("  [dry-run] would migrate", style="bold yellow"),
                    Text(f"'{from_label}'", style="bold blue"),
                    "->",
                    Text(f"'{to_label}'", style="bold green"),
                )
                continue

            update_issue_labels(issue, add_labels=[to_label], remove_labels=[from_label])
            console.print(
                Text("✅", style="bold green"),
                Text("Issue labels updated successfully from ->", style="bold white"),
                Text(f"'{from_label}'", style="bold blue"),
                "to",
                Text(f"'{to_label}'", style="bold green"),
            )

        if not issue_data:
            console.print(
                Text("No issues found for the specified criteria.", style="bold blue")
            )
            return

        console.print("\nTotal issues:", Text(f"{len(issue_data)}", style="bold cyan"))
        _save_json_if_verbose(issue_data)

    except Exception as e:
        __print_and_fail__(f"An unexpected error occurred: {e}")


@app.command()
def add_default_label(
    to_label: str = typer.Option(
        ...,
        "--to-label",
        "-t",
        help="Label that is added to issues without any column label (e.g. 'ToDo').",
    ),
    column_label: Optional[list[str]] = typer.Option(
        None,
        "--column-label",
        "-c",
        help="Label that counts as a board column. Can be given multiple times. "
        "If omitted, the label lists of the board are used.",
    ),
    board_id: Optional[int] = typer.Option(
        None,
        "--board-id",
        "-b",
        help="Board whose label lists are the column labels (if no --column-label "
        "is given). Defaults to the first board found.",
    ),
    board_name: Optional[str] = typer.Option(
        None,
        "--board-name",
        "-n",
        help="Board name, alternative to --board-id.",
    ),
    state: str = typer.Option(
        "opened",
        "--state",
        "-s",
        help="Filter issues by state: opened, closed, or all.",
    ),
):
    """
    Adds a default label (e.g. 'ToDo') to all issues that have none of the
    column labels, so that every issue shows up in a column of the board.
    The column labels are given explicitly (--column-label) or taken from the
    label lists of a board. The default label itself always counts as a column label.
    """
    scope, scope_name = get_gitlab_scope()

    if column_label:
        columns = list(dict.fromkeys(column_label))  # unique, keep order
        source = "given column labels"
    else:
        board = get_board(scope, board_id, board_name)
        if board is None:
            __print_and_fail__("No board found to read the column labels from.")
        columns = get_board_label_names(board)
        source = f"label lists of board '{board.name}'"
        if not columns:
            __print_and_fail__(f"Board '{board.name}' has no label lists.")

    if to_label not in columns:
        columns.append(to_label)

    for name in columns:
        check_if_label_exists(scope, name)

    console.print(
        "Column labels",
        f"({source}):",
        Text(", ".join(columns), style="bold yellow"),
    )
    console.print(
        "Get",
        Text(state, style="bold yellow"),
        "issues without column label for:",
        Text(scope_name, style="bold green"),
    )

    try:
        column_set = set(columns)
        issue_data = []

        for issue in scope.issues.list(state=state, get_all=True, iterator=True):
            if column_set.intersection(issue.labels or []):
                continue

            print_issue_to_console(issue)
            issue_data.append(issue.asdict())

            if settings.dry_run:
                console.print(
                    Text("  [dry-run] would add", style="bold yellow"),
                    Text(f"'{to_label}'", style="bold green"),
                )
                continue

            update_issue_labels(issue, add_labels=[to_label])
            console.print(
                Text("✅", style="bold green"),
                "Label added:",
                Text(f"'{to_label}'", style="bold green"),
            )

        if not issue_data:
            console.print(
                Text("All issues have a column label, nothing to do.", style="bold blue")
            )
            return

        console.print("\nTotal issues:", Text(f"{len(issue_data)}", style="bold cyan"))
        _save_json_if_verbose(issue_data)

    except typer.Exit:
        raise
    except Exception as e:
        __print_and_fail__(f"An unexpected error occurred: {e}")


def _parse_rule(rule: str, default_keys: tuple) -> tuple:
    """'ToDo;In Progress = due-date,priority' -> (['ToDo', 'In Progress'], keys).
    Columns '*' = all lists of the board."""
    if "=" in rule:
        cols, crit = rule.split("=", 1)
        keys = parse_sort_by(crit) if crit.strip() else default_keys
    else:
        cols, keys = rule, default_keys
    columns = [c.strip() for c in cols.split(";") if c.strip()]
    if not columns:
        __print_and_fail__(f"Rule without columns: '{rule}'")
    return columns, keys


@app.command()
def sort_board_issues(
    board_id: Optional[int] = typer.Option(
        None,
        "--board-id",
        "-b",
        help="Specific Board ID to inspect. Defaults to the first board found.",
    ),
    board_name: Optional[str] = typer.Option(
        None,
        "--board-name",
        "-n",
        help="Specific Board name to inspect. Defaults to the first board found.",
    ),
    label: Optional[list[str]] = typer.Option(
        None,
        "--label",
        "-l",
        help="Label (board list) to be sorted. Can be given multiple times. "
        "Defaults to all label lists of the board.",
    ),
    rule: Optional[list[str]] = typer.Option(
        None,
        "--rule",
        "-r",
        help="Sort rule '<columns> = <keys>', e.g. 'ToDo;Doing = due-date,priority'. "
        "Columns separated by ';', '*' = all lists. Can be given multiple times; "
        "if an issue is in several lists with contradicting orders, the rule "
        "(and column) given first wins. Alternative to --label/--sort-by.",
    ),
    desc: Annotated[
        bool,
        typer.Option(
            "--descending",
            "-desc",
            help="Sort issues in descending order by due date (only affects 'due-date').",
        ),
    ] = False,
    sort_by: str = typer.Option(
        "due-date",
        "--sort-by",
        "-s",
        help="Comma separated sort keys, most important first: 'due-date', "
        "'priority', 'overdue' (overdue issues first), 'due-within:N' (issues due "
        "within N days first). E.g. 'overdue,priority' = overdue issues first, "
        "both groups by priority. Default for rules without keys.",
    ),
    strict: Annotated[
        bool,
        typer.Option(
            "--strict",
            help="Fail if a given column label does not exist or is not a list on the "
            "board. Default: skip such labels with a warning.",
        ),
    ] = False,
    priority_labels: Optional[str] = typer.Option(
        None,
        "--priority-labels",
        "-P",
        envvar="ISQA_PRIORITY_LABELS",
        help="Comma separated priority labels, highest first "
        "(e.g. 'Prio_High,Prio_Medium,Prio_Low'). Issues without one go last. "
        "Can also be set via ISQA_PRIORITY_LABELS.",
    ),
):
    """
    Sorts the issues of the label lists of a GitLab board (project board or
    group board). Default: by due date, issues without due date at the end.
    With --sort-by also by priority labels, with --rule different keys per list.
    Issues that are equal in all sort keys keep their manual order.
    Only the first board is processed. A specific board ID or name could be provided.
    """
    default_keys = parse_sort_by(sort_by)
    if rule and label:
        __print_and_fail__("Use either --rule or --label, not both.")

    rules = [_parse_rule(r, default_keys) for r in rule] if rule else [
        (list(dict.fromkeys(label)) if label else ["*"], default_keys)
    ]

    uses_priority = any("priority" in keys for _, keys in rules)
    prio_labels = parse_priority_labels(priority_labels)
    if uses_priority and not prio_labels:
        __print_and_fail__(
            "Sorting by 'priority' needs --priority-labels (or ISQA_PRIORITY_LABELS)."
        )

    scope, _ = get_gitlab_scope()

    # priority labels must exist, otherwise the priority order would be wrong
    for name in prio_labels if uses_priority else []:
        check_if_label_exists(scope, name)

    if strict:
        for columns, _ in rules:
            for name in columns:
                if name != "*":
                    check_if_label_exists(scope, name)

    if uses_priority:
        console.print("Priorities:", Text(" > ".join(prio_labels), style="bold yellow"))

    try:
        board = get_board(scope, board_id, board_name)
        if board is None:
            return

        board_lists = get_board_label_names(board)
        if not board_lists:
            console.print(
                Text(f"No label lists found for board {board.name}.", style="bold yellow")
            )
            return

        # build the plan: (label, keys) in order of precedence
        plan, seen, requested = [], set(), []
        for columns, keys in rules:
            for name in board_lists if columns == ["*"] else columns:
                if name in seen:
                    if columns != ["*"]:
                        console.print(
                            Text("⚠️  WARNING:", style="bold yellow"),
                            f"Column '{name}' is used in several rules - the first one counts.",
                        )
                    continue
                seen.add(name)
                requested.append(name)
                if name not in board_lists:
                    if strict:
                        __print_and_fail__(
                            f"Label(s) ['{name}'] are not used as lists on board '{board.name}'."
                        )
                    reason = (
                        "is not a list on board"
                        if label_exists(scope, name)
                        else "does not exist (typo?) and is not a list on board"
                    )
                    msg = f"Label '{name}' {reason} '{board.name}' - skipped."
                    console.print(Text("⚠️  WARNING:", style="bold yellow"), msg)
                    logger.warning(msg)
                    continue
                plan.append((name, keys))

        if not plan:
            __print_and_fail__(
                f"None of the given labels {requested} is a list on board "
                f"'{board.name}'. Check the label names and the board."
            )

        console.print("\nSort plan (first = highest precedence):")
        for name, keys in plan:
            console.print("  -", Text(name, style="bold yellow"), "by", " > ".join(keys))

        result = sort_board_lists(scope, plan, desc, prio_labels)

        console.print()
        for name, res in result.items():
            if res["sorted"]:
                console.print(
                    Text("✅", style="bold green"),
                    Text(name, style="bold green"),
                    f"sorted ({res['moves']} move(s))"
                    if not settings.dry_run
                    else f"[dry-run] {res['moves']} move(s) planned",
                )
            else:
                msg = (
                    f"List '{name}' could not be fully sorted: issues are also in a "
                    "column with higher precedence that needs a different order:"
                )
                console.print(Text("⚠️  WARNING:", style="bold yellow"), msg)
                logger.warning(msg)
                for line in res["conflicts"]:
                    console.print("    -", line)
                    logger.warning(f"    - {line}")

    except typer.Exit:
        raise
    except Exception as e:
        __print_and_fail__(f"An unexpected error occurred: {e}")


class CommentConditionChoice(str, Enum):
    missing_assignee = "missing-assignee"
    missing_label = "missing-label"
    overdue = "overdue"


class _SafeDict(dict):
    def __missing__(self, key):
        return "{" + key + "}"


@app.command()
def comment_issues(
    condition: CommentConditionChoice = typer.Option(
        ...,
        "--condition",
        "-c",
        help="Which issues get the comment: 'missing-assignee', 'missing-label' "
        "or 'overdue' (due date before today).",
    ),
    message: str = typer.Option(
        ...,
        "--message",
        "-m",
        help="Comment text (Markdown), e.g. '@jdoe This issue has no assignee.' "
        "Placeholders: {title}, {due_date}, {reference}, {url}.",
    ),
    key: Optional[str] = typer.Option(
        None,
        "--key",
        "-k",
        help="Identifies the comment (invisible marker). Every issue gets a comment "
        "with the same key only once. Defaults to the condition.",
    ),
    label: Optional[list[str]] = typer.Option(
        None,
        "--label",
        "-l",
        help="Only issues that have this label. Can be given multiple times "
        "(issue needs one of them).",
    ),
    state: str = typer.Option(
        "opened",
        "--state",
        "-s",
        help="Filter issues by state: opened, closed, or all.",
    ),
):
    """
    Adds a comment ONCE to every issue that matches a condition, e.g. a mention
    of a responsible person on issues without assignee. Issues that already
    have a comment with the same key are skipped, also in later runs.
    """
    marker_key = key or condition.value
    marker = f"<!-- isqa:{marker_key} -->"

    scope, scope_name = get_gitlab_scope()

    for name in label or []:
        check_if_label_exists(scope, name)

    conditions = {
        CommentConditionChoice.missing_assignee: lambda i: not has_assignee(i),
        CommentConditionChoice.missing_label: lambda i: not i.labels,
        CommentConditionChoice.overdue: lambda i: is_overdue(i),
    }
    matches = conditions[condition]
    wanted_labels = set(label or [])

    console.print(
        "Comment", Text(f"'{marker_key}'", style="bold yellow"),
        "on", Text(state, style="bold yellow"), "issues with condition",
        Text(condition.value, style="bold yellow"), "in", Text(scope_name, style="bold green"),
    )

    try:
        commented, skipped = 0, 0
        for issue in scope.issues.list(state=state, get_all=True, iterator=True):
            if not matches(issue):
                continue
            if wanted_labels and not wanted_labels.intersection(issue.labels or []):
                continue

            if issue_has_comment_with(issue, marker):
                skipped += 1
                if settings.verbose:
                    console.print(
                        "  already commented:",
                        Text(describe_issue(issue), style="dim"),
                    )
                continue

            body = message.replace("\\n", "\n").format_map(_SafeDict(
                title=issue.title,
                due_date=issue.due_date or "",
                reference=issue_reference(issue),
                url=issue.web_url,
            ))
            body = f"{body}\n\n{marker}"

            if settings.dry_run:
                console.print(
                    Text("  [dry-run] would comment on", style="bold yellow"),
                    Text(describe_issue(issue), style="blue"),
                )
            else:
                add_issue_comment(issue, body)
                console.print(
                    Text("  ✅ commented on", style="bold green"),
                    Text(describe_issue(issue), style="blue"),
                )
            commented += 1

        verb = "would be commented" if settings.dry_run else "commented"
        console.print(
            f"\nIssues {verb}:", Text(str(commented), style="bold cyan"),
            "| already commented before:", Text(str(skipped), style="bold cyan"),
        )

    except typer.Exit:
        raise
    except Exception as e:
        __print_and_fail__(f"An unexpected error occurred: {e}")


# --------------------------------------------------------------------------- #
# Checks with optional email notification
# --------------------------------------------------------------------------- #


def _run_check(
    state: str,
    has_problem: Callable,
    notify_admin: bool,
    notify_assignee: bool,
    send_email_func: Callable,
    show_due_date: bool = False,
):
    """Common implementation of the `check-*` commands."""

    scope, scope_name = get_gitlab_scope()

    console.print(
        "Get",
        Text(state, style="bold yellow"),
        "issues for:",
        Text(scope_name, style="bold green"),
    )

    try:
        issues = scope.issues.list(state=state, get_all=True, iterator=True)
        issue_data = []
        issues_with_problems = {}
        admin_email = os.environ.get("EMAIL_ADMIN_TO_NOTIFY")

        def _add(email, name, issue):
            issues_with_problems.setdefault(email, []).append(
                {"link": issue.web_url, "title": issue.title, "assignee": name}
            )

        for issue in issues:
            issue_data.append(issue.asdict())
            try:
                if not has_problem(issue):
                    continue

                print_issue_to_console(
                    issue, due_date=issue.due_date if show_due_date else None
                )

                if notify_admin:
                    _add(admin_email, "Admin", issue)

                if notify_assignee:
                    assignee, assignee_email = get_assignee_data(issue)
                    if assignee is None or assignee_email is None:
                        assignee, assignee_email = "Admin", admin_email
                    _add(assignee_email, assignee, issue)

            except Exception:
                logger.error(traceback.format_exc())

        problems = sum(len(v) for v in issues_with_problems.values())

        if problems > 0:
            all_sent = send_notification_from_cli(
                notify_admin,
                notify_assignee,
                issues_with_problems,
                scope_name,
                send_email_func,
            )
            if not all_sent:
                __print_and_fail__("At least one notification email could not be sent.")
        else:
            console.print(
                Text("\nNo one will be notified this time.", style="bold yellow")
            )

        if not issue_data:
            console.print(
                Text("No issues found for the specified criteria.", style="bold yellow")
            )
            return

        console.print(
            "\nTotal issues:", Text(f"{len(issue_data)}", style="bold yellow")
        )
        _save_json_if_verbose(issue_data)

    except typer.Exit:
        raise
    except Exception as e:
        __print_and_fail__(f"An unexpected error occurred: {e}")


@app.command()
def check_due_date(
    state: str = typer.Option(
        "opened",
        "--state",
        "-s",
        help="Filter issues by state: opened, closed, or all.",
    ),
    notify_admin: bool = typer.Option(
        False,
        "--notify-admin",
        help="If set, notifies admin about issues with a past due date.",
    ),
    notify_assignee: bool = typer.Option(
        False,
        "--notify-assignee",
        help="If set, notifies assignee about issues with a past due date.",
    ),
):
    """
    Checks all issues if the 'due date' has passed. Optionally notifies the admin or assignee.
    """
    today = _today_utc()
    _run_check(
        state,
        lambda issue: _due_date_expired(issue, today),
        notify_admin,
        notify_assignee,
        send_email_due_date_expired,
        show_due_date=True,
    )


@app.command()
def check_missing_assignee(
    state: str = typer.Option(
        "opened",
        "--state",
        "-s",
        help="Filter issues by state: opened, closed, or all.",
    ),
    notify_admin: bool = typer.Option(
        False, "--notify-admin", help="If set, notifies admin about no assignee"
    ),
):
    """
    Checks all issues if the 'assignee' has not been set. Optionally notifies the admin.
    """
    _run_check(
        state,
        lambda issue: getattr(issue, "assignee", None) is None,
        notify_admin,
        False,
        send_email_missing_assignee,
    )


@app.command()
def check_missing_label(
    state: str = typer.Option(
        "opened",
        "--state",
        "-s",
        help="Filter issues by state: opened, closed, or all.",
    ),
    notify_admin: bool = typer.Option(
        False, "--notify-admin", help="If set, notifies admin about missing label."
    ),
    notify_assignee: bool = typer.Option(
        False,
        "--notify-assignee",
        help="If set, notifies assignee about missing label.",
    ),
):
    """
    Checks all issues if a label is missing. Optionally notifies the admin or assignee.
    """
    _run_check(
        state,
        lambda issue: not issue.labels,
        notify_admin,
        notify_assignee,
        send_email_missing_label,
    )
