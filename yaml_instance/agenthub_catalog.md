# AgentHub demo catalog

The six entries in `agenthub_*_metadata.json` are ordinary Registry metadata. Each
`runtime_ref` resolves through `agenthub_manifest.json` to a complete, single-Agent
workflow. The workflow receives the user's task directly; no upstream team output is
required. The registration command is explicit and uses the separately configured
AgentHub embedding endpoint:

```bash
python -m server.services.agenthub.demo_catalog
```

It prints the actual `agent_id` and version for each runtime reference. Repeating
the command with unchanged fixtures returns the same mapping. A conflicting
existing identity stops registration rather than creating a duplicate.

Research can gather public evidence when its configured web tools are available.
Code, Data, Document, Planning, and Review work from material supplied in the
task and have no external tools. Code and Review overlap on code quality, while
Code explains or debugs behavior and Review assesses a supplied work product.
Research and Document both compare information; Research can seek public sources,
while Document stays within supplied text. Planning and Review both assess a
design, but Planning orders future work and Review checks the current artifact.
Data may analyze structured values quoted in a document, while Document extracts
and compares the document's claims. These boundaries are routing inputs, not
guarantees of task quality or live provider behavior.
