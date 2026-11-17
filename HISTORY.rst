=======
History
=======

0.7.0 (2026-11-16)
------------------

* New command ``comment-issues``: adds a comment once to every issue that
  matches a condition (``missing-assignee``, ``missing-label``, ``overdue``),
  e.g. a mention on issues without assignee. Repeats are prevented by an
  invisible marker in the comment (``--key``).

* New sort keys ``overdue`` (overdue issues first) and ``due-within:N``
  (issues due within N days first), combinable with ``priority`` and
  ``due-date``, e.g. ``--rule "ToDo;Doing = overdue,priority"``.
  Debug output marks overdue issues.

* ``sort-board-issues --rule '<lists> = <keys>'``: several lists with different
  sort keys in one call. Issues that are in several lists are sorted
  consistently: lists are processed in the given order, issues that belong to
  a list with higher precedence are not moved, contradictions are reported
  (before, sorting one list could silently break the order of another list).
* Debug output: issue titles, due dates and priorities in all move messages;
  ``--verbose`` prints current and target order of each list as a table.
* Wider console output in CI logs (no terminal); ``COLUMNS`` overrides.

* ``sort-board-issues``: labels given with ``--label`` that are not a list on
  the board (or do not exist) are skipped with a warning instead of failing;
  ``--strict`` restores the old behaviour. Fails only if none of them is a list.
* Plain-text email templates are now part of the package.

* Support GitLab groups: ``--group`` / ``GITLAB_GROUP_ID`` for all commands
  (group issues, group boards, group labels; changes are written via the
  issue's project).
* ``sort-board-issues``: new single-pass sort algorithm (no fixed 10 s pauses),
  stable for equal due dates, issues without due date always at the end,
  ``--label`` can be given multiple times, verification after sorting.
* ``migrate-issues``: only adds/removes the two labels (other label changes
  made in the meantime are kept); dry-run output fixed.
* Email templates are shipped inside the package.
* Email sending reports failures (exit code 1) instead of always printing
  "Email has been sent"; no empty admin email from ``check-missing-assignee``.
* ``GITLAB_TOKEN`` is checked correctly; ``GITLAB_PRIVATE_TOKEN`` accepted as alias.
* New ``--project`` CLI option.
* ``sort-board-issues``: ``--sort-by`` (``due-date``, ``priority`` or a
  combination) and ``--priority-labels`` / ``ISQA_PRIORITY_LABELS``.
* GitLab CI: sort rules per board list (``ISQA_SORT_RULES``).
* New command ``add-default-label``: adds a default label (e.g. ``ToDo``) to
  issues without any column label (explicit ``--column-label`` list or the
  label lists of a board). CI job ``label-issues-without-column``.

0.6.2 (2026-08-08)
------------------

* Fix shields/badges in the README.

0.6.1 (2026-08-08)
------------------

* Add shields/badges to the README.

0.6.0 (2026-08-08)
------------------

* Add package to PyPI

0.5.4 (2026-03-16)
------------------

* Fix the issue with the image URL on Test PyPI.

0.5.3 (2026-03-16)
------------------

* Fix the issue with missing email templates in .txt format

0.5.2 (2026-03-16)
------------------

* Fix text in CLI and documentation.

0.5.1 (2026-03-16)
------------------

* Fix dependencies issues
* Improve documentation
* Trusted publishing on Test PyPI.

0.5.0 (2026-03-16)
------------------

* First release on Github
