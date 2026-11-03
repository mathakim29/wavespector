#!/usr/bin/env node

import { spawn } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import path from 'node:path';

const packageRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const viteBin = path.join(packageRoot, 'node_modules', 'vite', 'bin', 'vite.js');
const children = [];

function start(label, command, args, color) {
  const child = spawn(command, args, {
    cwd: packageRoot,
    stdio: ['inherit', 'pipe', 'pipe'],
    env: process.env
  });
  child.stdout.on('data', (data) => process.stdout.write(`${color}[${label}]\x1b[0m ${data}`));
  child.stderr.on('data', (data) => process.stderr.write(`${color}[${label}]\x1b[0m ${data}`));
  child.on('exit', (code) => {
    if (code && !stopping) process.exitCode = code;
  });
  children.push(child);
  return child;
}

let stopping = false;
start('web', process.execPath, [viteBin, '--host', '127.0.0.1'], '\x1b[35m');
start('api', 'uv', ['run', 'api.py'], '\x1b[36m');

function stop() {
  if (stopping) return;
  stopping = true;
  for (const child of children) child.kill('SIGTERM');
}

process.on('SIGINT', stop);
process.on('SIGTERM', stop);
