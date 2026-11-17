"""End-to-end tests: isqa CLI (subprocess) against the fake GitLab API."""

import os
import random
import subprocess
import sys
from datetime import date, timedelta

import pytest

sys.path.insert(0, os.path.dirname(__file__))
import fake_gitlab as fg  # noqa: E402

ISQA = os.path.join(os.path.dirname(sys.executable), "isqa")
TODAY = date.today()


def d(days):
    return (TODAY + timedelta(days=days)).isoformat()


@pytest.fixture()
def gl(tmp_path):
    fg.STORE = fg.Store()
    S = fg.STORE
    # group tree: offis (10) -> team (11) ; projects in both
    S.groups[10] = {"id": 10, "full_path": "offis", "parent_id": None, "name": "offis"}
    S.groups[11] = {"id": 11, "full_path": "offis/team", "parent_id": 10, "name": "team"}
    S.groups[20] = {"id": 20, "full_path": "other", "parent_id": None, "name": "other"}
    S.projects[100] = {"id": 100, "path_with_namespace": "offis/team/alpha", "group_id": 11, "name": "alpha"}
    S.projects[101] = {"id": 101, "path_with_namespace": "offis/team/beta", "group_id": 11, "name": "beta"}
    S.projects[102] = {"id": 102, "path_with_namespace": "offis/gamma", "group_id": 10, "name": "gamma"}
    S.projects[200] = {"id": 200, "path_with_namespace": "other/x", "group_id": 20, "name": "x"}
    S.labels[("group", 10)] = {"ToDo", "Waiting", "TODO_Date", "Termin_durchführen", "In Progress"}
    S.labels[("group", 20)] = {"ToDo"}
    S.boards[("group", 11)] = [
        {"id": 1, "name": "Team", "lists": ["ToDo", "Waiting", "In Progress"]},
        {"id": 2, "name": "Termine", "lists": ["Termin_durchführen"]},
    ]
    S.boards[("project", 100)] = [{"id": 5, "name": "Alpha", "lists": ["ToDo"]}]
    S.users[7] = {"id": 7, "name": "Max Muster", "username": "max", "public_email": "max@example.org"}
    S.users[8] = {"id": 8, "name": "Erika", "username": "erika", "public_email": ""}
    srv = fg.start()
    env = dict(
        os.environ,
        GITLAB_URL=f"http://127.0.0.1:{srv.server_port}",
        GITLAB_TOKEN="secret",
        ISQA_REORDER_DELAY_SECONDS="0",
        NO_PROXY="127.0.0.1,localhost",
        no_proxy="127.0.0.1,localhost",
    )
    env.pop("GITLAB_PROJECT_ID", None)
    env.pop("GITLAB_GROUP_ID", None)

    def run(*args, extra_env=None, ok=True):
        e = dict(env, **(extra_env or {}))
        r = subprocess.run([ISQA, *args], env=e, cwd=tmp_path, capture_output=True, text=True, timeout=120)
        out = r.stdout + r.stderr
        if ok:
            assert r.returncode == 0, out
        return r.returncode, out

    yield S, run
    srv.shutdown()


_next = {"id": 1000}


def add_issue(S, project_id, labels, due=None, pos=None, assignee=None, state="opened", title=None):
    _next["id"] += 1
    gid = _next["id"]
    iid = sum(1 for i in S.issues.values() if i["project_id"] == project_id) + 1
    S.issues[gid] = {
        "id": gid,
        "iid": iid,
        "project_id": project_id,
        "title": title or f"issue {gid}",
        "labels": list(labels),
        "due_date": due,
        "state": state,
        "relative_position": pos,
        "assignee_id": assignee,
    }
    return S.issues[gid]


def list_order(S, label, root=10):
    items = [i for i in S.scope_issues(root) if label in i["labels"] and i["state"] == "opened"]
    return [i["id"] for i in S.ordered(items)]


def expected(S, label, desc=False, root=10):
    """Stable sort of the current order by due date, no due date at the end."""
    cur = [S.issues[i] for i in list_order(S, label, root)]
    with_due = sorted([i for i in cur if i["due_date"]], key=lambda i: i["due_date"], reverse=desc)
    return [i["id"] for i in with_due + [i for i in cur if not i["due_date"]]]


