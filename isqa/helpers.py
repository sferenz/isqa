import os
import time
import traceback
from datetime import datetime, timedelta, timezone
from typing import Optional, Union

import gitlab
import typer
from gitlab.v4.objects import Group, Project, ProjectIssue
from rich.table import Table
from rich.text import Text

from isqa.configs import console, logger, settings
from isqa.constants import ISSUES_REORDER_DELAY_SECONDS, ISSUES_REORDER_MAX_PASSES
from isqa.html import convert_to_html_list

# A "scope" is either a GitLab project or a GitLab group. Both offer the
# managers `issues`, `boards` and `labels`, so most commands work on both.
Scope = Union[Project, Group]

# authenticated GitLab session (set by `get_gitlab_scope`)
gitlab_auth_object: Optional[gitlab.Gitlab] = None


def __check_and_fail__(key, message: str):
    if key is None or key == "":
        logger.error(message)
        console.print(Text(f"❌ ERROR: {message}", style="bold red"))
        raise typer.Exit(code=1)


def __print_and_fail__(message: str):
    logger.error(message)
    logger.error(traceback.format_exc())
    console.print(Text(f"❌ ERROR: {message}", style="bold red"))
    raise typer.Exit(code=1)


# --------------------------------------------------------------------------- #
# Connection / scope handling
# --------------------------------------------------------------------------- #


def get_gitlab_credentials_envs():
    """Reads the GitLab connection settings from the environment.

    Returns:
        tuple: (gitlab_url, scope_type, scope_id, token) where scope_type is
        either "group" or "project".
    """

    GITLAB_URL = os.environ.get("GITLAB_URL")
    __check_and_fail__(GITLAB_URL, "No Gitlab URL. Set 'GITLAB_URL' in your .env file.")

    GROUP_ID = os.environ.get("GITLAB_GROUP_ID") or None
    REPO_ID = os.environ.get("GITLAB_PROJECT_ID") or None

    if GROUP_ID and REPO_ID:
        console.print(
            Text("⚠️", style="bold yellow"),
            "Both 'GITLAB_GROUP_ID' and 'GITLAB_PROJECT_ID' are set.",
            "Working on the group:",
            Text(GROUP_ID, style="bold yellow"),
        )

    if GROUP_ID:
        scope_type, scope_id = "group", GROUP_ID
    else:
        __check_and_fail__(
            REPO_ID,
            "No project or group was provided. Set 'GITLAB_PROJECT_ID' or "
            "'GITLAB_GROUP_ID' in your .env file.",
        )
        scope_type, scope_id = "project", REPO_ID

    # `GITLAB_PRIVATE_TOKEN` is accepted as an alias (used in older docs)
    GITLAB_TOKEN = os.environ.get("GITLAB_TOKEN") or os.environ.get(
        "GITLAB_PRIVATE_TOKEN"
    )
    __check_and_fail__(
        GITLAB_TOKEN, "No token was provided. Set 'GITLAB_TOKEN' in your .env file."
    )

    if settings.verbose:
        console.print("Gitlab: ", Text(GITLAB_URL, style="bold yellow"))
        console.print(f"{scope_type.capitalize()}:", Text(scope_id, style="bold yellow"))

    return GITLAB_URL, scope_type, scope_id, GITLAB_TOKEN


