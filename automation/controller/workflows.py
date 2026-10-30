"""Generate the four signed job roles and their single native Actions graph."""

from pathlib import Path

ENGINE = "KianBahasadri/clusterfork"
WORKFLOW = "clusterfork-task.yml"
ROLE_FILES = {
    "review-select": "clusterfork-review-account.yml",
    "review": "clusterfork-review.yml",
    "coding-select": "clusterfork-coding-account.yml",
    "coding": "clusterfork-code.yml",
}


def entrypoint():
    return """name: Clusterfork task
run-name: "Clusterfork task · ${{ inputs.linear_identifier }} · ${{ inputs.dispatch_key }}"

on:
  workflow_dispatch:
    inputs:
      linear_issue_id:
        required: true
        type: string
      linear_identifier:
        required: true
        type: string
      linear_project_id:
        required: true
        type: string
      dispatch_key:
        required: true
        type: string

permissions:
  contents: read
  id-token: write

concurrency:
  group: clusterfork-task-${{ inputs.linear_issue_id }}
  cancel-in-progress: false

jobs:
  review-account:
    uses: ./.github/workflows/clusterfork-review-account.yml
    with:
      dispatch_key: ${{ inputs.dispatch_key }}
      worker_revision: ${{ github.sha }}
  review:
    needs: review-account
    if: needs.review-account.outputs.selected == 'true'
    uses: ./.github/workflows/clusterfork-review.yml
    with:
      dispatch_key: ${{ inputs.dispatch_key }}
      worker_revision: ${{ github.sha }}
  coding-account:
    needs: review
    if: needs.review.outputs.ready == 'true'
    uses: ./.github/workflows/clusterfork-coding-account.yml
    with:
      dispatch_key: ${{ inputs.dispatch_key }}
      worker_revision: ${{ github.sha }}
  code:
    needs: coding-account
    if: needs.coding-account.outputs.selected == 'true'
    uses: ./.github/workflows/clusterfork-code.yml
    with:
      dispatch_key: ${{ inputs.dispatch_key }}
      worker_revision: ${{ github.sha }}
"""


def role_workflow(role):
    selector = role.endswith("-select")
    review = role == "review"
    output = "selected" if selector else "ready" if review else ""
    outputs = ("    outputs:\n      " + output + ":\n        value: ${{ jobs.run.outputs." + output + " }}\n") if output else ""
    job_output = ("    outputs:\n      " + output + ": ${{ steps.execute.outputs." + output + " }}\n") if output else ""
    return """name: Clusterfork ROLE

on:
  workflow_call:
    inputs:
      dispatch_key:
        required: true
        type: string
      worker_revision:
        required: true
        type: string
OUTPUTS
permissions:
  contents: read
  id-token: write

jobs:
  run:
    runs-on: ubuntu-24.04
    timeout-minutes: TIMEOUT
JOB_OUTPUTS    steps:
      - name: Check out the approved runtime
        uses: actions/checkout@v4
        with:
          repository: KianBahasadri/clusterfork
          ref: ${{ inputs.worker_revision }}
          path: .clusterfork-runtime
          persist-credentials: false
      - uses: ./.clusterfork-runtime/.github/actions/setup-codex
      - uses: ./.clusterfork-runtime/.github/actions/setup-cursor
      - name: Authenticate this role and obtain scoped target checkout access
        id: target
        env:
          CLUSTERFORK_BROKER_URL: ${{ vars.CLUSTERFORK_BROKER_URL }}
          DISPATCH_KEY: ${{ inputs.dispatch_key }}
          WORKER_REVISION: ${{ inputs.worker_revision }}
        run: python3 "$GITHUB_WORKSPACE/.clusterfork-runtime/automation/controller_bootstrap.py"
      - name: Check out the authorized target context
        uses: actions/checkout@v4
        with:
          repository: ${{ steps.target.outputs.repository }}
          ref: ${{ steps.target.outputs.context_sha }}
          token: ${{ steps.target.outputs.token }}
          fetch-depth: 0
          path: .clusterfork-target
          persist-credentials: false
      - name: Execute the authorized phase
        id: execute
        working-directory: .clusterfork-target
        env:
          CLUSTERFORK_BROKER_URL: ${{ vars.CLUSTERFORK_BROKER_URL }}
          DISPATCH_KEY: ${{ inputs.dispatch_key }}
        run: python3 "$GITHUB_WORKSPACE/.clusterfork-runtime/automation/worker.py"SELECT
""".replace("ROLE", role).replace("OUTPUTS\n", outputs).replace("JOB_OUTPUTS", job_output).replace("TIMEOUT", "12" if selector or review else "360").replace("SELECT", " --select-account" if selector else "")


def render():
    return {WORKFLOW: entrypoint(), **{name: role_workflow(role) for role, name in ROLE_FILES.items()}}


if __name__ == "__main__":
    destination = Path(__file__).resolve().parents[2] / ".github/workflows"
    for name, source in render().items():
        (destination / name).write_text(source)
