# Linear issue description images

`automation/common/images.py` discovers Markdown inline and reference images
and HTML `<img>` tags in issue descriptions. Code examples and escaped image
syntax are excluded. The full description stays intact; numbered visual inputs
follow description order, with an explicit map of image alt text and titles so
surrounding captions and references remain usable. Repeated references download
once per snapshot but attach at each occurrence. Comment images are outside this
feature's scope.

The private SQLite `image_snapshots` cache stores a validated snapshot per issue
revision. Intermediate broker operations verify the cached digest against the
admitted specification without downloading the binaries again. Phase startup,
review submission, publication authorization and publishing revalidate current
bytes. Queued and active issue revisions retain their cache; terminal snapshots
are pruned.

## Retrieval and supported inputs

The broker downloads and decodes images before admitting a review. It uses the
existing Linear authorization only for the exact `uploads.linear.app` host,
following [Linear's file storage authentication](https://linear.app/developers/file-storage-authentication).
Other public HTTPS images download without Linear credentials. Every redirect
is validated separately; DNS answers must all be public, and connections pin a
validated address while retaining TLS hostname verification. Environment proxies
are bypassed. Private networks, URL userinfo, HTTP, and ports other than 443 are
unsupported.

| Limit | Value |
| --- | --- |
| Image occurrences per description | 6 |
| Encoded file size per image | 5 MiB |
| Combined file sizes, including repeated visual inputs | 12 MiB |
| Decoded dimensions per image | 20 megapixels; 8192 pixels per side |
| Formats | PNG, JPEG, non-animated WebP; matching HTTP media type required |
| Download budget per description | 12 seconds; 3-second socket timeouts |
| Redirects per image | 3 |

SVG, GIF, animated images, data URLs, and corrupt files are rejected. The broker
uses pinned `markdown-it-py` and Pillow dependencies from
`automation/requirements.txt`; the runner's image handling uses the Python
standard library.

If any image is inaccessible, unsupported, oversized, or cannot be decoded, a
Linear readiness comment names its description-order number and explains the
failure. Partial image context never admits coding or claims a completed visual
review. Coding and publication stay held. Accessible replacements or smaller
still images resolve permanent errors; transient failures retry through the
existing repository queue and timer. Errors exclude source URLs, signed query
strings, remote exception messages, headers, and credentials.

## Review and coding context

Broker responses carry image bytes and an ordered manifest. The runner checks
the manifest and each file's SHA-256, then writes private, read-only image files
in its temporary directory, outside the repository. Readiness uses native
`codex exec --image` attachments with the existing network-isolated `read-only`+sandbox. Coding uses native app-server `localImage` inputs on its initial turn.
Linear credentials remain in the broker and are never added to prompts or logs.
Descriptions without images use the existing text flow without image downloads.

Readiness binds approval to the ordered manifest, content hashes, and existing
issue/repository binding. Image bytes are fetched again at admission, reviewer
start and result acceptance, account access, coding start, and publication.
Changed bytes at the same URL invalidate approval, as do description edits that
replace, remove, caption, or reorder images. Readiness heartbeats do not download
images or grant approval. Image context holds use the readiness admission retry
path and cannot trigger an automatic repair that bypasses a fresh review.
The runner verifies its exact input bytes against the
approved manifest before calling Codex. Durable review rows retain only the
manifest digest, without image data. The shared start-protocol hash includes
`linear-description-images-v1` and its limits; broker and workflow branches must
ship compatible sources before either phase runs.

## Validation

`tests/test_issue_images.py` covers parsing, multiple images, download failures,
limits, credentials, redirects, public destinations, native coding input, and
descriptions without images. `tests/test_readiness.py` covers native readiness
attachments, Linear feedback, and changed-image fencing through both stages.

The screenshot fixtures contain button requirements absent from their issue
text. To exercise the pinned CLI/model with an existing Codex login:

```bash
CLUSTERFORK_LIVE_IMAGES=1 python3 -m unittest discover -s tests -p test_issue_images_live.py -v
```

This opt-in check uses a temporary copy of the login cache and a disposable
static-page repository. It asserts that the read-only reviewer names the target
button text and the coding session implements it using the native attachments.
It makes no Linear/GitHub calls or publications and never modifies the source
login cache. Ordinary CI runs use mocks and skip model inference.
