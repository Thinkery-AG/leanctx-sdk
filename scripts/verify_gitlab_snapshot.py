"""Validate a bounded SDK snapshot on stdin; never emit source content."""
import hashlib
import json
import os
import sys


def main():
    raw = sys.stdin.buffer.read(1024 * 1024 + 1)
    if len(raw) > 1024 * 1024:
        raise ValueError('snapshot exceeds bound')
    value = json.loads(raw)
    if set(value) != {'schema_version', 'provider', 'resource', 'request', 'result', 'snapshot_digest'}:
        raise ValueError('snapshot envelope is invalid')
    request, result = value['request'], value['result']
    items = result['items']
    payload = {key: child for key, child in value.items() if key != 'snapshot_digest'}
    canonical = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(',', ':'), allow_nan=False).encode()
    digest = 'sha256:' + hashlib.sha256(canonical).hexdigest()
    checks = [value['schema_version'] == 1, value['provider'] == result['provider'] == 'gitlab',
              value['resource'] == result['resource_type'] == 'merge_requests',
              request['project'] == os.environ['LEANCTX_REFERENCE_GITLAB_PROJECT'], request['limit'] == 1,
              isinstance(items, list) and len(items) == 1 and isinstance(items[0], dict) and bool(items[0].get('id')),
              value['snapshot_digest'] == digest]
    if not all(checks):
        raise ValueError('snapshot checks failed')
    print(json.dumps({'accepted': True, 'snapshot_digest': digest, 'item_count': 1}))


if __name__ == '__main__':
    try:
        main()
    except Exception:
        print('{"accepted":false}')
        raise SystemExit(1)
