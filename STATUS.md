# STATUS — dspy-dotprompt

## Verified (2026-11-18, this VM)

- `zx tasks.mjs check` exit 0: proto compiles, 27/27 Python tests pass,
  example harness runs 2/2 and records to the Protobuf store.
- dotpromptz render verified by execution (smoke test + all program
  tests): `.prompt` frontmatter + Handlebars render to role/content
  messages; `{{role}}` and variable substitution work.
- Protobuf Editions: `proto/harness.proto` (`edition = "2024"`) compiles
  with grpc_tools.protoc (protobuf 7.36.2) and round-trips
  (RunRecord -> bytes -> RunRecord). Store queries verified by test.
- LevelDB: built from google/leveldb (CMake, Release) to
  `~/workspace/.tools/leveldb-build/libleveldb.so.1.23.0`. Key store
  verified: put/get/delete/list survive reopen; wrong passphrase raises
  ValueError; plaintext does not appear in the DB files; directory is
  0700.
- zx: installed via npm (zx binary present); backtick shell verified
  (`echo hello-from-zx`); `tasks.mjs check` runs proto + test + run.
- OllamaLM: verified it raises a clean ConnectionError when Ollama is
  not running (no hang, no traceback leak of a key).

## Not verified here

- `OpenAILM`: implemented, needs `OPENAI_API_KEY`; not called here
  (no key on this VM).
- `OllamaLM` against a live Ollama server: not run (no server here).
  The adapter posts to `http://localhost:11434/api/chat`.
- The ax Go binary (agentic games) is a separate delegated build in
  `~/workspace/ax-games/`; it is not part of this package.

## Layout

- `src/dspy_dotprompt/` — signature, lm, hooks, program, store,
  keystore, harness, harness_pb2 (generated from proto/harness.proto).
- `proto/harness.proto` — Editions 2024 schema (Dataset, Example,
  RunRecord, RunResult, HookEvent).
- `prompts/` — qa, summarize, extract_person (.prompt files).
- `tasks.mjs` — zx task runner (setup, proto, test, run, check, clean).
- `tests/` — 27 tests, plain asserts, `run_tests.py` runner.
- `examples/run_qa.py` — end-to-end QA run with FakeLM + a custom hook.

## Benchmarks (measured 2026-11-18, this VM, FakeLM offline)

| Component | Result |
|---|---|
| Signature parse | 0.005 ms |
| dotprompt render (qa.prompt) | 5.65 ms |
| Predict call | 2.58 ms |
| ChainOfThought call | 6.02 ms |
| ReAct call (1 tool) | 6.33 ms |
| Harness run (2 ex., 2/2) | 7.0 ms |
| Protobuf store write 100 runs | 2.0 ms (0.02 ms/run) |
| Protobuf store query 100 runs | 0.3 ms |
| Protobuf store get_run by id | 0.19 ms |
| LevelDB keystore 100 puts | 3.2 ms (0.03 ms/op) |
| LevelDB keystore 100 gets | 1.7 ms (0.02 ms/op) |
| ax binary run (Go) | 78 ms median over 5 runs (range 14–211 ms) |
| Test suite (27 tests) | 6.8 s |
| `zx tasks.mjs check` | 10.2 s |

Repo scale: 42 files, 1,527 lines of Python in 9 files. Charts rendered
from these numbers live in `docs/images/` (bench_latency.png,
bench_throughput.png, architecture.png). README is 2,857 words (full
walkthrough standard). A GitHub Actions workflow (`.github/workflows/check.yml`)
runs the same check on every push; Apache License 2.0 file added.

## Companion: ax binary

`~/workspace/ax-games/axgames` — a Go number-guessing game on google/ax
primitives (Setter + Guesser agents, ax Task/Model/Workspace types).
17.9 MB, statically linked, `go vet` clean, runs exit 0, deterministic,
guesser wins in 7 turns (secret 42). Go 1.27.1 installed at
`~/workspace/.tools/go/`. Limits: it drives the ax types locally with a
stub model; it does not run an ax server, a cluster, or a real model.
Full record: `~/workspace/ax-games/STATUS.md`. Not part of this repo.