# --------------------------------------------------------------------------- #


def test_group_sort_across_projects(gl):
    S, run = gl
    pos = 0
    rnd = random.Random(1)
    for n in range(12):
        pos += 1000
        add_issue(S, rnd.choice([100, 101]), ["ToDo"], due=rnd.choice([d(rnd.randint(-5, 30)), None]), pos=pos)
        pos += 1000
        add_issue(S, rnd.choice([100, 101]), ["Waiting"], due=d(rnd.randint(0, 9)), pos=pos)  # interleaved, other list
    # closed issue must be ignored
    add_issue(S, 100, ["ToDo"], due=d(-100), pos=500, state="closed")
    want_todo = expected(S, "ToDo")
    want_wait = expected(S, "Waiting")
    assert list_order(S, "ToDo") != want_todo

    _, out = run("--group", "offis/team", "sort-board-issues", "--board-name", "Team")
    assert list_order(S, "ToDo") == want_todo, out
    assert list_order(S, "Waiting") == want_wait, out

    # second run: nothing to do, no reorder calls
    S.calls.clear()
    _, out = run("--group", "offis/team", "sort-board-issues", "--board-name", "Team")
    assert not [c for c in S.calls if c[1].endswith("/reorder")], out
    assert "already sorted" in out


def test_group_sort_descending_and_single_label(gl):
    S, run = gl
    for k, due in enumerate([d(3), None, d(1), d(10), d(1), None, d(5)]):
        add_issue(S, [100, 101][k % 2], ["ToDo"], due=due, pos=(k + 1) * 10)
    add_issue(S, 100, ["Waiting"], due=d(9), pos=1)
    add_issue(S, 100, ["Waiting"], due=d(2), pos=2)
    before_wait = list_order(S, "Waiting")
    want = expected(S, "ToDo", desc=True)
    run("-g", "11", "sort-board-issues", "-l", "ToDo", "--descending")
    assert list_order(S, "ToDo") == want
    assert list_order(S, "Waiting") == before_wait  # untouched
    # no-due-date issues stay at the end also in descending mode
    assert [S.issues[i]["due_date"] for i in list_order(S, "ToDo")][-2:] == [None, None]


def test_dry_run_changes_nothing(gl):
    S, run = gl
    for k, due in enumerate([d(5), d(1), d(3)]):
        add_issue(S, 100, ["ToDo"], due=due, pos=(k + 1) * 10)
    before = list_order(S, "ToDo")
    _, out = run("--dry-run", "--group", "offis/team", "sort-board-issues")
    assert list_order(S, "ToDo") == before
    assert not [c for c in S.calls if c[0] == "PUT"]
    assert "would be moved" in out


def test_project_mode_still_works(gl):
    S, run = gl
    for k, due in enumerate([d(5), None, d(1), d(3)]):
        add_issue(S, 100, ["ToDo"], due=due, pos=(k + 1) * 10)
    add_issue(S, 101, ["ToDo"], due=d(-50), pos=25)  # other project, must stay unaffected
    run(extra_env={"GITLAB_PROJECT_ID": "offis/team/alpha"}, *["sort-board-issues"])
    alpha = [i for i in list_order(S, "ToDo") if S.issues[i]["project_id"] == 100]
    assert [S.issues[i]["due_date"] for i in alpha] == [d(1), d(3), d(5), None]


def test_unknown_label_or_board_fails(gl):
    S, run = gl
    rc, out = run("-g", "offis/team", "sort-board-issues", "-l", "Nope", ok=False)
    assert rc != 0 and "None of the given labels" in out
    rc, out = run("-g", "offis/team", "sort-board-issues", "-n", "Nope", ok=False)
    assert rc != 0 and "not found" in out
    rc, out = run("-g", "offis/team", "sort-board-issues", "-n", "Team", "-l", "TODO_Date", ok=False)
    assert rc != 0 and "None of the given labels" in out
    # --strict: fail as before
    rc, out = run("-g", "offis/team", "sort-board-issues", "--strict", "-l", "ToDo", "-l", "Nope", ok=False)
    assert rc != 0 and "not found" in out
    rc, out = run("-g", "offis/team", "sort-board-issues", "--strict", "-n", "Team", "-l", "ToDo",
                  "-l", "TODO_Date", ok=False)
    assert rc != 0 and "not used as lists" in out


