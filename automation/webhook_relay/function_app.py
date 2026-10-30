"""Preserve Linear's existing URL. This app has no task or credential resources."""

import json
from pathlib import Path
from urllib.error import HTTPError,URLError
from urllib.request import Request,urlopen

import azure.functions as func

TARGET='https://clusterfork-controller-7604cac329.mexicocentral.cloudapp.azure.com/api/linear'
app=func.FunctionApp(http_auth_level=func.AuthLevel.ANONYMOUS)


@app.function_name(name='linear_relay')
@app.route(route='linear',methods=['POST'])
def linear_relay(req:func.HttpRequest)->func.HttpResponse:
    body=req.get_body()
    if len(body)>1024*1024:
        return func.HttpResponse('Request is too large',status_code=413)
    signature=req.headers.get('Linear-Signature','')
    if not signature:
        return func.HttpResponse('Missing signature',status_code=401)
    forwarded=Request(TARGET,data=body,headers={'Content-Type':'application/json','Linear-Signature':signature},method='POST')
    try:
        with urlopen(forwarded,timeout=20) as response:
            if response.status!=200:
                return func.HttpResponse('Controller has not accepted the event',status_code=503)
    except HTTPError as exc:
        status=exc.code if exc.code in {400,401,413} else 503
        exc.close()
        return func.HttpResponse('Controller has not accepted the event',status_code=status)
    except (URLError,OSError,TimeoutError):
        return func.HttpResponse('Controller has not accepted the event',status_code=503)
    return func.HttpResponse('{"status":"accepted"}',status_code=200,mimetype='application/json')


@app.function_name(name='health')
@app.route(route='health',methods=['GET'])
def health(req:func.HttpRequest)->func.HttpResponse:
    path=Path(__file__).parent/'deployment.json'
    metadata=json.loads(path.read_text()) if path.exists() else {}
    return func.HttpResponse(json.dumps({'mode':'webhook-relay','deployment':metadata,'target':TARGET}),
                             status_code=200,mimetype='application/json')