def get_gitlab_scope() -> tuple[Scope, str]:
    """Connects to GitLab and returns the configured project or group.

    Returns:
        tuple: (scope, scope_name) - scope is a python-gitlab Project or Group.
    """
    global gitlab_auth_object

    gitlab_url, scope_type, scope_id, token = get_gitlab_credentials_envs()

    try:
        console.print(
            "Connecting to GitLab instance at: ",
            Text(f"{gitlab_url}", style="yellow bold"),
        )
        gl = gitlab.Gitlab(gitlab_url, private_token=token)
        gl.auth()
        gitlab_auth_object = gl

        if scope_type == "group":
            scope = gl.groups.get(scope_id)
            scope_name = scope.full_path
        else:
            scope = gl.projects.get(scope_id)
            scope_name = scope.path_with_namespace

    except gitlab.exceptions.GitlabAuthenticationError:
        __print_and_fail__(
            "Authentication failed. Check your URL and Personal Access Token."
        )
    except gitlab.exceptions.GitlabGetError:
        __print_and_fail__(
            f"{scope_type.capitalize()} with ID/Path '{scope_id}' not found on {gitlab_url}."
        )
    except Exception as e:
        __print_and_fail__(f"An unexpected error occurred: {e}")

    console.print(
        "Working on",
        scope_type,
        Text(scope_name, style="bold green"),
    )
    return scope, scope_name


def get_gitlab_project(repo_id: str, gitlab_url: str, token: str):
    """Initializes GitLab connection and retrieves the project object.

    Kept for backwards compatibility; new code should use `get_gitlab_scope`.
    """
    global gitlab_auth_object

    try:
        gl = gitlab.Gitlab(gitlab_url, private_token=token)
        gl.auth()
        gitlab_auth_object = gl
        return gl.projects.get(repo_id)
    except gitlab.exceptions.GitlabAuthenticationError:
        __print_and_fail__(
            "Authentication failed. Check your URL and Personal Access Token."
        )
    except gitlab.exceptions.GitlabGetError:
        __print_and_fail__(
            f"Project with ID/Path '{repo_id} not found on {gitlab_url}."
        )
    except Exception as e:
        __print_and_fail__(f"An unexpected error occurred: {e}")

    return None


def is_group(scope: Scope) -> bool:
    return isinstance(scope, Group)


# --------------------------------------------------------------------------- #
# Issue helpers (work for project and group issues)
# --------------------------------------------------------------------------- #

_project_issue_managers: dict = {}


def _project_issues_manager(project_id: int):
    """Returns a (cached, lazy) issues manager of the issue's project.

    Group issues are read-only in the API; changes must go through the
    project the issue belongs to.
    """
    if project_id not in _project_issue_managers:
        project = gitlab_auth_object.projects.get(project_id, lazy=True)
        _project_issue_managers[project_id] = project.issues
    return _project_issue_managers[project_id]


def issue_reference(issue) -> str:
    """Human readable reference, e.g. '#12' or 'group/project#12'."""
    refs = getattr(issue, "references", None) or {}
    if not isinstance(issue, ProjectIssue) and refs.get("full"):
        return refs["full"]
    return f"#{issue.iid}"


def update_issue_labels(
    issue, add_labels: list = None, remove_labels: list = None, remove_due_date: bool = False
):
    """Adds/removes labels of a project or group issue (other labels are kept).
    Optionally removes the due date in the same update."""
    data = {}
    if remove_due_date:
        data["due_date"] = None
    if add_labels:
        data["add_labels"] = ",".join(add_labels)
    if remove_labels:
        data["remove_labels"] = ",".join(remove_labels)
    if not data:
        return
    _project_issues_manager(issue.project_id).update(issue.iid, data)


def _project_issue(issue):
    return _project_issues_manager(issue.project_id).get(issue.iid, lazy=True)


def issue_has_comment_with(issue, marker: str) -> bool:
    """True if one of the issue's comments (notes) contains `marker`."""
    for note in _project_issue(issue).notes.list(get_all=True, iterator=True):
        if marker in (getattr(note, "body", "") or ""):
            return True
    return False


def add_issue_comment(issue, body: str):
    """Adds a comment (note) to a project or group issue."""
    _project_issue(issue).notes.create({"body": body})


def assignee_usernames(issue) -> list:
    """Usernames of all assignees of an issue (lower case, in GitLab's order)."""
    people = list(getattr(issue, "assignees", None) or [])
    single = getattr(issue, "assignee", None)
    if not people and single:
        people = [single]
    return [p.get("username", "").lower() for p in people if p and p.get("username")]


