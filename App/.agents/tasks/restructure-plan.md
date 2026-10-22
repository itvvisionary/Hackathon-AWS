# Implementation Plan — Finish the App/ + IaC restructuring, fix test wiring, resolve the merge

Work directly in the repository root (NO worktree). There is an IN-PROGRESS,
UNCOMMITTED merge (`git status` = "You have unmerged paths"; `.git/MERGE_HEAD`
present). The final step completes that merge with ONE commit. Do NOT push.

## Design decisions (made during exploration, grounded in the real tree)

All findings below were verified by reading the files and running the suite.

- **Installed tooling:** `App/.venv_test/Scripts/python.exe` is Python 3.12.7 with
  **pytest 9.1.1** and moto 5.2.4 (from `App/requirements-dev.txt`). pytest 9.x
  fully supports the `pythonpath` ini option (added in pytest 7.0), so the clean
  ini-based mechanism is available and is what this plan uses. No top-level
  `sys.path` shim file is needed.
- **Import style stays flat.** Runtime modules import each other flatly:
  `agent.py` does `import data`, `from checkpointer import DynamoDBSaver`,
  `from logging_setup import ...`; `app.py` does `import agent`, `import data`,
  `import whatsapp`; `checkpointer.py`/`data.py`/`whatsapp.py` do
  `from logging_setup import ...`. Tests do `import agent/app/data`,
  `from checkpointer import ...`, `from conftest import USERS_TABLE_NAME, ...`.
  We keep this style and make it work via sys.path, per the constraints — no
  module renames, no dotted-package conversion.
- **Single authoritative conftest lives at `App/conftest.py` (the rootdir).**
  Rationale, verified empirically: `agent.py` builds its LangGraph graph at
  import time and `DynamoDBSaver.__init__` reads `os.environ["CHECKPOINTER_TABLE_NAME"]`
  eagerly. pytest imports test modules (which do `import agent`) during
  collection. Only a conftest at the rootdir (or inside the tests package) has
  its module-level code executed BEFORE collection — a conftest sitting in
  `App/utils/` does not, so its `os.environ.setdefault(...)` env seeding would
  run too late and collection fails with `KeyError: 'CHECKPOINTER_TABLE_NAME'`
  (observed). Placing the fixtures conftest at `App/` root fixes this AND keeps
  `from conftest import ...` working once `App/` is on `sys.path`. We therefore
  MOVE `App/utils/conftest.py` -> `App/conftest.py` and keep exactly one
  conftest (no second copy anywhere) to avoid fixture/namespace conflicts.
- **pytest.ini mechanism:** `pythonpath = HackathonWhatsAppCode .` under
  `[pytest]`. `HackathonWhatsAppCode` makes the flat runtime imports resolve; `.`
  (the rootdir = `App/`) makes `from conftest import ...` resolve. `utils` does
  NOT need to be on the path for tests (no test imports from utils). Verified:
  with `App/conftest.py` + `pythonpath = HackathonWhatsAppCode .`, the full suite
  is **32 passed**.
- **conftest's own `sys.path.insert`:** change `sys.path.insert(0, os.path.dirname(__file__))`
  (which at `App/` root wrongly points at `App/`) to insert the
  `HackathonWhatsAppCode` dir, so the conftest is self-sufficient (fixtures that
  `import agent` / `import whatsapp` work even if someone imports conftest
  outside the pytest `pythonpath` context). This is belt-and-suspenders with the
  ini and harmless.
- **export_graph.py:** it is a standalone utility in `App/utils/`. Fix = inject
  `<App>/HackathonWhatsAppCode` onto `sys.path` before `import agent`, and write
  the two output files into `App/` (one level up from `utils/`), matching where
  the user keeps `agent_graph.mmd` / `agent_graph.png`. Verified the path math
  (`APP_DIR = dirname(dirname(__file__))`) resolves to `App/` and `import agent`
  succeeds from `utils/`.
- **Stale pyc caches are a real blocker.** `App/tests/__pycache__/` holds
  pytest-rewritten caches (`*.cpython-312-pytest-9.1.1.pyc`) created when the
  tests lived at `src/src/tests/`; pytest reuses them and reports the stale
  `src\src\tests\...` source path in tracebacks. These (and `App/.pytest_cache`)
  must be deleted before the verifying test run. They are gitignored, so this is
  disk-only cleanup.
- **src/ cleanup scope.** Everything under `src/` is junk EXCEPT the already-
  represented `src/src/pytest.ini` settings. `src/old/handler.py` and
  `src/old/whatsapp_lambda.py` are legacy reference samples, imported by nothing
  in `App/` (confirmed: `whatsapp.py` no longer imports `whatsapp_lambda`); they
  are NOT moved. `src/src/requirements-layer.txt` is superseded by
  `App/layer/requirements.txt`; not copied. `src/src/python/` is the vendored
  layer build; `function.zip`, `src.rar`, `*.log` are build artifacts.