def test_missing_columns_are_skipped_with_warning(gl):
    S, run = gl
    for k, due in enumerate([d(5), d(1), d(3)]):
        add_issue(S, 100, ["ToDo"], due=due, pos=(k + 1) * 10)
    for k, due in enumerate([d(9), d(2)]):
        add_issue(S, 101, ["Waiting"], due=due, pos=(k + 1) * 100)
    rc, out = run("-g", "offis/team", "sort-board-issues", "-n", "Team",
                  "-l", "ToDo", "-l", "TODO_Date", "-l", "Nope", "-l", "Waiting")
    assert rc == 0
    assert "Label 'TODO_Date' is not a list on board 'Team' - skipped" in out
    assert "Label 'Nope' does not exist (typo?)" in out
    assert [S.issues[i]["due_date"] for i in list_order(S, "ToDo")] == [d(1), d(3), d(5)]
    assert [S.issues[i]["due_date"] for i in list_order(S, "Waiting")] == [d(2), d(9)]


def test_group_migrate(gl):
    S, run = gl
    a = add_issue(S, 100, ["TODO_Date", "prio"], due=d(-1))  # yesterday -> migrate
    b = add_issue(S, 102, ["Waiting"], due=d(-10))  # older, other project in parent group? (102 is in offis, not offis/team)
    c = add_issue(S, 101, ["Waiting"], due=d(0))  # today -> stays
    e = add_issue(S, 101, ["Waiting"], due=None)  # no due date -> stays
    f = add_issue(S, 101, ["Termin_durchführen"], due=d(-2))  # migrate
    g = add_issue(S, 200, ["Waiting"], due=d(-3))  # other group -> untouched

    for lab in ["TODO_Date", "Waiting", "Termin_durchführen"]:
        run("--group", "offis", "migrate-issues", "-f", lab, "-t", "ToDo", "--check-condition", "due-date")

    assert a["labels"] == ["prio", "ToDo"]
    assert b["labels"] == ["ToDo"]
    assert c["labels"] == ["Waiting"]
    assert e["labels"] == ["Waiting"]
    assert f["labels"] == ["ToDo"]
    assert g["labels"] == ["Waiting"]


def test_group_migrate_dry_run_and_missing_label(gl):
    S, run = gl
    a = add_issue(S, 100, ["Waiting"], due=d(-1))
    _, out = run("-d", "-g", "offis", "migrate-issues", "-f", "Waiting", "-t", "ToDo", "--check-condition", "due-date")
    assert a["labels"] == ["Waiting"] and "would migrate" in out
    rc, out = run("-g", "offis", "migrate-issues", "-f", "Waiting", "-t", "Tod0", "--check-condition", "due-date", ok=False)
    assert rc != 0 and a["labels"] == ["Waiting"]


def test_list_commands(gl):
    S, run = gl
    add_issue(S, 100, ["ToDo"], due=d(1), pos=1, title="Alpha task")
    add_issue(S, 102, ["Waiting"], due=d(1), pos=2, title="Gamma waits")
    add_issue(S, 101, [], pos=3, title="Unlabeled beta")
    _, out = run("-g", "offis", "list-issues")
    assert "offis/team/alpha#1" in out and "offis/gamma#1" in out and "Total issues: 3" in out
    S.boards[("group", 10)] = [{"id": 9, "name": "All", "lists": ["ToDo", "Waiting"]}]
    _, out = run("-g", "offis", "list-board-issues", "--without-labels")
    assert "Alpha task" in out and "Gamma waits" in out and "Unlabeled beta" in out