def user_exists(username: str) -> bool:
    """True if a GitLab user with this username exists."""
    try:
        return bool(gitlab_auth_object.users.list(username=username))
    except Exception:
        return True  # cannot check (e.g. missing permission) -> do not warn


def has_assignee(issue) -> bool:
    return bool(getattr(issue, "assignees", None)) or getattr(issue, "assignee", None) is not None


def reorder_issue(issue, move_before_id: int = None, move_after_id: int = None):
    """Repositions an issue (board order).

    Args:
        move_before_id: global ID of the issue that should be placed BEFORE this one
        move_after_id: global ID of the issue that should be placed AFTER this one
    """
    project_issue = _project_issues_manager(issue.project_id).get(issue.iid, lazy=True)
    project_issue.reorder(move_after_id=move_after_id, move_before_id=move_before_id)


def get_user_email(assignee_id: int):

    try:
        user = gitlab_auth_object.users.get(assignee_id)
        public_email = getattr(user, "public_email", None)
        if not public_email:
            return None
        return public_email

    except gitlab.exceptions.GitlabAuthenticationError:
        __print_and_fail__(
            "Authentication failed. Check your URL and Personal Access Token."
        )
    except Exception as e:
        __print_and_fail__(f"An unexpected error occurred: {e}")

    return None


def __print_on_dry_run_mode__(message: str):
    console.print(
        Text("✅", style="bold green"),
        "Dry run mode:",
        Text("enabled", style="bold green"),
        ". Message: ",
        Text(message, style="bold blue"),
    )


def print_issue_to_console(issue, due_date=None):
    assignee, assignee_email = get_assignee_data(issue)
    if not assignee:
        assignee = ""
    if not assignee_email:
        assignee_email = ""

    console.print(
        Text(issue_reference(issue), style="blue"),
        "\t",
        Text(issue.title, style="bold white"),
        Text(assignee, style="yellow"),
        Text(assignee_email, style="green"),
        end=None,
    )
    if due_date is not None:
        console.print(Text(f" {due_date}", style="bold red"), end=None)
    console.print()


def send_notification_from_cli(
    notify_admin: bool,
    notify_assignee: bool,
    issues_with_problems: dict,
    repo_name: str,
    send_emain_func,
) -> bool:
    """Abstraction function to send emails from CLI and set some parameters

    Returns:
        bool: False if at least one email could not be sent.
    """

    all_sent = True

    def _send(user_email, user_name, issues_found):
        nonlocal all_sent
        if not issues_found:
            return
        if settings.dry_run:
            __print_on_dry_run_mode__(f"no email will be send to {user_email}")
            return
        sent = send_emain_func(
            user_email,
            user_name,
            repository_name=repo_name,
            issues_block=convert_to_html_list(issues_found),
        )
        if sent:
            console.print(Text(f"\nEmail has been sent to {user_email}.", style="bold green"))
        else:
            all_sent = False
            console.print(
                Text(
                    f"\n❌ Email to {user_email} could not be sent (see log).",
                    style="bold red",
                )
            )

    if notify_admin:
        user_email = os.environ.get("EMAIL_ADMIN_TO_NOTIFY")
        _send(user_email, "Admin", issues_with_problems.get(user_email, []))
    elif notify_assignee:
        for user_email, items in issues_with_problems.items():
            _send(user_email, items[0]["assignee"], items)
    else:
        console.print(Text("\nNo one will be notified this time.", style="bold yellow"))

    return all_sent


def get_assignee_data(issue):
    assignee, assignee_email = None, None
    if getattr(issue, "assignee", None) is not None:
        assignee = issue.assignee["name"]
        assignee_email = get_user_email(issue.assignee["id"])
    return assignee, assignee_email


def label_exists(scope: Scope, label_name: str) -> bool:
    """True if the label exists in the project/group (incl. parent groups)."""
    try:
        scope.labels.get(label_name)
        return True
    except gitlab.GitlabGetError:
        return False


