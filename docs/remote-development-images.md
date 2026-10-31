# Linear issue description images

`automation/common/images.py` finds inline/reference Markdown and HTML `<img>`
images in descriptions, excluding code/escaped syntax and comment images. Numbered
inputs preserve order, full description, alt text/titles and captions. Repeated
references download once per snapshot but attach at each occurrence.
Private SQLite `image_snapshots` caches validated issue revisions. Intermediate
operations check cached digest; phase/review/publication boundaries revalidate
bytes. Terminal snapshots are pruned; queued/active revisions remain cached.

## Retrieval and supported inputs

The broker downloads/decodes before review admission. Only exact `uploads.linear.app`
receives [Linear auth](https://linear.app/developers/file-storage-authentication);
other public HTTPS hosts receive none. Every redirect/DNS answer must be public;
connections pin validated addresses with TLS hostname checks and bypass environment
proxies. Reject private networks, userinfo, HTTP and non-443 ports.

| Limit | Value |
| --- | --- |
| Occurrences/description | 6 |
| Encoded image size | 5 MiB |
| Combined size, counting repeated inputs | 12 MiB |
| Decoded image | 20 megapixels; 8192 pixels/side |
| Formats | PNG, JPEG, non-animated WebP; matching HTTP media type |
| Description download budget | 12 seconds; 3-second socket timeouts |
| Redirects/image | 3 |

SVG/GIF/animation/data URLs/corrupt images fail. Broker dependencies pin
`markdown-it-py`/Pillow in `automation/requirements.txt`; runner handling uses stdlib.
Any failure holds coding/publication and names the numbered image in readiness
feedback; partial context cannot approve visual review. Replace inaccessible inputs
or use smaller stills; transient errors use queue retries. Errors exclude URLs,
signed queries, headers, remote messages and credentials.

## Review and coding context

Broker responses contain bytes and an ordered manifest. Runners verify SHA-256
and write private read-only temporary files outside the repo. Review uses native
`codex exec --image` in the network-isolated `read-only` sandbox; initial coding
uses app-server `localImage`. Linear credentials never enter prompts/logs.
Image-free descriptions need no download.

Approval binds ordered manifest/hashes plus issue/repository context. Re-fetch at
admission, reviewer start/result, account access, coding start and publication.
Changed bytes, captions, edits/removal/reordering invalidate approval. Heartbeats
neither download nor approve. Holds use readiness retries, never repair bypass.
Runner inputs must match approval before Codex starts; durable review rows store
only manifest digest. Start-protocol hash includes `linear-description-images-v1`
and limits; broker/workflow must ship compatible sources.

## Validation

`test_issue_images.py` covers parsing, limits, downloads, credentials, redirects,
public-address checks and native coding input; `test_readiness.py` covers native
attachments, feedback and changed-byte fencing. Screenshot fixtures specify button
text absent from issue prose. An opt-in pinned-model check with an existing login:

```bash
CLUSTERFORK_LIVE_IMAGES=1 python3 -m unittest discover -s tests -p test_issue_images_live.py -v
```

It uses a temporary auth copy/disposable static-page repo, verifies reviewer
recognition and coding implementation, and makes no Linear/GitHub calls or
publications. The source cache stays intact; normal CI skips this live test.
