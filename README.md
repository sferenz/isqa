## 📢 About

![PyPI Version](https://img.shields.io/pypi/v/isqa?style=flat)
![PyPI License](https://img.shields.io/pypi/l/isqa?style=flat) ![PyPI - Python Version](https://img.shields.io/pypi/pyversions/isqa?style=flat)

`isqa` is a CLI utility designed to help you manage issues of a GitLab repository.

> This CLI utility could be used for quality assurance of GitLab issues. Name `isqa` is comes from merging the term `issues` and `quality assurance `.

The intended use is to use `cron` or a similar approach to run the CLI in the background with a time interval. For insstance, a version running in th docker can use the following docker job scheduler: [mcuadros/ofelia](https://github.com/mcuadros/ofelia)

## 📦 Installation

* Install using pip
    ```
    pip install --upgrade isqa
    ```
* Check `configuration` section afterward
    

## 🧩 Development Setup

This guide walks through setting up the project for local development using `uv`.

1. Create a new virtual environment in a `.venv` directory and activates it.
    ```bash
    uv venv
    ```
1. Activate the environment (macOS/Linux):
   ```
   source .venv/bin/activate
   ```
1. Activate the environment (Windows):
    ```
    call .venv/Scripts/activate.bat
    ```

1.  **Install Package in Editable Mode**
    Installing the package in **editable mode** (`-e`) is the key to development. It links the `isqa` command in your environment directly to your source code.
    ```bash
    uv pip install -e .
    ```
    
## ⚙️ Configuration

This tool requires access to your GitLab repository (*scopes*: `api`, `read_api`). It uses a `.env` file to securely load your API credentials.

1.  Create a `.env` file in the root of the project
1.  Add your GitLab URL and a **Access Token** to the `.env` file.
    ```env
    GITLAB_URL=https://gitlab.your-company.com
    GITLAB_PROJECT_ID="Project_Name"
    GITLAB_TOKEN=Your_Access_Token_Here
    ```
    > **Note:** You can generate a Access Token (AT) from your GitLab repository under **Settings > Access Tokens**. Make sure to check upon creation the following *scopes*: `api`, `read_api`.

    > Never commit your `.env` file to version control.

1. To send notifications via email, a file `extra.env` with email configs is required.
    ```
    cp samples/sample.extra.env extra.env
    nano extra.env
    ```

## 🌟 Usage

Once installed, the `isqa` command is available directly in your terminal.

**NOTE:** In case you want to work with multiple repositories, then you need to create multiple `.env` files. Then each `.env` file could be explicitly passed/injected into CLI via parameter.

Because it's a typer application, you can explore all its commands and options by simply running:

* Help
    ```
    isqa --help
    ```

Here is a list of examples, which demonstrate the core features of the utility:

* List board issues
    ```
    isqa list-board-issues --board-name tasks
    ```
* List board issues (without visible labels on the board)
    ```
    isqa list-board-issues --without-labels
    ```
* Use a custom `test.env` file
    ```
    isqa --env-file test.env list-issues
    ```
* Run a check of a due date property of the issues and notify admin via email, if some issues are due
    ```
    isqa check-due-date --notify-admin
    ```
* Run a check of missing labels of the issues and notify admin via email, if some issues got no labels assigned
    ```
    isqa check-missing-label --notify-admin
    ```
* Run a check of missing assignee for the issues and notify admin via email, if some issues got no assignee
    ```
    isqa check-missing-assignee --notify-admin
    ```
* Migration issues from label `In Progress` to label `administration` when condition `due-date` met (issues have been due)
    ```
    isqa migrate-issues --from-label "In Progress" --to-label "administration" --check-condition due-date
    ```
* Sorting the issues on a board within the label `projects` (issues without due date are placed at the end; issues with the same due date keep their manual order)
    ```
    isqa sort-board-issues --label projects
    ```
* Sorting the issues on a board within the label `projects` in descending order.
    ```
    isqa sort-board-issues --label projects --descending
    ```

* Sorting several lists of a board at once
    ```
    isqa sort-board-issues --board-name Team --label ToDo --label "In Progress"
    ```

* Sorting by priority labels: by due date, and issues with the same due date by priority
    ```
    isqa sort-board-issues --label ToDo --sort-by due-date,priority \
        --priority-labels "11_Prio_High,12_Prio_Medium,13_Prio_Low"
    ```
    Sort keys (most important first): `due-date` (no due date at the end; `--descending`
    reverses only this key) and `priority` (order given by `--priority-labels` or
    `ISQA_PRIORITY_LABELS`, highest first; issues without priority label at the end;
    with several priority labels the highest one counts). `priority,due-date` sorts by
    priority first. Issues equal in all keys keep their manual order.

## 👥 Working on GitLab groups

All commands can work on a whole GitLab **group** (incl. all subgroups and
their projects) instead of a single project:

* via environment / `.env` file
    ```env
    GITLAB_URL=https://gitlab.your-company.com
    GITLAB_GROUP_ID="my-group/my-subgroup"
    GITLAB_TOKEN=Your_Access_Token_Here
    ```
* or via CLI parameter (overrides the environment)
    ```
    isqa --group my-group list-issues
    isqa --group my-group/my-subgroup sort-board-issues --board-name Team
    isqa --group my-group migrate-issues --from-label Waiting --to-label ToDo --check-condition due-date
    isqa --group my-group check-due-date --notify-assignee
    ```

Notes:

* `sort-board-issues` and `list-board-issues` use the **group boards**. Sorting
  works across projects (GitLab positions issues per top-level group).
* Labels are looked up in the group and its parent groups, so the labels used
  for migration/sorting should be **group labels**.
* Use a **group access token** (or a personal token of a bot user) with scope
  `api` and at least the role `Reporter`/`Planner` in the group.
* If both `GITLAB_GROUP_ID` and `GITLAB_PROJECT_ID` are set, the group wins.
  `--project` on the CLI switches back to project mode.

## 📧 Email notifications

The email templates are shipped with the package (override the folder with
`ISQA_EMAIL_TEMPLATES_DIR`). Settings (environment or `extra.env`):

```env
SMTP_SERVER=mail.your-company.com
SMTP_PORT=587               # STARTTLS; 465 = SSL
EMAIL_SENDER=isqa@your-company.com
EMAIL_ADMIN_TO_NOTIFY=admin@your-company.com
# optional - leave out for a relay that accepts mail without login
EMAIL_LOGIN=
EMAIL_PASSWORD=
```

If an email cannot be sent, the command exits with code `1`.

## 🧪 Tests

End-to-end tests run the CLI against an in-memory fake of the GitLab API
(`tests/fake_gitlab.py`) and a local SMTP server:

```
pip install -e . pytest aiosmtpd
pytest tests
```

## CLI UI

![alt text](https://raw.githubusercontent.com/vdmitriyev/isqa/main/docs/cli-ui.png)

## Note

In order to get emails notifications, each Gitlab user must set his/her email as `public` inside the personal Gitlab profile (this could be differ according to your Gitlab default configurations). Otherwise only configured in the `.env` file `admin` will receive notification emails.

## Documentation

[Documentation](https://vdmitriyev.github.io/isqa/)

## License

[MIT](https://github.com/vdmitriyev/isqa/blob/main/LICENSE)