def check_if_label_exists(scope: Scope, label_name: str):
    """
    Checks if a label with the given name exists in the GitLab project or group
    (labels of parent groups are included).
    """
    try:
        scope.labels.get(label_name)
        return True
    except gitlab.GitlabGetError:
        __print_and_fail__(f"Label with name '{label_name}' not found.")
    except Exception:
        __print_and_fail__("An unexpected error occurred")


def get_board_id_by_name(scope: Scope, board_name: str) -> Optional[int]:
    """
    Finds a GitLab board's ID within a project or group based on its name.

    Returns:
        The integer ID of the board if found, otherwise None.
    """

    try:
        all_boards = scope.boards.list(get_all=True)
        found_board = next(
            (board for board in all_boards if board.name == board_name), None
        )
        return found_board.id if found_board else None

    except gitlab.exceptions.GitlabError:
        __print_and_fail__("Error fetching boards from GitLab.")
    except Exception:
        __print_and_fail__("An unexpected error occurred")

    return None


def get_board(scope: Scope, board_id: Optional[int], board_name: Optional[str]):
    """Returns the requested board (by name or ID) or the first board."""

    console.print("\nFetching boards...")
    boards = scope.boards.list(get_all=True)

    if not boards:
        console.print(Text("No Issue Boards found.", style="bold yellow"))
        return None

    if board_name:
        board_id = get_board_id_by_name(scope, board_name)
        if board_id is None:
            __print_and_fail__(f"Board with name '{board_name}' not found.")

    if board_id:
        try:
            return scope.boards.get(board_id)
        except gitlab.exceptions.GitlabGetError:
            __print_and_fail__(f"Board with ID '{board_id}' not found.")

    board = boards[0]
    console.print(
        f"Using the first board found (ID: {board.id}):",
        Text(board.name, style="bold green"),
    )
    return board


def get_board_label_names(board) -> list:
    """Returns the label names of all label lists of a board (in board order)."""
    names = []
    for list_obj in board.lists.list(get_all=True):
        label = getattr(list_obj, "label", None)
        if label:  # skip assignee/milestone/iteration lists
            names.append(label["name"])
    return names


# --------------------------------------------------------------------------- #
# Sorting
# --------------------------------------------------------------------------- #


SORT_KEYS = ("due-date", "priority", "overdue", "due-within:<days>")


def today() -> "datetime.date":
    """Current date in UTC (same reference as the due-date checks).
    Can be overridden with ISQA_TODAY=YYYY-MM-DD (e.g. for tests)."""
    override = os.environ.get("ISQA_TODAY")
    if override:
        return datetime.strptime(override, "%Y-%m-%d").date()
    return datetime.now(timezone.utc).date()


def is_overdue(issue, ref_date=None) -> bool:
    due = _due_date_of(issue)
    return due is not None and due < (ref_date or today())


def _due_date_of(issue):
    if not issue.due_date:
        return None
    return datetime.strptime(issue.due_date, "%Y-%m-%d").date()


def _priority_rank(issue, priority_labels: list) -> int:
    """0 = highest priority. Issues without priority label get len(labels),
    i.e. they go behind the lowest priority. With several priority labels the
    highest one counts."""
    ranks = [priority_labels.index(l) for l in (issue.labels or []) if l in priority_labels]
    return min(ranks) if ranks else len(priority_labels)


