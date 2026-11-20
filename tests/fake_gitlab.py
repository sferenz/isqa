"""Minimal in-memory fake of the GitLab REST API (only what isqa uses).

Reorder semantics mirror GitLab's Gitlab::RelativePositioning::Mover:
  * positioning scope = all issues of the root group (across projects)
  * move_before_id = issue placed BEFORE the moved one (lhs)
  * move_after_id  = issue placed AFTER the moved one (rhs)
  * if the issue is already inside the range -> no-op
  * only lhs given -> position between lhs and MAX (not directly behind lhs!)
  * only rhs given -> position between MIN and rhs
"""

import json
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlparse

MAX_POS = 2**31 - 1
MIN_POS = -(2**31)


class Store:
    def __init__(self):
        self.groups = {}  # id -> dict(id, full_path, parent_id)
        self.projects = {}  # id -> dict(id, path_with_namespace, group_id)
        self.labels = {}  # ("group"|"project", id) -> set(names)
        self.boards = {}  # ("group"|"project", id) -> [dict(id, name, lists=[label])]
        self.issues = {}  # global id -> dict
        self.users = {}
        self.calls = []  # (method, path, body)
        self.lock = threading.Lock()

    # --- helpers ----------------------------------------------------------
    def root_group(self, group_id):
        g = self.groups[group_id]
        while g["parent_id"]:
            g = self.groups[g["parent_id"]]
        return g["id"]

    def descendant_groups(self, group_id):
        out = {group_id}
        changed = True
        while changed:
            changed = False
            for g in self.groups.values():
                if g["parent_id"] in out and g["id"] not in out:
                    out.add(g["id"])
                    changed = True
        return out

    def ancestor_groups(self, group_id):
        out = []
        while group_id:
            out.append(group_id)
            group_id = self.groups[group_id]["parent_id"]
        return out

    def resolve_group(self, ref):
        ref = unquote(ref)
        for g in self.groups.values():
            if str(g["id"]) == ref or g["full_path"] == ref:
                return g
        return None

    def resolve_project(self, ref):
        ref = unquote(ref)
        for p in self.projects.values():
            if str(p["id"]) == ref or p["path_with_namespace"] == ref:
                return p
        return None

    def issue_json(self, i, group_view=False):
        p = self.projects[i["project_id"]]
        d = {k: v for k, v in i.items() if k not in ("relative_position", "notes")}
        d["web_url"] = f"http://fake/{p['path_with_namespace']}/-/issues/{i['iid']}"
        d["references"] = {
            "short": f"#{i['iid']}",
            "relative": f"#{i['iid']}",
            "full": f"{p['path_with_namespace']}#{i['iid']}",
        }
        a = i.get("assignee_id")
        d["assignee"] = self.users[a] if a else None
        d["assignees"] = [self.users[a]] if a else []
        d.pop("assignee_id", None)
        return d

    def ordered(self, issues):
        # GitLab orders by relative_position ASC NULLS LAST, then id
        return sorted(
            issues,
            key=lambda i: (
                i["relative_position"] is None,
                i["relative_position"] or 0,
                i["id"],
            ),
        )

    def scope_issues(self, root_gid):
        return [
            i
            for i in self.issues.values()
            if self.root_group(self.projects[i["project_id"]]["group_id"]) == root_gid
        ]

    def reorder(self, issue, before_id, after_id):
        root = self.root_group(self.projects[issue["project_id"]]["group_id"])
        scope = [i for i in self.scope_issues(root) if i["id"] != issue["id"]]
        lhs = self.issues.get(before_id) if before_id else None
        rhs = self.issues.get(after_id) if after_id else None
        if lhs and rhs and (lhs["relative_position"] or 0) > (rhs["relative_position"] or 0):
            lhs, rhs = rhs, lhs
        pos = issue["relative_position"]
        if lhs is None and rhs is None:
            raise ValueError("not found")
        lpos = lhs["relative_position"] if lhs else None
        rpos = rhs["relative_position"] if rhs else None
        open_l = lpos is None
        open_r = rpos is None

        # cover? -> no-op
        if pos is not None:
            if open_l and open_r:
                return
            if open_r and lpos < pos:
                return
            if open_l and pos < rpos:
                return
            if not open_l and not open_r and lpos < pos < rpos:
                return

        if open_l and open_r:
            return
        if open_r:
            new = (lpos + MAX_POS) // 2
        elif open_l:
            new = (MIN_POS + rpos) // 2
        else:
            if rpos - lpos < 2:
                # make space: shift everything >= rpos to the right
                for i in scope:
                    if i["relative_position"] is not None and i["relative_position"] >= rpos:
                        i["relative_position"] += 1000
                rpos = rhs["relative_position"]
            new = (lpos + rpos) // 2
        issue["relative_position"] = new


