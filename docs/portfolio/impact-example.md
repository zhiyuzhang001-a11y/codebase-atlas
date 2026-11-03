# Trace impact in a small graph

The fixture models two different functions named `format`. Each has its own
caller and related test. Atlas distinguishes the functions by ID and limits how
far it follows their incoming call edges.

```text
test_text -> render_text -> text.format
test_json -> render_json -> json.format
```

Run from the repository root with Python 3.11–3.14. The example uses the
[existing graph fixture](../../tests/test_graph.py) and
[traversal implementation](../../src/codebase_atlas/graph.py). The data and source
locations are synthetic. No Provider process, index or agent configuration is
needed for this in-memory example.

```sh
PYTHONPATH=src python3 - <<'PY'
import runpy

fixture = runpy.run_path("tests/test_graph.py")
node, calls = fixture["node"], fixture["calls"]
evidence_graph = fixture["EvidenceGraph"]
graph = evidence_graph(
    [node("text.format", "format", 5), node("json.format", "format", 10),
     node("render_text", "render_text", 14), node("render_json", "render_json", 19),
     node("test_text", "test_render_text", 4), node("test_json", "test_render_json", 8)],
    [calls("render_text", "text.format"), calls("render_json", "json.format"),
     calls("test_text", "render_text"), calls("test_json", "render_json")],
)
for depth in (1, 2):
    hits = graph.impact(("text.format", "json.format"), direction="upstream", max_depth=depth)
    print(depth, ", ".join(f"{hit.node.id} ({hit.depth})" for hit in hits))
PY
```

Observed output lists each node ID with its hop count in parentheses.

```text
1 render_text (1), render_json (1)
2 render_text (1), render_json (1), test_text (2), test_json (2)
```

For the related identity, cycle and heuristic-edge regression checks, run:

```sh
PYTHONPATH=src python3 -m unittest discover -s tests -p 'test_graph.py' -v
```

This example exercises graph traversal at source revision `b47be4e`. To query a
real repository, follow the [project deployment workflow](../CODEX_DEPLOYMENT_RULES.md)
and inspect the returned freshness and partial-result fields.