def _target_order(
    issues: list,
    desc: bool = False,
    sort_by: tuple = ("due-date",),
    priority_labels: tuple = (),
) -> list:
    """Sorts issues by the given keys (in order of importance).

    * due-date: earliest first (latest first with `desc`); no due date at the end
    * priority: order of `priority_labels`; no priority label at the end
    * overdue: overdue issues (due date before today) first
    * due-within:N: issues due within the next N days (incl. overdue) first

    The sort is stable: issues that are equal in all keys keep their current
    manual order.
    """
    priority_labels = list(priority_labels)
    ref = today()

    def key(issue):
        parts = []
        for k in sort_by:
            if k == "due-date":
                due = _due_date_of(issue)
                if due is None:
                    parts.append((1, 0))
                else:
                    o = due.toordinal()
                    parts.append((0, -o if desc else o))
            elif k == "priority":
                parts.append((_priority_rank(issue, priority_labels),))
            elif k == "overdue":
                parts.append((0 if is_overdue(issue, ref) else 1,))
            elif k.startswith("due-within:"):
                due = _due_date_of(issue)
                limit = ref + timedelta(days=int(k.split(":", 1)[1]))
                parts.append((0 if due is not None and due <= limit else 1,))
            else:
                raise ValueError(f"Unknown sort key: {k}")
        return tuple(parts)

    return sorted(issues, key=key)


def parse_sort_by(value: str) -> tuple:
    """'due-date,priority' -> ('due-date', 'priority') (validated)."""
    keys = tuple(k.strip() for k in (value or "").split(",") if k.strip())
    if not keys:
        keys = ("due-date",)
    def valid(k):
        if k in ("due-date", "priority", "overdue"):
            return True
        if k.startswith("due-within:"):
            n = k.split(":", 1)[1]
            return n.isdigit()
        return False

    unknown = [k for k in keys if not valid(k)]
    if unknown:
        __print_and_fail__(
            f"Unknown sort key(s) {unknown}. Allowed: {', '.join(SORT_KEYS)} "
            "(e.g. 'due-within:7')."
        )
    if len(set(keys)) != len(keys):
        __print_and_fail__(f"Sort keys must not repeat: {value}")
    return keys


def parse_priority_labels(value: str) -> tuple:
    """'A,B,C' -> ('A', 'B', 'C'), highest priority first."""
    return tuple(l.strip() for l in (value or "").split(",") if l.strip())


def _fetch_label_issues(scope: Scope, label_name: str) -> list:
    return scope.issues.list(
        labels=[label_name],
        state="opened",
        order_by="relative_position",
        sort="asc",
        get_all=True,
    )


def describe_issue(issue, priority_labels: tuple = ()) -> str:
    """'#12 "Title" (due 2026-11-20, 11_Prio_High)' - for log output."""
    details = [f"due {issue.due_date}" if issue.due_date else "no due date"]
    if is_overdue(issue):
        details[0] += ", OVERDUE"
    prios = [l for l in (issue.labels or []) if l in priority_labels]
    if priority_labels:
        details.append(", ".join(prios) if prios else "no priority")
    title = issue.title if len(issue.title) <= 60 else issue.title[:57] + "..."
    return f'{issue_reference(issue)} "{title}" ({", ".join(details)})'


def print_issue_order(heading: str, issues: list, priority_labels: tuple = ()):
    """Prints a table of issues in the given order (verbose/debug output)."""
    table = Table(title=heading, title_justify="left", show_lines=False)
    table.add_column("#", justify="right", style="dim")
    table.add_column("Issue", style="blue", no_wrap=True)
    table.add_column("Title")
    table.add_column("Due date", no_wrap=True)
    if priority_labels:
        table.add_column("Priority", no_wrap=True)
    for n, issue in enumerate(issues, 1):
        due = issue.due_date or "-"
        if is_overdue(issue):
            due += " (overdue)"
        row = [str(n), issue_reference(issue), issue.title, due]
        if priority_labels:
            prios = [l for l in (issue.labels or []) if l in priority_labels]
            row.append(", ".join(prios) or "-")
        table.add_row(*row)
    console.print(table)