@pytest.fixture()
def smtp():
    from aiosmtpd.controller import Controller

    class H:
        def __init__(self):
            self.msgs = []

        async def handle_DATA(self, server, session, envelope):
            self.msgs.append((envelope.rcpt_tos, envelope.content.decode("utf8", "replace")))
            return "250 OK"

    import ssl
    import tempfile

    tmp = tempfile.mkdtemp()
    subprocess.run(
        ["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "1", "-subj", "/CN=localhost",
         "-keyout", f"{tmp}/k.pem", "-out", f"{tmp}/c.pem"],
        check=True, capture_output=True,
    )
    ctx = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
    ctx.load_cert_chain(f"{tmp}/c.pem", f"{tmp}/k.pem")
    h = H()
    c = Controller(h, hostname="127.0.0.1", port=8025, tls_context=ctx, require_starttls=True)
    c.start()
    yield h
    c.stop()


MAIL_ENV = {
    "SMTP_SERVER": "127.0.0.1",
    "SMTP_PORT": "8025",
    "EMAIL_SENDER": "isqa@example.org",
    "EMAIL_ADMIN_TO_NOTIFY": "admin@example.org",
}


def test_group_check_due_date_mail_without_login(gl, smtp):
    S, run = gl
    add_issue(S, 100, ["ToDo"], due=d(-1), assignee=7, title="Late alpha")
    add_issue(S, 102, ["ToDo"], due=d(-3), assignee=8, title="Late gamma (no public mail)")
    add_issue(S, 101, ["ToDo"], due=d(2), assignee=7, title="Not late")
    _, out = run("-g", "offis", "check-due-date", "--notify-assignee", extra_env=MAIL_ENV)
    rcpts = sorted(r[0][0] for r in smtp.msgs)
    assert rcpts == ["admin@example.org", "max@example.org"], out
    body = dict((r[0][0], r[1]) for r in smtp.msgs)
    assert "Late alpha" in body["max@example.org"] and "Not late" not in body["max@example.org"]
    assert "Late gamma" in body["admin@example.org"]
    assert "offis" in body["admin@example.org"]  # group name as "repository"


def test_check_missing_assignee_no_empty_mail(gl, smtp):
    S, run = gl
    add_issue(S, 100, ["ToDo"], assignee=7)
    _, out = run("-g", "offis", "check-missing-assignee", "--notify-admin", extra_env=MAIL_ENV)
    assert smtp.msgs == [], out
    add_issue(S, 101, ["ToDo"], title="Nobody owns me")
    run("-g", "offis", "check-missing-assignee", "--notify-admin", extra_env=MAIL_ENV)
    assert len(smtp.msgs) == 1 and "Nobody owns me" in smtp.msgs[0][1]


def test_check_missing_label_and_mail_failure_exit_code(gl):
    S, run = gl
    add_issue(S, 100, [], title="No label here")
    env = dict(MAIL_ENV, SMTP_PORT="8999")  # nobody listening
    rc, out = run("-g", "offis", "check-missing-label", "--notify-admin", extra_env=env, ok=False)
    assert rc != 0 and "could not be sent" in out


@pytest.mark.parametrize("seed", range(25))
def test_sort_random(gl, seed):
    """Random boards incl. duplicates, missing dates, null positions, tight gaps."""
    S, run = gl
    rnd = random.Random(seed)
    n = rnd.randint(2, 40)
    used = set()
    for k in range(n):
        pos = rnd.choice([None] + [rnd.randint(-5000, 5000)] * 6) if seed % 3 == 0 else rnd.randint(-50, 50)
        while pos is not None and pos in used:
            pos += 1
        if pos is not None:
            used.add(pos)
        add_issue(S, rnd.choice([100, 101, 102]), ["ToDo"],
                  due=rnd.choice([None, d(rnd.randint(-3, 6))]), pos=pos)
        if rnd.random() < 0.5:  # noise issues in other lists, same root group
            p2 = rnd.randint(-5000, 5000)
            while p2 in used:
                p2 += 1
            used.add(p2)
            add_issue(S, rnd.choice([100, 101]), ["Waiting"], pos=p2)
    desc = seed % 2 == 1
    want = expected(S, "ToDo", desc=desc)
    args = ["-g", "offis", "sort-board-issues", "-l", "ToDo"] + (["--descending"] if desc else [])
    S.boards[("group", 10)] = [{"id": 3, "name": "Root", "lists": ["ToDo", "Waiting"]}]
    _, out = run(*args)
    got = list_order(S, "ToDo")
    dues = [S.issues[i]["due_date"] for i in got]
    with_due = [x for x in dues if x]
    assert with_due == sorted(with_due, reverse=desc), out
    assert all(x is None for x in dues[len(with_due):]), out
    moves = len([c for c in S.calls if c[1].endswith("/reorder")])
    assert moves <= 3 * (n - 1)
    if seed % 3 != 0:  # without null positions the result is exactly the stable sort
        assert got == want, out


