#!/usr/bin/env zx
// Task runner for dspy-dotprompt. This replaces bash scripts: every shell
// command runs through zx backticks (`$`...``), so tasks read as a list
// of shell commands with real variables, pipes, and error handling —
// not as a pile of .sh files.
//
// Usage:
//   zx tasks.mjs <task>
// Tasks: setup | proto | test | run | check | clean
// Default task: check.

const ROOT = path.resolve(__dirname);
// The venv lives in the workspace-level .tools dir (shared, survives VM
// swaps), not inside this project: ~/workspace/.tools/dspy-dotprompt-venv.
const VENV = path.join(ROOT, '..', '.tools', 'dspy-dotprompt-venv');
const PY = path.join(VENV, 'bin', 'python');

const task = argv._[0] || 'check';

async function setup() {
  echo('== setup: venv + Python deps ==');
  await $`python3 -m venv ${VENV}`;
  await $`${PY} -m pip install -q dotpromptz protobuf grpcio-tools cryptography`;
  echo('venv ready: ' + VENV);
}

async function proto() {
  echo('== proto: compile proto/harness.proto (Editions 2024) ==');
  await within(async () => {
    cd(ROOT);
    await $`${PY} -m grpc_tools.protoc -Iproto --python_out=src/dspy_dotprompt proto/harness.proto`;
  });
  echo('generated src/dspy_dotprompt/harness_pb2.py');
}

async function test() {
  echo('== test: Python test suite ==');
  await within(async () => {
    cd(ROOT);
    await $`${PY} tests/run_tests.py`;
  });
}

async function run() {
  echo('== run: example harness (FakeLM, offline) ==');
  await within(async () => {
    cd(ROOT);
    await $`${PY} examples/run_qa.py`;
  });
}

async function clean() {
  echo('== clean: build artifacts ==');
  await $`rm -rf ${VENV} ${ROOT}/src/dspy_dotprompt/__pycache__ ${ROOT}/tests/__pycache__ ${ROOT}/.store`;
  echo('cleaned');
}

const TASKS = { setup, proto, test, run, clean };
if (task === 'check') {
  await proto();
  await test();
  await run();
  echo('== check: all green ==');
} else if (TASKS[task]) {
  await TASKS[task]();
} else {
  echo(`unknown task: ${task}`);
  echo('tasks: setup | proto | test | run | check | clean');
  process.exit(2);
}