def _constrained_target(
    issues: list,
    pinned: set,
    desc: bool = False,
    sort_by: tuple = ("due-date",),
    priority_labels: tuple = (),
) -> list:
    """Target order when some issues are pinned (must keep their current
    relative order, because they belong to a list with higher precedence).

    Free issues are sorted by the sort keys and merged around the pinned ones.
    Without pinned issues this is exactly `_target_order`.
    """
    if not pinned:
        return _target_order(issues, desc, sort_by, priority_labels)

    order = {i.id: n for n, i in enumerate(issues)}
    ranked = _target_order(issues, desc, sort_by, priority_labels)
    rank = {i.id: n for n, i in enumerate(ranked)}  # position in the ideal order

    free = [i for i in ranked if i.id not in pinned]
    fixed = [i for i in issues if i.id in pinned]  # current order
    target = []
    while free and fixed:
        f, p = free[0], fixed[0]
        if (rank[f.id], order[f.id]) < (rank[p.id], order[p.id]):
            target.append(free.pop(0))
        else:
            target.append(fixed.pop(0))
    return target + free + fixed


def _sort_pass(issues: list, target: list, pinned: set = frozenset(),
               priority_labels: tuple = ()) -> int:
    """Brings `issues` (current order) into `target` order.

    Walks through the target order and places each free issue directly behind
    its predecessor (between the predecessor and its current neighbour); the
    first issue goes to the top. Pinned issues are never moved. Issues already
    at the right place are not touched.

    Returns:
        int: number of reorder API calls (or simulated calls in dry-run mode)
    """
    current = [i.id for i in issues]
    moves = 0

    for n, cur in enumerate(target):
        if cur.id in pinned:
            continue
        if n == 0:
            if current[0] == cur.id:
                continue
            current.remove(cur.id)
            before_id, after_id, prev = None, current[0], None
            insert_at = 0
        else:
            prev = target[n - 1]
            p = current.index(prev.id)
            if p + 1 < len(current) and current[p + 1] == cur.id:
                continue  # already directly behind its predecessor
            current.remove(cur.id)
            p = current.index(prev.id)
            before_id = prev.id
            after_id = current[p + 1] if p + 1 < len(current) else None
            insert_at = p + 1

        if settings.verbose or settings.dry_run:
            if prev is None:
                console.print("  move", Text(describe_issue(cur, priority_labels), style="blue"),
                              "\n    to the top")
            else:
                console.print(
                    "  move",
                    Text(describe_issue(cur, priority_labels), style="blue"),
                    "\n    behind",
                    Text(describe_issue(prev, priority_labels), style="cyan"),
                )

        if not settings.dry_run:
            reorder_issue(cur, move_before_id=before_id, move_after_id=after_id)
            time.sleep(ISSUES_REORDER_DELAY_SECONDS)

        current.insert(insert_at, cur.id)
        moves += 1

    return moves


def _is_sorted(issues: list, target: list) -> bool:
    return [i.id for i in target] == [i.id for i in issues]


def _pinned_ids(issues: list, pinned_labels) -> set:
    pinned_labels = set(pinned_labels or ())
    return {i.id for i in issues if pinned_labels.intersection(i.labels or [])}


