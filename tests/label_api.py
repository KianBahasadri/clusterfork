"""Stateful Linear label transport for reporting tests; no lifecycle decisions."""

import copy

from common.reporting import AUTO_REPAIR_LABEL, EXECUTION_STATUS_LABELS


class LabelAPI:
    def __init__(self):
        self.labels = {name: f'label-{index}' for index, name in
                       enumerate(sorted(EXECUTION_STATUS_LABELS | {AUTO_REPAIR_LABEL}))}
        self.issues = {}
        self.mutations = []
        self.snapshots = []

    def add_issue(self, issue_id, labels=()):
        nodes = []
        for label in labels:
            if isinstance(label, str):
                identifier = self.labels.setdefault(label, f'label-{len(self.labels)}')
                label = {'id': identifier, 'name': label}
            nodes.append(copy.deepcopy(label))
        self.issues[issue_id] = {
            'team': {'id': 'team'}, 'assignee': {'id': 'human'},
            'project': {'id': 'project', 'labels': {'nodes': [{'name': 'clusterfork-symphony'}]}},
            'labels': {'nodes': nodes, 'pageInfo': {'hasNextPage': False}},
        }

    def names(self, issue_id):
        return [label['name'] for label in self.issues[issue_id]['labels']['nodes']]

    def query(self, query, variables):
        if 'issueLabels(' in query:
            identifier = self.labels.get(variables['name'])
            return {'issueLabels': {'nodes': [{'id': identifier}] if identifier else [],
                                    'pageInfo': {'hasNextPage': False}}}
        if 'issueLabelCreate(' in query:
            self.labels[variables['name']] = variables['id']
            return {'issueLabelCreate': {'success': True}}
        if 'issueAddLabel(' in query or 'issueRemoveLabel(' in query:
            issue_id, identifier = variables['id'], variables['label']
            nodes = self.issues[issue_id]['labels']['nodes']
            if 'issueAddLabel(' in query:
                operation = 'issueAddLabel'
                name = next(name for name, value in self.labels.items() if value == identifier)
                if not any(label['id'] == identifier for label in nodes):
                    nodes.append({'id': identifier, 'name': name})
            else:
                operation = 'issueRemoveLabel'
                name = next(label['name'] for label in nodes if label['id'] == identifier)
                nodes[:] = [label for label in nodes if label['id'] != identifier]
            self.mutations.append((operation, issue_id, name))
            self.snapshots.append((issue_id, self.names(issue_id)))
            return {operation: {'success': True}}
        if 'issue(id:' in query:
            return {'issue': copy.deepcopy(self.issues[variables['id']])}
        raise AssertionError(f'Unexpected label operation: {query}')
