# k3s status collector: SSH stderr must not contaminate structured stdout

## Symptom

A remote Kubernetes command can exit successfully and return valid JSON on stdout while the SSH session writes a login or locale warning to stderr.

If a collector merges both streams before JSON decoding, valid node data can be misclassified as an empty result.

Typical high-level symptoms are:

- inventory target resolution succeeds;
- pod text may still be partially visible;
- node count unexpectedly becomes zero;
- the cluster is reported as converging or unknown for the wrong reason.

## Rule

Structured remote output must be decoded from stdout only.

```text
remote command
  stdout -> structured parser
  stderr -> bounded diagnostics
```

Do not use `2>&1` before JSON parsing.

A command that exits with status 0 but returns invalid JSON is not equivalent to an empty Kubernetes object list. Treat it as an unknown collector result and surface a distinct contract error.

## Rollout health gate

A rollout gate should also preserve the semantic status returned by the cluster status collector.

- `healthy`: forward progress may continue only with the expected success exit code.
- `converging` / `blocked`: stop before mutation and retain node counts and issue keys in diagnostics.
- `unknown`: stop before mutation and preserve the reason as diagnostic evidence.

A nonzero exit code alone should not discard an otherwise valid structured status result.