def sort_issues_in_label(
    scope: Scope,
    list_label_name: str,
    desc: bool = False,
    sort_by: tuple = ("due-date",),
    priority_labels: tuple = (),
    pinned_labels: tuple = (),
) -> int:
    """Sorts the open issues with the given label (see `_target_order`).

    Issues that also carry one of `pinned_labels` (lists with higher
    precedence) are not moved. Works for project boards and group boards.

    Returns:
        int: number of issues that were moved (0 = already sorted)
    """

    total_moves = 0

    for attempt in range(1, ISSUES_REORDER_MAX_PASSES + 1):
        issues = _fetch_label_issues(scope, list_label_name)
        pinned = _pinned_ids(issues, pinned_labels)
        target = _constrained_target(issues, pinned, desc, sort_by, priority_labels)

        if len(issues) < 2 or _is_sorted(issues, target):
            if settings.verbose and attempt == 1:
                print_issue_order(
                    f"Order of '{list_label_name}' (already sorted)", issues, priority_labels
                )
            return total_moves

        if settings.verbose:
            if attempt == 1:
                print_issue_order(f"Current order of '{list_label_name}'", issues, priority_labels)
                print_issue_order(f"Target order of '{list_label_name}'", target, priority_labels)
                if pinned:
                    console.print(
                        "  pinned (also in a list with higher precedence):",
                        ", ".join(issue_reference(i) for i in issues if i.id in pinned),
                    )
            console.print("Sort pass:", Text(str(attempt), style="bold yellow"))

        total_moves += _sort_pass(issues, target, pinned, priority_labels)

        if settings.dry_run:
            return total_moves

    # final check
    issues = _fetch_label_issues(scope, list_label_name)
    pinned = _pinned_ids(issues, pinned_labels)
    target = _constrained_target(issues, pinned, desc, sort_by, priority_labels)
    if not _is_sorted(issues, target):
        print_issue_order(f"Current order of '{list_label_name}'", issues, priority_labels)
        print_issue_order(f"Target order of '{list_label_name}'", target, priority_labels)
        __print_and_fail__(
            f"Issues of label '{list_label_name}' could not be fully sorted "
            f"after {ISSUES_REORDER_MAX_PASSES} passes."
        )

    return total_moves


def sort_board_lists(scope: Scope, plan: list, desc: bool, priority_labels: tuple) -> dict:
    """Sorts several board lists consistently.

    GitLab stores ONE position per issue, shared by all board lists the issue
    appears in. Sorting one list can therefore change the order of another
    list, if issues carry several column labels.

    `plan` is a list of (label, sort_keys) in order of precedence. Lists are
    sorted in this order; issues that also belong to a list sorted before are
    pinned (not moved), so a list never breaks the order of a list with higher
    precedence. Contradictions are reported per list.

    Returns:
        dict: label -> {"moves": int, "sorted": bool, "conflicts": [str]}
    """
    result = {}
    done = []

    for label, keys in plan:
        console.print(
            Text("\n⚙️", style="bold cyan"),
            "Sorting list",
            Text(label, style="bold yellow"),
            "by",
            Text(" > ".join(keys), style="bold yellow"),
        )
        moves = sort_issues_in_label(scope, label, desc, keys, priority_labels, tuple(done))
        if moves == 0:
            console.print(Text("  already sorted, nothing to do", style="white"))
        elif settings.dry_run:
            console.print(Text("  [dry-run]", style="bold yellow"), f"{moves} issue(s) would be moved.")
        else:
            console.print(Text("  ✅", style="bold green"), f"{moves} issue(s) moved.")

        # compare with the ideal order (without pinned issues)
        issues = _fetch_label_issues(scope, label)
        pinned = _pinned_ids(issues, done)
        ideal = _target_order(issues, desc, keys, priority_labels)
        conflicts = []
        if pinned and not _is_sorted(
            _constrained_target(issues, pinned, desc, keys, priority_labels), ideal
        ):
            ideal_pos = {i.id: n for n, i in enumerate(ideal)}
            constrained = _constrained_target(issues, pinned, desc, keys, priority_labels)
            for n, issue in enumerate(constrained):
                if issue.id in pinned and ideal_pos[issue.id] != n:
                    others = [l for l in done if l in (issue.labels or [])]
                    conflicts.append(
                        f"{describe_issue(issue, priority_labels)} - also in "
                        + ", ".join(f"'{o}'" for o in others)
                    )
        result[label] = {"moves": moves, "sorted": not conflicts, "conflicts": conflicts}
        done.append(label)

    return result


def print_issues_in_label(label_issues, label_name: str):
    console.print(
        Text("\n⚙️", style="bold cyan"),
        "Listing issues of the label:",
        Text(label_name, style="bold yellow"),
    )
    for _, issue in enumerate(label_issues):
        print_issue_to_console(issue)
