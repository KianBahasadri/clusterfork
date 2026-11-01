"""Two signed phase jobs; selection and execution stay inside each trusted process."""

from pathlib import Path

ENGINE='KianBahasadri/clusterfork'
WORKFLOW='clusterfork-task.yml'
ROLE_FILES={'review':'clusterfork-review.yml','coding':'clusterfork-code.yml'}


def entrypoint():
    return '''name: Clusterfork task
run-name: "Clusterfork task · ${{ inputs.linear_identifier }} · ${{ inputs.phase }} · ${{ inputs.dispatch_key }}"

on:
  workflow_dispatch:
    inputs:
      phase:
        required: true
        type: choice
        options: [review, coding]
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
  group: clusterfork-task-${{ inputs.linear_issue_id }}-${{ inputs.phase }}
  cancel-in-progress: false

jobs:
  review:
    if: inputs.phase == 'review'
    uses: ./.github/workflows/clusterfork-review.yml
    with:
      dispatch_key: ${{ inputs.dispatch_key }}
      worker_revision: ${{ github.sha }}
  code:
    if: inputs.phase == 'coding'
    uses: ./.github/workflows/clusterfork-code.yml
    with:
      dispatch_key: ${{ inputs.dispatch_key }}
      worker_revision: ${{ github.sha }}
'''


def role_workflow(role):
    return '''name: Clusterfork ROLE

on:
  workflow_call:
    inputs:
      dispatch_key:
        required: true
        type: string
      worker_revision:
        required: true
        type: string

permissions:
  contents: read
  id-token: write

jobs:
  run:
    runs-on: ubuntu-24.04
    timeout-minutes: TIMEOUT
    steps:
      - name: Check out the approved runtime
        uses: actions/checkout@v4
        with:
          repository: KianBahasadri/clusterfork
          ref: ${{ inputs.worker_revision }}
          path: .clusterfork-runtime
          persist-credentials: false
      - uses: ./.clusterfork-runtime/.github/actions/setup-codex
      - uses: ./.clusterfork-runtime/.github/actions/setup-cursor
      - name: Select account, check out context and execute the authorized phase
        id: execute
        env:
          CLUSTERFORK_BROKER_URL: ${{ vars.CLUSTERFORK_BROKER_URL }}
          DISPATCH_KEY: ${{ inputs.dispatch_key }}
          WORKER_REVISION: ${{ inputs.worker_revision }}
        run: python3 "$GITHUB_WORKSPACE/.clusterfork-runtime/automation/worker.py"
'''.replace('ROLE',role).replace('TIMEOUT','20' if role=='review' else '360')


def render():
    return {WORKFLOW:entrypoint(),**{name:role_workflow(role) for role,name in ROLE_FILES.items()}}


if __name__=='__main__':
    destination=Path(__file__).resolve().parents[2]/'.github/workflows'
    for name,source in render().items():
        (destination/name).write_text(source)