# --------------------------------------------------------------------------- #
# Priority sorting
# --------------------------------------------------------------------------- #

PRIO = "11_Prio_High,12_Prio_Medium,13_Prio_Low"
H, M, L = "11_Prio_High", "12_Prio_Medium", "13_Prio_Low"


def _prio_setup(S):
    S.labels[("group", 10)] |= {H, M, L}
    data = [  # (due, prio-labels)
        (d(5), [L]),
        (d(1), [M]),
        (None, [L]),
        (d(1), [H]),
        (d(5), []),
        (None, [H]),
        (d(1), []),
        (d(5), [H]),
        (None, []),
        (d(1), [L, H]),  # several prio labels -> highest counts
    ]
    issues = []
    for k, (due, prio) in enumerate(data):
        issues.append(add_issue(S, [100, 101][k % 2], ["ToDo", *prio], due=due, pos=(k + 1) * 100,
                                title=f"t{k}"))
    return issues


def _titles(S, label="ToDo"):
    return [S.issues[i]["title"] for i in list_order(S, label)]


def test_sort_due_date_then_priority(gl):
    S, run = gl
    _prio_setup(S)
    run("-g", "offis/team", "sort-board-issues", "-l", "ToDo", "--sort-by", "due-date,priority",
        "--priority-labels", PRIO)
    # due d+1: t3(H) t9(H, also L) t1(M) t6(-) | due d+5: t7(H) t0(L) t4(-) | none: t5(H) t2(L) t8(-)
    assert _titles(S) == ["t3", "t9", "t1", "t6", "t7", "t0", "t4", "t5", "t2", "t8"]


def test_sort_priority_then_due_date_via_env(gl):
    S, run = gl
    _prio_setup(S)
    run("-g", "offis/team", "sort-board-issues", "-l", "ToDo", "-s", "priority,due-date",
        extra_env={"ISQA_PRIORITY_LABELS": PRIO})
    assert _titles(S) == ["t3", "t9", "t7", "t5", "t1", "t0", "t2", "t6", "t4", "t8"]


def test_sort_priority_only_keeps_manual_order(gl):
    S, run = gl
    _prio_setup(S)
    run("-g", "offis/team", "sort-board-issues", "-l", "ToDo", "-s", "priority", "-P", PRIO)
    assert _titles(S) == ["t3", "t5", "t7", "t9", "t1", "t0", "t2", "t4", "t6", "t8"]


def test_sort_due_date_desc_then_priority(gl):
    S, run = gl
    _prio_setup(S)
    run("-g", "offis/team", "sort-board-issues", "-l", "ToDo", "-s", "due-date,priority", "-P", PRIO,
        "--descending")
    assert _titles(S) == ["t7", "t0", "t4", "t3", "t9", "t1", "t6", "t5", "t2", "t8"]
    # idempotent: second run moves nothing
    S.calls.clear()
    _, out = run("-g", "offis/team", "sort-board-issues", "-l", "ToDo", "-s", "due-date,priority",
                 "-P", PRIO, "--descending")
    assert not [c for c in S.calls if c[1].endswith("/reorder")] and "already sorted" in out


def test_default_sort_unchanged_with_priority_labels_present(gl):
    S, run = gl
    _prio_setup(S)
    want = expected(S, "ToDo")
    run("-g", "offis/team", "sort-board-issues", "-l", "ToDo", extra_env={"ISQA_PRIORITY_LABELS": PRIO})
    assert list_order(S, "ToDo") == want


