# Munin frontend extensions

Extensions are typed widgets placed only in declared slots. They are lazy-loaded
behind feature flags and may request read-only data or propose a diff. They do
not receive credentials, session cookies as JavaScript values, mutation powers,
or a path around server authorization. An untrusted extension is disabled by
default and must pass isolated preview, typecheck, tests, and human review.
