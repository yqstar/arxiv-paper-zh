import assert from "node:assert/strict";
import { existsSync, mkdtempSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { spawnSync } from "node:child_process";
import test from "node:test";
import { fileURLToPath } from "node:url";

const CLI = fileURLToPath(new URL("../bin/arxiv-paper-zh.mjs", import.meta.url));

function run(args) {
  return spawnSync(process.execPath, [CLI, ...args], {
    encoding: "utf8",
  });
}

test("prints help and version", () => {
  const help = run(["--help"]);
  assert.equal(help.status, 0);
  assert.match(help.stdout, /Usage: arxiv-paper-zh/);

  const version = run(["--version"]);
  assert.equal(version.status, 0);
  assert.match(version.stdout, /^0\.1\.2\n$/);
});

test("installs all project targets without duplicating .agents", () => {
  const project = mkdtempSync(join(tmpdir(), "arxiv-paper-zh-test-"));
  const result = run(["install", "--project", project, "--all"]);

  assert.equal(result.status, 0, result.stderr);
  assert.equal(
    existsSync(join(project, ".agents", "skills", "arxiv-paper-zh", "SKILL.md")),
    true,
  );
  assert.equal(
    existsSync(join(project, ".claude", "skills", "arxiv-paper-zh", "SKILL.md")),
    true,
  );
  assert.equal((result.stdout.match(/^installed:/gm) ?? []).length, 2);
});

test("requires --force before replacing an installation", () => {
  const project = mkdtempSync(join(tmpdir(), "arxiv-paper-zh-test-"));
  const first = run(["--project", project, "--agents"]);
  assert.equal(first.status, 0, first.stderr);

  const destination = join(project, ".agents", "skills", "arxiv-paper-zh");
  writeFileSync(join(destination, "stale.txt"), "old\n");

  const second = run(["--project", project, "--agents"]);
  assert.equal(second.status, 1);
  assert.match(second.stderr, /destination already exists/);

  const forced = run(["--project", project, "--agents", "--force"]);
  assert.equal(forced.status, 0, forced.stderr);
  assert.equal(existsSync(join(destination, "stale.txt")), false);
  assert.equal(existsSync(join(destination, "SKILL.md")), true);
});