def test_priority_config_errors(gl):
    S, run = gl
    _prio_setup(S)
    rc, out = run("-g", "offis/team", "sort-board-issues", "-s", "priority", ok=False)
    assert rc != 0 and "needs --priority-labels" in out
    rc, out = run("-g", "offis/team", "sort-board-issues", "-s", "prio", "-P", PRIO, ok=False)
    assert rc != 0 and "Unknown sort key" in out
    rc, out = run("-g", "offis/team", "sort-board-issues", "-s", "priority", "-P", "11_Prio_High,Typo", ok=False)
    assert rc != 0 and "not found" in out


# --------------------------------------------------------------------------- #
# add-default-label
# --------------------------------------------------------------------------- #

COLUMNS = ["ToDo_ideas", "ToDo_date", "ToDo", "Doing", "Wait", "Termin durchführen", "39_Supervision",
           "Discussion_SFz", "Info_SFz", "Discussion_SCO", "Info_SCO", "Discussion_ANi", "Decision_ANi",
           "Info_ANi", "Discussion_EFr_SFz_ANi", "info_backlog"]


def _col_args(cols=COLUMNS):
    out = []
    for c in cols:
        out += ["-c", c]
    return out


def test_add_default_label_explicit_columns(gl):
    S, run = gl
    S.labels[("group", 10)] |= set(COLUMNS) | {"bug", t_prio := "11_Prio_High"}
    a = add_issue(S, 100, [], title="no labels")
    b = add_issue(S, 101, ["bug", t_prio], title="only other labels")
    c = add_issue(S, 102, ["Termin durchführen"], title="has column with space")
    e = add_issue(S, 100, ["info_backlog", "bug"], title="has column")
    f = add_issue(S, 101, ["ToDo"], title="already ToDo")
    g = add_issue(S, 100, [], state="closed", title="closed")
    h = add_issue(S, 200, [], title="other group")
    _, out = run("-g", "offis", "add-default-label", "-t", "ToDo", *_col_args())
    assert a["labels"] == ["ToDo"]
    assert b["labels"] == ["bug", t_prio, "ToDo"]
    assert c["labels"] == ["Termin durchführen"]
    assert e["labels"] == ["info_backlog", "bug"]
    assert f["labels"] == ["ToDo"]
    assert g["labels"] == [] and h["labels"] == []
    assert "Total issues: 2" in out
    # second run: nothing left
    S.calls.clear()
    _, out = run("-g", "offis", "add-default-label", "-t", "ToDo", *_col_args())
    assert not [c for c in S.calls if c[0] == "PUT"] and "nothing to do" in out


def test_add_default_label_columns_from_board(gl):
    S, run = gl
    a = add_issue(S, 100, ["Termin_durchführen"], title="not on board Team")
    b = add_issue(S, 101, ["Waiting"], title="on board Team")
    run("-g", "offis/team", "add-default-label", "-t", "ToDo", "-n", "Team")
    assert a["labels"] == ["Termin_durchführen", "ToDo"]
    assert b["labels"] == ["Waiting"]


def test_add_default_label_to_label_counts_as_column(gl):
    S, run = gl
    a = add_issue(S, 100, ["ToDo"])
    b = add_issue(S, 100, [])
    run("-g", "offis", "add-default-label", "-t", "ToDo", "-c", "Waiting")
    assert a["labels"] == ["ToDo"] and b["labels"] == ["ToDo"]


def test_add_default_label_dry_run_and_typos(gl):
    S, run = gl
    a = add_issue(S, 100, [])
    _, out = run("-d", "-g", "offis", "add-default-label", "-t", "ToDo", "-c", "Waiting")
    assert a["labels"] == [] and "would add" in out
    rc, out = run("-g", "offis", "add-default-label", "-t", "ToDo", "-c", "Wating", ok=False)
    assert rc != 0 and "not found" in out and a["labels"] == []
    rc, out = run("-g", "offis", "add-default-label", "-t", "Todo2", "-c", "Waiting", ok=False)
    assert rc != 0 and a["labels"] == []


def test_add_default_label_project_mode(gl):
    S, run = gl
    a = add_issue(S, 100, [])
    b = add_issue(S, 101, [])
    run("-p", "offis/team/alpha", "add-default-label", "-t", "ToDo", "-c", "Waiting")
    assert a["labels"] == ["ToDo"] and b["labels"] == []


