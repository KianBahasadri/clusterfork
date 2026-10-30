"""Adapt the existing instruction resource protocol to read-only task views."""

import json
from uuid import UUID

from .model import Rejected
from steering import Steering
from worker_core import WorkerError


class TaskRows:
    def __init__(self,controller,registry,kind):
        self.controller,self.registry,self.kind=controller,registry,kind

    def get(self,key):
        try:
            view=self.controller.for_attempt(key)
        except Rejected:
            raise WorkerError("Unknown task attempt",404) from None
        if view.attempt["attempt_id"]!=key:
            raise WorkerError("Task attempt was superseded",404)
        route=self.registry.routes.get(view.project_id)
        repository=route.repository if route and route.repository.lower()==view.repository else view.repository
        receipt={"RowKey":key,"issue_id":view.issue_id,"identifier":view.identifier,
                 "project_id":view.project_id,"repository":repository}
        if self.kind=="receipt":
            return receipt,None
        session=view.session("coding")
        data=session["data"] if session else {}
        running=bool(session and not session["finished"] and not view.attempt["stopped"])
        row={**data,"RowKey":key,"owner":f"{view.attempt['run_id']}:{view.attempt['run_attempt']}",
             "status":"running" if running else "completed", "stage":view.attempt["stage"],
             "lease_until":data.get("heartbeat_until",0),
             "attempt_id":str(UUID(hex=key[:32],version=4)),
             "steering_started_at":data.get("steering_started_at",view.attempt["started_at"])}
        return row,None

    def list(self):
        rows=[]
        for view in self.controller.tasks():
            if view.attempt:
                rows.append(self.get(view.attempt["attempt_id"])[0])
        return rows


class TaskAdmission:
    def __init__(self,controller):
        self.controller=controller

    def assert_worker(self,repository,key,owner):
        try:
            view=self.controller.for_attempt(key)
        except Rejected:
            raise WorkerError("Task admission is unavailable",403) from None
        if (not view.active or view.phase=="held" or view.repository!=repository.lower()
                or view.attempt["attempt_id"]!=key
                or owner!=f"{view.attempt['run_id']}:{view.attempt['run_attempt']}"):
            raise WorkerError("Instruction target no longer owns repository admission",403)


class Instructions:
    def __init__(self,controller,registry,table,linear,*,clock):
        self.receipts=TaskRows(controller,registry,"receipt")
        self.runs=TaskRows(controller,registry,"run")
        self.resource=Steering(registry,self.receipts,self.runs,table,linear,TaskAdmission(controller),clock)

    def handle(self,view,action,data):
        key=view.attempt["attempt_id"]
        receipt,_=self.receipts.get(key)
        run,_=self.runs.get(key)
        if action=="instructions":
            return self.resource.poll(receipt,run)
        if action=="instruction_result":
            return self.resource.result(receipt,run,data)
        raise Rejected("Unsupported instruction transport operation")

    def comment(self,key):
        self.resource.comment(key)

    def reconcile(self):
        self.resource.reconcile()
