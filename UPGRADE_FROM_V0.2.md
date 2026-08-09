# Upgrade from v0.2.x

v0.3 removes `contexts.yaml` as the runtime source of truth.

1. Install v0.3 in a new directory.
2. Start it once (`install.cmd`) to create `data/control-plane.db`.
3. Import your old YAML if needed:

```cmd
.venv\Scripts\aicp.exe migrate-contexts D:\old\config\contexts.yaml
```

4. Start `run-api.cmd`.
5. Manage Contexts at `http://127.0.0.1:8765/admin`.

The old YAML can be kept only as backup/export material.