STORE = Store()


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code, data, headers=None):
        body = json.dumps(data).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _body(self):
        n = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(n) if n else b""
        if not raw:
            return {}
        try:
            return json.loads(raw)
        except Exception:
            return {k: v[0] for k, v in parse_qs(raw.decode()).items()}

    def do_GET(self):
        self.route("GET")

    def do_PUT(self):
        self.route("PUT")

    def do_POST(self):
        self.route("POST")

    def route(self, method):
        if self.headers.get("PRIVATE-TOKEN") != "secret":
            return self._send(401, {"message": "401 Unauthorized"})
        u = urlparse(self.path)
        path = u.path[len("/api/v4"):]
        q = {k: v[0] for k, v in parse_qs(u.query).items()}
        body = self._body() if method in ("PUT", "POST") else {}
        S = STORE
        with S.lock:
            S.calls.append((method, path, body))
            try:
                return self._dispatch(method, path, q, body)
            except KeyError as e:
                return self._send(404, {"message": f"404 Not found {e}"})

    def _dispatch(self, method, path, q, body):
        S = STORE
        m = re.fullmatch(r"/user", path)
        if m:
            return self._send(200, {"id": 1, "username": "bot"})

        m = re.fullmatch(r"/users/(\d+)", path)
        if m:
            u = S.users[int(m[1])]
            return self._send(200, u)

        m = re.fullmatch(r"/(groups|projects)/([^/]+)(/.*)?", path)
        if not m:
            return self._send(404, {"message": "404 Not found"})
        kind, ref, rest = m[1], m[2], m[3] or ""
        obj = S.resolve_group(ref) if kind == "groups" else S.resolve_project(ref)
        if obj is None:
            return self._send(404, {"message": "404 Not found"})
        key = ("group" if kind == "groups" else "project", obj["id"])

        if rest == "" and method == "GET":
            return self._send(200, obj)

        # labels (including ancestor groups)
        m = re.fullmatch(r"/labels/(.+)", rest)
        if m and method == "GET":
            name = unquote(m[1])
            gid = obj["id"] if kind == "groups" else obj["group_id"]
            names = set()
            if kind == "projects":
                names |= S.labels.get(("project", obj["id"]), set())
            for g in S.ancestor_groups(gid):
                names |= S.labels.get(("group", g), set())
            if name in names:
                return self._send(200, {"id": hash(name) % 1000, "name": name})
            return self._send(404, {"message": "404 Label Not Found"})

        # boards
        if rest == "/boards" and method == "GET":
            boards = S.boards.get(key, [])
            return self._send(200, [{"id": b["id"], "name": b["name"]} for b in boards])
        m = re.fullmatch(r"/boards/(\d+)(/lists)?(/\d+)?", rest)
        if m and method == "GET":
            board = next((b for b in S.boards.get(key, []) if b["id"] == int(m[1])), None)
            if board is None:
                return self._send(404, {"message": "404 Board Not Found"})
            lists = [
                {"id": board["id"] * 100 + n, "label": {"name": lab}, "position": n}
                for n, lab in enumerate(board["lists"])
            ]
            if m[2] and m[3]:
                lid = int(m[3][1:])
                return self._send(200, next(l for l in lists if l["id"] == lid))
            if m[2]:
                return self._send(200, lists)
            return self._send(200, {"id": board["id"], "name": board["name"]})

        # issues list
        if rest == "/issues" and method == "GET":
            if kind == "groups":
                gids = S.descendant_groups(obj["id"])
                items = [
                    i for i in S.issues.values() if S.projects[i["project_id"]]["group_id"] in gids
                ]
            else:
                items = [i for i in S.issues.values() if i["project_id"] == obj["id"]]
            state = q.get("state", "all")
            if state != "all":
                items = [i for i in items if i["state"] == state]
            if q.get("labels"):
                want = q["labels"].split(",")
                items = [i for i in items if all(w in i["labels"] for w in want)]
            if q.get("order_by") == "relative_position":
                items = S.ordered(items)
                if q.get("sort") == "desc":
                    items = items[::-1]
            else:
                items = sorted(items, key=lambda i: i["id"], reverse=True)
            # simple pagination
            page = int(q.get("page", 1))
            per_page = int(q.get("per_page", 20))
            chunk = items[(page - 1) * per_page : page * per_page]
            headers = {}
            if page * per_page < len(items):
                nxt = self.path.split("?")[0] + "?" + "&".join(
                    f"{k}={v}" for k, v in {**q, "page": page + 1, "per_page": per_page}.items()
                )
                headers["Link"] = f'<http://{self.headers["Host"]}{nxt}>; rel="next"'
            headers["X-Total"] = str(len(items))
            return self._send(200, [S.issue_json(i) for i in chunk], headers)

        # issue comments (notes)
        m = re.fullmatch(r"/issues/(\d+)/notes", rest)
        if m and kind == "projects":
            issue = next(
                i for i in S.issues.values() if i["project_id"] == obj["id"] and i["iid"] == int(m[1])
            )
            notes = issue.setdefault("notes", [])
            if method == "GET":
                page = int(q.get("page", 1))
                per_page = int(q.get("per_page", 20))
                chunk = notes[(page - 1) * per_page: page * per_page]
                headers = {"X-Total": str(len(notes))}
                if page * per_page < len(notes):
                    nxt = self.path.split("?")[0] + "?" + "&".join(
                        f"{k}={v}" for k, v in {**q, "page": page + 1, "per_page": per_page}.items())
                    headers["Link"] = f'<http://{self.headers["Host"]}{nxt}>; rel="next"'
                return self._send(200, chunk, headers)
            if method == "POST":
                note = {"id": len(notes) + 1, "body": body.get("body", ""), "system": False}
                notes.append(note)
                return self._send(201, note)

        # project issue update / reorder
        m = re.fullmatch(r"/issues/(\d+)(/reorder)?", rest)
        if m and kind == "projects":
            issue = next(
                i for i in S.issues.values() if i["project_id"] == obj["id"] and i["iid"] == int(m[1])
            )
            if method == "GET":
                return self._send(200, S.issue_json(issue))
            if method == "PUT" and m[2]:
                b = body.get("move_before_id")
                a = body.get("move_after_id")
                S.reorder(issue, int(b) if b else None, int(a) if a else None)
                return self._send(200, S.issue_json(issue))
            if method == "PUT":
                labels = list(issue["labels"])
                for lab in str(body.get("remove_labels", "")).split(","):
                    if lab and lab in labels:
                        labels.remove(lab)
                for lab in str(body.get("add_labels", "")).split(","):
                    if lab and lab not in labels:
                        labels.append(lab)
                if "labels" in body:
                    labels = [l for l in str(body["labels"]).split(",") if l]
                issue["labels"] = labels
                if "due_date" in body:
                    issue["due_date"] = body["due_date"] or None
                return self._send(200, S.issue_json(issue))

        return self._send(404, {"message": f"404 not implemented {method} {path}"})


def start(port=0):
    srv = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    return srv
