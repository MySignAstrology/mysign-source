"""
Which environment a synth is for, and what that changes.

prod is the stack that already exists, and every name in it is load-bearing:
the table, the secret and the log groups are RETAIN, so renaming one does not
move it — it strands the full one and creates an empty one beside it. That is
why prod is simply the top-level context in cdk.json, and its entry in the
`stages` map is empty.

Any other stage reads its own entry in `stages`, and from the top level only
the keys in SHARED. Everything else top-level is prod's (its certificate, its
WAF, its reserved concurrency) and a staging deploy must not inherit it by
omission. A null in the map cannot say "unset" — CDK drops nulls from
context — so absence has to.

  cdk deploy -c stage=staging
  cdk deploy -c stage=staging -c stagingCertificateArn=arn:...   # <stage><Key>

The <stage><Key> form exists because a bare `-c certificateArn=` is the prod
key, and a staging deploy must never be one typo away from prod's values.
"""
import sys

DEFAULT = "prod"
SHARED = {"lwaLayerVersion", "hostedZoneId", "forwardTo", "sesSandbox",
          "provisionedFromHourUtc", "provisionedToHourUtc"}


class Stage:
    def __init__(self, node):
        self._node = node
        self.name = node.try_get_context("stage") or DEFAULT
        stages = node.try_get_context("stages") or {}
        if self.name not in stages:
            sys.exit(f"\nUnknown stage {self.name!r}. Known: {', '.join(sorted(stages))} "
                     "(the `stages` map in deploy/cdk.json).\n")
        self._over = stages[self.name] or {}

    @property
    def prod(self):
        return self.name == DEFAULT

    def get(self, key, default=None):
        """-c <stage><Key>, then stages[<stage>][key], then top-level (prod, or SHARED)."""
        v = self._node.try_get_context(self.name + key[0].upper() + key[1:])
        if v is None:
            v = self._over.get(key)
        if v is None and (self.prod or key in SHARED):
            v = self._node.try_get_context(key)
        return default if v is None else v

    def flag(self, key, default=False):
        """-c arrives as a string; cdk.json as JSON."""
        v = self.get(key, default)
        return v is True or str(v).lower() in ("1", "true", "yes")

    def num(self, key, default=None):
        v = self.get(key)
        return default if v in (None, "", "null") else int(v)
