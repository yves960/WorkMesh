# Upgrade from v0.3

v0.3.1 removes duplicated local asset ownership.

If you want to preserve an existing v0.3 database, copy:

```text
old\data\control-plane.db
```

to the new v0.3.1 `data` directory, then run:

```cmd
.venv\Scripts\aicp.exe migrate-v03
```

Existing Contexts and Multica bindings are reused directly.

Old `local_roots` and local file/directory `knowledge_sources` remain in the SQLite file for rollback, but v0.3.1 ignores them.
