#!/usr/bin/env node

import {
  cpSync,
  existsSync,
  lstatSync,
  mkdirSync,
  readFileSync,
  renameSync,
  rmSync,
} from "node:fs";
import { homedir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const SKILL_NAME = "arxiv-paper-zh";
const PACKAGE_ROOT = fileURLToPath(new URL("../", import.meta.url));
const SOURCE_DIR = join(PACKAGE_ROOT, "skills", SKILL_NAME);
const PACKAGE_JSON = JSON.parse(
  readFileSync(join(PACKAGE_ROOT, "package.json"), "utf8"),
);

function usage() {
  process.stdout.write(`Usage: arxiv-paper-zh [install] [options]

Install the ${SKILL_NAME} Agent Skill.

Options:
  --all           Install for Codex, Claude Code, and generic Agent Skills
  --codex         Install for Codex
  --claude        Install for Claude Code
  --agents        Install in the generic .agents/skills directory
  --project PATH  Install at project scope instead of user scope
  --force         Replace an existing installation
  -h, --help      Show this help
  -v, --version   Show the package version

With no target option, Codex and Claude Code are selected.
`);
}

function fail(message) {
  process.stderr.write(`error: ${message}\n`);
  process.exitCode = 1;
}

function pathExists(path) {
  try {
    lstatSync(path);
    return true;
  } catch (error) {
    if (error?.code === "ENOENT") {
      return false;
    }
    throw error;
  }
}

function parseArgs(argv) {
  const args = [...argv];
  if (args[0] === "install") {
    args.shift();
  }

  const options = {
    agents: false,
    claude: false,
    codex: false,
    force: false,
    project: null,
  };

  for (let index = 0; index < args.length; index += 1) {
    const arg = args[index];
    switch (arg) {
      case "--all":
        options.agents = true;
        options.claude = true;
        options.codex = true;
        break;
      case "--agents":
        options.agents = true;
        break;
      case "--claude":
        options.claude = true;
        break;
      case "--codex":
        options.codex = true;
        break;
      case "--force":
        options.force = true;
        break;
      case "--project":
        index += 1;
        if (index >= args.length) {
          throw new Error("--project requires a path");
        }
        options.project = resolve(args[index]);
        break;
      case "-h":
      case "--help":
        usage();
        return null;
      case "-v":
      case "--version":
        process.stdout.write(`${PACKAGE_JSON.version}\n`);
        return null;
      default:
        throw new Error(`unknown option: ${arg}`);
    }
  }

  if (!options.agents && !options.claude && !options.codex) {
    options.claude = true;
    options.codex = true;
  }
  return options;
}

function destinations(options) {
  const paths = [];
  if (options.project) {
    if (!existsSync(options.project)) {
      throw new Error(`project path does not exist: ${options.project}`);
    }
    if (options.codex || options.agents) {
      paths.push(join(options.project, ".agents", "skills", SKILL_NAME));
    }
    if (options.claude) {
      paths.push(join(options.project, ".claude", "skills", SKILL_NAME));
    }
  } else {
    const userHome = homedir();
    if (options.codex) {
      const codexRoot = process.env.CODEX_HOME
        ? resolve(process.env.CODEX_HOME)
        : join(userHome, ".codex");
      paths.push(join(codexRoot, "skills", SKILL_NAME));
    }
    if (options.claude) {
      paths.push(join(userHome, ".claude", "skills", SKILL_NAME));
    }
    if (options.agents) {
      paths.push(join(userHome, ".agents", "skills", SKILL_NAME));
    }
  }
  return [...new Set(paths)];
}

function install(destination, force) {
  const parent = dirname(destination);
  mkdirSync(parent, { recursive: true });

  if (pathExists(destination) && !force) {
    throw new Error(
      `destination already exists: ${destination} (use --force to replace it)`,
    );
  }

  const suffix = `${process.pid}-${Date.now()}`;
  const temporary = join(parent, `.${SKILL_NAME}.tmp-${suffix}`);
  const backup = join(parent, `.${SKILL_NAME}.backup-${suffix}`);
  let backedUp = false;

  try {
    cpSync(SOURCE_DIR, temporary, { recursive: true, errorOnExist: true });
    if (pathExists(destination)) {
      renameSync(destination, backup);
      backedUp = true;
    }
    renameSync(temporary, destination);
    if (backedUp) {
      rmSync(backup, { recursive: true, force: true });
    }
  } catch (error) {
    rmSync(temporary, { recursive: true, force: true });
    if (backedUp && !pathExists(destination)) {
      renameSync(backup, destination);
    }
    throw error;
  }

  process.stdout.write(`installed: ${destination}\n`);
}

try {
  if (!existsSync(join(SOURCE_DIR, "SKILL.md"))) {
    throw new Error(`packaged skill is missing: ${SOURCE_DIR}`);
  }
  const options = parseArgs(process.argv.slice(2));
  if (options) {
    const targets = destinations(options);
    if (!options.force) {
      const existing = targets.find(pathExists);
      if (existing) {
        throw new Error(
          `destination already exists: ${existing} (use --force to replace it)`,
        );
      }
    }
    for (const destination of targets) {
      install(destination, options.force);
    }
    process.stdout.write(
      "Installation complete. Start a new agent session if the skill is not detected immediately.\n",
    );
  }
} catch (error) {
  fail(error.message);
}
