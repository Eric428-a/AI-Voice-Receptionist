/**
 * Test runner: compiles src + tests with tsc, then runs them with Node's
 * built-in test runner (node:test) using jsdom for the DOM. Usage: npm test
 */
import { spawnSync } from 'node:child_process';
import { readdirSync, rmSync } from 'node:fs';
import { createRequire } from 'node:module';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const root = path.dirname(fileURLToPath(import.meta.url));
const outDir = path.join(root, '.test-build');
rmSync(outDir, { recursive: true, force: true });

const tsc = createRequire(import.meta.url).resolve('typescript/bin/tsc');
const compile = spawnSync(process.execPath, [tsc, '-p', path.join(root, 'tsconfig.test.json')], { stdio: 'inherit' });
if (compile.status !== 0) process.exit(compile.status ?? 1);

const testDir = path.join(outDir, 'tests');
const files = readdirSync(testDir)
  .filter((name) => name.endsWith('.test.js'))
  .sort()
  .map((name) => path.join(testDir, name));

const [major, minor] = process.versions.node.split('.').map(Number);
const args = ['--test'];
if (major >= 22 || (major === 20 && minor >= 14)) args.push('--test-force-exit');
const run = spawnSync(process.execPath, [...args, ...files], { stdio: 'inherit' });
process.exit(run.status ?? 1);
