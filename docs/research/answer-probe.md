# Answer probe (key 1, re-test after the allowlist change)

Probed 2026-09-30 (retest, after the user said key 1 should now allow `answer` and `search`). Scratch and redacted raw output: `.tmp/answer-probe/` (`common.py`, `p1.py`, `p2.py`, `raw/t01_capital_en.json`, `raw/t02_key1_retry.json`, `raw/t03_key2.json`). No key value is stored anywhere.

## Result: still blocked, probing stopped as instructed

| call | key | body | status | response |
|---|---|---|---|---|
| t01 | key 1 (`api key.txt`) | `{"query":"What is the capital of Australia?","language":"en","stream":false,"markdown":true}` | 403 | `{"message":"Forbidden","statusCode":403}` |
| t02 | key 1, repeated a moment later | same | 403 | same |
| t03 | key 2 (`api key 2.txt`) | same | 403 | same (expected: key 2 is search-only) |
| control | key 1 | `GET /v2/search/web?q=test&market=en-gb` | 200 | key 1 still authenticates and searches |

Response header `content-type: application/json; charset=utf-8`, about 0.7 s. Identical to the pre-change behaviour recorded in `gaps.md` and `key2-probe.md`.

## Observations worth checking on the user side

1. `api key.txt` was last modified on 2026-09-29 22:51, i.e. before the allowlist change, and has not been touched since. If the change created or rotated the key, the file may still hold the old value. If the permission was changed on the same key, it has not taken effect yet (two calls, a few minutes apart) or the gate is account-level rather than key-level.
2. `disabled key.txt` is an EMPTY file (0 bytes). It cannot be used to test the disabled-key error. An empty Bearer token is not a disabled key, so nothing was sent with it.
3. The 403 is returned before body validation (as before), so it says nothing about the request shape.

## Consequence

None of the requested items could be measured: full response schema, citation structure, `position` unit (code points, UTF-16, bytes) and boundary placement, inline markers in `answer`, `source_id` base and mapping, repeated or same-position citations, `markdown` true vs false, `filter` forms, `language` values, error shapes (400/oversize), and the `stream` default. The GFM footnote algorithm therefore cannot be validated against real responses yet.

The working hypotheses in `gaps.md` B5 stand unchanged (not confirmed, not contradicted): `position` is an insertion index into `answer`, the text is marker-free, and `source_id` matches `sources[].id`. Keep `to_gfm` behind one helper (`_index_for`) so the offset unit can be switched once real data exists. Keep the 403 mapping to a clear "key not allowlisted for Answer, contact support@staan.ai" error.

## To resume

Once `api key.txt` (or another key) really returns non-403 on `/v2/answer`, re-run this task: `.tmp/answer-probe/common.py` already has a `call(tag, body, keyfile)` helper that saves redacted raw JSON. Put a non-empty key in `disabled key.txt` for the error test.
