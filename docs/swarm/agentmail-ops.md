# Agent Mail Ops (template)

## Check server health

- `curl -fsS http://127.0.0.1:8765/health/readiness`
- `curl -fsS http://127.0.0.1:8765/health/liveness`

## Start the server

If you installed Agent Mail via its installer, you likely have:
- `am` shell alias (starts the server with the saved token)

Or start directly:
- `cd ~/dev/mcp_agent_mail && scripts/run_server_with_token.sh`

## Project identity rule (avoid fragmentation)

Always use the repo’s absolute path as `project_key` / `human_key`:
- `<REPO_ABS_PATH>` (use `pwd`)

## Taking over a stuck agent (resume identity)

If a coding session hangs but you want to keep the same identity:

1) Discover agent names:
- Read `resource://agents/<project-slug>` (or use the web UI project page)

2) In a new session, reuse the name:
- `macro_start_session(human_key="<REPO_ABS_PATH>", program="<program>", model="<model>", agent_name="<ExistingName>", task_description="takeover")`

3) Release stale reservations (if any):
- `release_file_reservations(project_key="<REPO_ABS_PATH>", agent_name="<ExistingName>")`

Important:
- Calling `macro_start_session` without `agent_name` generates a new identity each time.

## Human Overseer (broadcast override)

Use the UI composer to message any subset of agents (including “select all”):
- `http://127.0.0.1:8765/mail` → open project → **Human Overseer**

