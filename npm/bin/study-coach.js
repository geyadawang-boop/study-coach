#!/usr/bin/env node
"use strict";

// 零依赖安装器：把 skill 文件复制进 Agent 技能目录，或就地跑验收。
// 只覆盖同名文件，不删除目标目录中的任何内容。

const fs = require("fs");
const os = require("os");
const path = require("path");
const { spawnSync } = require("child_process");

const pkg = require("../../package.json");
const ROOT = path.resolve(__dirname, "..", "..");
const DIRS = ["agents", "references", "scripts", "subjects", "evals", "知识库模板"];
const FILES = ["SKILL.md", "README.md", "checks.json"];
const SKIP = new Set(["__pycache__", ".index", "学习库", ".git", "node_modules"]);

function usage() {
  console.log(`study-coach ${pkg.version}

用法：
  study-coach install [--dest <目录>] [--force]   复制 skill 到技能目录
  study-coach doctor [--dest <目录>] [--kb <库>]  跑验收检查（默认查包本身）
  study-coach --version                           打印版本

install 默认目标：${path.join(os.homedir(), ".trae-cn", "skills", "study-coach")}
目标已有 SKILL.md 时需加 --force；install 不会删除任何文件。`);
}

function fail(message) {
  console.error(`study-coach: ${message}`);
  process.exit(1);
}

function parse(argv) {
  const [command = "help", ...rest] = argv;
  const opts = { command, dest: null, kb: null, force: false };
  for (let i = 0; i < rest.length; i += 1) {
    const arg = rest[i];
    if (arg === "--dest" || arg === "-d") opts.dest = rest[++i];
    else if (arg.startsWith("--dest=")) opts.dest = arg.slice("--dest=".length);
    else if (arg === "--kb") opts.kb = rest[++i];
    else if (arg.startsWith("--kb=")) opts.kb = arg.slice("--kb=".length);
    else if (arg === "--force" || arg === "-f") opts.force = true;
    else if (arg === "--help" || arg === "-h") opts.command = "help";
    else fail(`未知参数：${arg}（用 --help 查看用法）`);
  }
  return opts;
}

function copyTree(src, dest) {
  fs.cpSync(src, dest, {
    recursive: true,
    filter: (from) => {
      const name = path.basename(from);
      return !SKIP.has(name) && !name.endsWith(".pyc") && !name.endsWith(".pyo");
    },
  });
}

function install(opts) {
  const dest = path.resolve(
    opts.dest || path.join(os.homedir(), ".trae-cn", "skills", "study-coach")
  );
  if (dest === ROOT || dest.startsWith(ROOT + path.sep)) {
    fail(`目标目录不能是包本身或其子目录：${dest}`);
  }
  if (!opts.force && fs.existsSync(path.join(dest, "SKILL.md"))) {
    fail(`目标已有 SKILL.md：${dest}\n确认覆盖请加 --force（仍不会删除其他文件）`);
  }
  if (path.basename(dest) !== "study-coach") {
    console.warn("提示：目录名不是 study-coach，SKILL.md 的 name 校验与 Agent 加载都会对不上。");
  }
  fs.mkdirSync(dest, { recursive: true });
  for (const file of FILES) {
    fs.copyFileSync(path.join(ROOT, file), path.join(dest, file));
  }
  for (const dir of DIRS) {
    copyTree(path.join(ROOT, dir), path.join(dest, dir));
  }
  console.log(`已安装到 ${dest}`);
  console.log(`下一步：把该目录配置为 Agent 技能，或运行 python scripts/run_checks.py --skill-root .`);
}

function doctor(opts) {
  const root = opts.dest ? path.resolve(opts.dest) : ROOT;
  const script = path.join(root, "scripts", "run_checks.py");
  if (!fs.existsSync(script)) fail(`找不到 ${script}，先 install 或用 --dest 指向已安装目录`);
  const python = process.env.PYTHON || (process.platform === "win32" ? "python" : "python3");
  const args = [script, "--skill-root", root];
  args.push("--kb", path.resolve(opts.kb || path.join(root, "知识库模板")));
  const result = spawnSync(python, args, { stdio: "inherit" });
  if (result.error) {
    console.error(`study-coach: 找不到 Python（${python}）。本 Skill 的核心脚本需要 Python 3.9+。`);
    if (result.error.code === "ENOENT") {
      console.error("装好 Python 后重试，或用 PYTHON 环境变量指定解释器路径。");
    }
    process.exit(2);
  }
  process.exit(result.status === null ? 2 : result.status);
}

const opts = parse(process.argv.slice(2));
switch (opts.command) {
  case "install":
    install(opts);
    break;
  case "doctor":
    doctor(opts);
    break;
  case "version":
  case "--version":
  case "-v":
    console.log(pkg.version);
    break;
  default:
    usage();
}