- **Working-tree note for staging:** the 7 files under
  `App/HackathonWhatsAppCode/` show as `AM` in `git status` (staged by the merge,
  then further modified in the working tree — the user's manual edits). The final
  `git add` must re-stage the working-tree versions so those edits are included.
  `App/layers-zip/*.zip` are correctly untracked under the `*.zip` ignore rule;
  leave them alone.

## Verification command (used throughout)

Run from `App/`:
`.\.venv_test\Scripts\python.exe -m pytest`
(`pytest.ini` already supplies `-q`, `testpaths = tests`, `python_files = test_*.py`.)
Expected after the fixes: `32 passed`.

---

- [ ] 1. Move the single authoritative conftest to the rootdir and fix its sys.path insert.
      Move `App/utils/conftest.py` to `App/conftest.py` (git mv is fine, but the
      file is currently untracked so a plain filesystem move + later `git add`
      also works). In the moved file, change the line
      `sys.path.insert(0, os.path.dirname(__file__))` to insert the sibling
      runtime dir instead:
      `sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "HackathonWhatsAppCode"))`.
      Do NOT leave any `conftest.py` in `App/utils/` (exactly one conftest total).
      Keep all fixture definitions (USERS_TABLE_NAME, CHECKPOINTER_TABLE_NAME,
      CHAT_HISTORY_TABLE_NAME, MAPPING_TABLE_NAME, the `dynamodb` moto fixture,
      `scripted_llm`, `mock_requests_post`, `mock_socialmessaging`,
      `order_response`, autouse `_env`) unchanged.
      Files: `App/conftest.py` (new, moved), `App/utils/conftest.py` (removed)
      Verify: `Test-Path App\conftest.py` is True and `Test-Path App\utils\conftest.py`
      is False. (Full suite is verified in step 4 after the ini is updated.)

- [ ] 2. Update `App/pytest.ini` to add the pythonpath entries.
      Replace the contents so it reads exactly:
      ```
      [pytest]
      testpaths = tests
      python_files = test_*.py
      addopts = -q
      pythonpath = HackathonWhatsAppCode .
      ```
      This preserves every setting from the old `src/src/pytest.ini`
      (testpaths/python_files/addopts) and adds the sys.path mechanism. Do not
      create a pyproject `[tool.pytest.ini_options]`; a single `pytest.ini` is the
      authoritative config.
      Files: `App/pytest.ini`
      Verify: covered by step 4 (the suite must discover `tests/` and import both
      runtime modules and `conftest`).

- [ ] 3. Fix `App/utils/export_graph.py` imports and output paths.
      Before `import agent`, inject the runtime dir onto sys.path and compute the
      App dir. Replace the `import agent  # noqa: E402` region and the HERE/MMD/PNG
      path block so it reads (keep the existing env `setdefault` block and the
      `main()` body/except handling intact):
      - add at top (after `import os`): `import sys`
      - immediately before `import agent`:
        `HERE = os.path.dirname(os.path.abspath(__file__))`
        `APP_DIR = os.path.dirname(HERE)`
        `sys.path.insert(0, os.path.join(APP_DIR, "HackathonWhatsAppCode"))`
      - change the output paths to:
        `MMD_PATH = os.path.join(APP_DIR, "agent_graph.mmd")`
        `PNG_PATH = os.path.join(APP_DIR, "agent_graph.png")`
      - update the stale docstring line `Run from src/src:  python export_graph.py`
        to `Run from App/:  python utils/export_graph.py` (or equivalent accurate
        instruction).
      Files: `App/utils/export_graph.py`
      Verify: from `App/`, run
      `.\.venv_test\Scripts\python.exe utils\export_graph.py` and confirm it prints
      `wrote ...\App\agent_graph.mmd` and that `App/agent_graph.mmd` is refreshed
      (PNG render is best-effort and may be skipped offline — that is acceptable).

- [ ] 4. Clear stale caches and run the full offline test suite green.
      Delete `App/tests/__pycache__`, `App/.pytest_cache`, and any
      `App/__pycache__` / `App/HackathonWhatsAppCode/__pycache__` /
      `App/utils/__pycache__` left from the old tree (they are gitignored; disk
      cleanup only). Then run the suite.
      Files: none (cache cleanup + verification only)
      Verify: from `App/`, `.\.venv_test\Scripts\python.exe -m pytest` exits 0 and
      reports `32 passed` (1 LangChain deprecation warning is expected and benign).
      If anything fails, fix wiring before proceeding — do not continue to git
      finalize with a red suite.

- [ ] 5. Resolve the `.gitignore` both-added merge conflict by combining both lists.
      Write `.gitignore` (repo root) with the merged, de-duplicated content below,
      then `git add .gitignore` to mark it resolved. It must retain: all Terraform
      rules + the `!IaC/environments/**/terraform.tfvars` negation, all build-
      artifact rules, Python rules (deduped), all virtualenvs incl. `.venv_test/`,
      `.venv_verify/`, `env/`, and `*.log`, `python/`, `layers/`, plus OS/editor
      rules.
      ```
      # Python bytecode
      *.pyc
      *.pyo
      *.pyd
      *.py[cod]
      __pycache__/

      # Virtual environments
      .venv/
      .venv_test/
      .venv_verify/
      venv/
      env/

      # Pytest cache
      .pytest_cache/

      # Logs
      *.log
      crash.log
      crash.*.log

      # Build artifacts
      **/.build/
      build/
      App/dist/
      App/layer/dist/
      python/
      layers/
      *.zip
      *.rar

      # Terraform (IaC/) — .terraform.lock.hcl intentionally committed
      .terraform/
      *.tfstate
      *.tfstate.*
      *.tfplan
      tfplan
      override.tf
      override.tf.json
      *_override.tf
      *_override.tf.json
      .terraformrc
      terraform.rc
      *.auto.tfvars
      *.auto.tfvars.json
      terraform.tfvars
      !IaC/environments/**/terraform.tfvars

      # Env files
      .env
      .env.*

      # OS / editor
      .DS_Store
      Thumbs.db
      .idea/
      .vscode/
      ```
      Files: `.gitignore`
      Verify: `git status` no longer lists `.gitignore` under "Unmerged paths"; it
      appears as staged (`A`/`M`). `git check-ignore -v App/.venv_test` and
      `App/layers-zip/layer.zip` both report a match (stay ignored);
      `git check-ignore IaC/environments/dev/terraform.tfvars` reports NO match
      (the negation keeps it trackable).

- [ ] 6. Stage the new App/ layout and the user's working-tree edits.
      Add the untracked App artifacts and re-stage the modified runtime modules so
      the manual edits (the `AM` state) are included:
      `git add App/ .github/ README.md` (App/ pulls in the new `conftest.py`,
      updated `pytest.ini`, fixed `export_graph.py`, tests/, utils/, requirements,
      agent_graph.*, README, .agents/). Confirm nothing ignored sneaks in.
      Files: none (git index only)
      Verify: `git status --short` shows the 7 `App/HackathonWhatsAppCode/*` files
      as `A ` or `M ` (no stray `M` working-tree residue for them), and shows no
      `*.zip`/`*.rar`/`.venv_test` entries staged.

- [ ] 7. Delete the old `src/` tree from disk and from the index.
      Remove the entire `src/` directory: `git rm -r --cached src` (it is partly
      tracked from HEAD) and delete it from disk (`Remove-Item -Recurse -Force src`).
      This covers `src/src/` (incl. vendored `python/`, `function.zip`,
      `requirements-layer.txt`, the old pytest.ini/tests/modules/.agents) and
      `src/old/` (legacy `handler.py`, `whatsapp_lambda.py`, `src.rar`). Nothing
      valuable remains to migrate (settled in exploration).
      Files: delete `src/**`
      Verify: `Test-Path src` is False, and `git status` shows the `src/src/*`
      deletions staged (no longer under "Changes not staged").

- [ ] 8. Complete the in-progress merge with a single commit (NO push).
      With `.gitignore` resolved and everything staged, commit to finish the merge:
      `git commit -m "chore: restructure into App/ + IaC layout, fix test wiring, resolve gitignore, drop old src tree"`.
      Do not amend, do not force, do not push, do not alter git config, keep hooks.
      Files: none (commit only)
      Verify: `git status` reports a clean tree with no "unmerged paths" and no
      `.git/MERGE_HEAD` remaining (`Test-Path .git\MERGE_HEAD` is False);
      `git log --oneline -1` shows the new commit; re-running
      `.\.venv_test\Scripts\python.exe -m pytest` from `App/` still reports
      `32 passed`.

## Gaps / assumptions

- Assumed the `App/HackathonWhatsAppCode/*` working-tree edits (the `AM` state)
  are intentional and should be committed as-is; step 6 re-stages them. If a
  later reviewer finds those edits unrelated to this restructuring, flag rather
  than silently dropping them.
- The merge commit message follows the suggested wording. If the user prefers a
  different message, only the text changes; the steps are unaffected.
- PNG export in step 3 may be skipped offline (needs mermaid.ink or a local
  renderer); the `.mmd` write is the authoritative success signal.