# --------------------------------------------------------------------------- #
# Several lists with shared issues (--rule)
# --------------------------------------------------------------------------- #

def _sorted_like(S, label, keys, desc, pinned_labels, prio=PRIO.split(",")):
    """Checks a list against the expected (constrained) order using isqa's own model."""
    from types import SimpleNamespace
    from isqa import helpers

    issues = [SimpleNamespace(**{**S.issues[i], "labels": S.issues[i]["labels"]})
              for i in list_order(S, label)]
    pinned = {i.id for i in issues if set(pinned_labels) & set(i.labels)}
    target = helpers._constrained_target(issues, pinned, desc, keys, tuple(prio))
    return [i.id for i in target] == [i.id for i in issues]


def test_rules_conflict_first_rule_wins_and_stable(gl):
    S, run = gl
    S.labels[("group", 10)] |= {H, M, L, "Discussion"}
    S.boards[("group", 11)] = [{"id": 1, "name": "Team", "lists": ["ToDo", "Discussion"]}]
    x = add_issue(S, 100, ["ToDo", "Discussion", L], due=d(1), pos=10, title="x")
    y = add_issue(S, 100, ["ToDo", "Discussion", H], due=d(5), pos=20, title="y")
    add_issue(S, 101, ["ToDo", M], due=d(3), pos=30, title="t")
    add_issue(S, 101, ["Discussion", M], pos=5, title="disc-only")
    args = ["-g", "offis/team", "sort-board-issues", "-P", PRIO,
            "-r", "ToDo = due-date,priority", "-r", "Discussion = priority"]
    _, out = run(*args)
    assert _titles(S, "ToDo") == ["x", "t", "y"]                 # first rule wins
    assert _titles(S, "Discussion") == ["disc-only", "x", "y"]   # free issue merged around pinned ones
    assert "List 'Discussion' could not be fully sorted" in out
    assert '"y" (due' in out and "also in 'ToDo'" in out
    S.calls.clear()
    run(*args)
    assert not [c for c in S.calls if c[1].endswith("/reorder")]


def test_rule_option_validation(gl):
    S, run = gl
    rc, out = run("-g", "offis/team", "sort-board-issues", "-r", "ToDo", "-l", "ToDo", ok=False)
    assert rc != 0 and "either --rule or --label" in out
    rc, out = run("-g", "offis/team", "sort-board-issues", "-r", "ToDo = prio", ok=False)
    assert rc != 0 and "Unknown sort key" in out
    add_issue(S, 100, ["ToDo"], due=d(2), pos=2)
    add_issue(S, 100, ["ToDo"], due=d(1), pos=1)
    _, out = run("-g", "offis/team", "sort-board-issues", "-r", "ToDo = due-date", "-r", "* = due-date",
                 "-r", "ToDo = due-date")
    assert "used in several rules" in out
    assert "Sort plan" in out and "- ToDo by due-date" in out and "- Waiting by due-date" in out


def test_verbose_output_shows_titles(gl):
    S, run = gl
    add_issue(S, 100, ["ToDo"], due=d(5), pos=1, title="Write the report")
    add_issue(S, 100, ["ToDo"], due=d(1), pos=2, title="Book the room")
    _, out = run("-v", "-g", "offis/team", "sort-board-issues", "-l", "ToDo")
    assert "Current order of 'ToDo'" in out and "Target order of 'ToDo'" in out
    assert "Write the report" in out and "Book the room" in out
    assert 'move offis/team/alpha#2 "Book the room" (due' in out


@pytest.mark.parametrize("seed", range(20))
def test_rules_random_shared_issues(gl, seed):
    S, run = gl
    rnd = random.Random(1000 + seed)
    cols = ["A", "B", "C", "D"]
    S.labels[("group", 10)] |= set(cols) | {H, M, L}
    S.boards[("group", 10)] = [{"id": 7, "name": "R", "lists": cols}]
    used = set()
    for k in range(rnd.randint(4, 30)):
        labels = rnd.sample(cols, rnd.choice([1, 1, 1, 2, 2, 3]))
        prio = rnd.choice([[H], [M], [L], []])
        pos = rnd.randint(-3000, 3000)
        while pos in used:
            pos += 1
        used.add(pos)
        add_issue(S, rnd.choice([100, 101, 102]), labels + prio,
                  due=rnd.choice([None, d(rnd.randint(-3, 8))]), pos=pos)
    order = cols[:]
    rnd.shuffle(order)
    keys = [rnd.choice([("due-date",), ("priority",), ("due-date", "priority"), ("priority", "due-date")])
            for _ in order]
    desc = seed % 4 == 3
    args = ["-g", "offis", "sort-board-issues", "-n", "R", "-P", PRIO] + (["--descending"] if desc else [])
    for c, k in zip(order, keys):
        args += ["-r", f"{c} = {','.join(k)}"]
    _, out = run(*args)
    for n, (c, k) in enumerate(zip(order, keys)):
        assert _sorted_like(S, c, k, desc, order[:n]), (c, out)
    S.calls.clear()
    run(*args)
    assert not [c for c in S.calls if c[1].endswith("/reorder")], "second run must not move anything"


# --------------------------------------------------------------------------- #
# overdue / due-within
# --------------------------------------------------------------------------- #

def _overdue_setup(S):
    S.labels[("group", 10)] |= {H, M, L, "Doing"}
    data = [  # (title, due, prio)
        ("future-low", d(2), L),
        ("overdue-low", d(-1), L),
        ("none-high", None, H),
        ("overdue-none", d(-5), None),
        ("today-high", d(0), H),       # due today = not overdue
        ("overdue-high", d(-2), H),
        ("future-med", d(10), M),
        ("overdue-med", d(-1), M),
    ]
    for k, (title, due, prio) in enumerate(data):
        add_issue(S, [100, 101][k % 2], ["ToDo"] + ([prio] if prio else []), due=due,
                  pos=(k + 1) * 10, title=title)


def test_sort_overdue_then_priority(gl):
    S, run = gl
    _overdue_setup(S)
    _, out = run("-g", "offis/team", "sort-board-issues", "-P", PRIO, "-r", "ToDo = overdue,priority")
    assert _titles(S) == ["overdue-high", "overdue-med", "overdue-low", "overdue-none",
                          "none-high", "today-high", "future-med", "future-low"]
    S.calls.clear()
    run("-g", "offis/team", "sort-board-issues", "-P", PRIO, "-r", "ToDo = overdue,priority")
    assert not [c for c in S.calls if c[1].endswith("/reorder")]


def test_sort_overdue_with_fixed_today(gl):
    S, run = gl
    _overdue_setup(S)
    # pretend it is 3 days earlier: only "overdue-none" (d-5) is overdue then
    fake_today = (TODAY - timedelta(days=3)).isoformat()
    run("-g", "offis/team", "sort-board-issues", "-P", PRIO, "-r", "ToDo = overdue,priority",
        extra_env={"ISQA_TODAY": fake_today})
    assert _titles(S)[0] == "overdue-none"
    assert _titles(S)[1:4] == ["none-high", "today-high", "overdue-high"]


def test_sort_due_within_then_priority(gl):
    S, run = gl
    _overdue_setup(S)
    run("-g", "offis/team", "sort-board-issues", "-P", PRIO, "-r", "ToDo = due-within:2,priority")
    # due within 2 days (incl. overdue and today): by priority; then the rest by priority
    # (same priority keeps the manual order: future-low was above overdue-low)
    assert _titles(S) == ["today-high", "overdue-high", "overdue-med", "future-low", "overdue-low",
                          "overdue-none", "none-high", "future-med"]


def test_overdue_keys_validation_and_debug_output(gl):
    S, run = gl
    rc, out = run("-g", "offis/team", "sort-board-issues", "-r", "ToDo = due-within:x", ok=False)
    assert rc != 0 and "Unknown sort key" in out and "due-within:7" in out
    rc, out = run("-g", "offis/team", "sort-board-issues", "-r", "ToDo = overdue,overdue", ok=False)
    assert rc != 0 and "must not repeat" in out
    _overdue_setup(S)
    _, out = run("-v", "-g", "offis/team", "sort-board-issues", "-P", PRIO, "-r", "ToDo = overdue,priority")
    assert "(overdue)" in out and "OVERDUE" in out
