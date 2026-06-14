const { app } = require('electron');
const path = require('path');
const fs = require('fs');
const { spawn } = require('child_process');

function resolvePython() {
  const candidates = [];
  if (app.isPackaged) {
    candidates.push(path.join(process.resourcesPath, 'venv311', 'Scripts', 'python.exe'));
    candidates.push(path.join(process.resourcesPath, 'venv311', 'bin', 'python'));
  }
  candidates.push(path.join(__dirname, 'venv311', 'Scripts', 'python.exe'));
  candidates.push(path.join(__dirname, 'venv311', 'bin', 'python'));
  for (const c of candidates) { try { if (fs.existsSync(c)) return c; } catch(e){} }
  return null;
}

function resolvePyDir() {
  const candidates = [];
  if (app.isPackaged) candidates.push(path.join(process.resourcesPath, 'python'));
  candidates.push(path.join(__dirname, 'python'));
  for (const c of candidates) { try { if (fs.existsSync(c)) return c; } catch(e){} }
  return null;
}

function runScript(scriptName, args = [], { onStdout, onStderr, timeoutMs } = {}) {
  return new Promise((resolve) => {
    const py = resolvePython();
    const dir = resolvePyDir();
    if (!py) return resolve({ ok: false, error: 'python.exe non trovato in venv311' });
    if (!dir) return resolve({ ok: false, error: 'cartella python/ non trovata' });
    const scriptPath = path.join(dir, scriptName);
    if (!fs.existsSync(scriptPath)) return resolve({ ok: false, error: 'script mancante: ' + scriptName });

    const env = Object.assign({}, process.env, {
      PYTHONIOENCODING: 'utf-8',
      PYTHONUNBUFFERED: '1'
    });
    let proc;
    try {
      proc = spawn(py, [scriptPath, ...args], { windowsHide: true, env });
    } catch (e) { return resolve({ ok: false, error: 'spawn: ' + e.message }); }

    let stdout = '', stderr = '';
    let timer = null;
    if (timeoutMs) timer = setTimeout(() => { try { proc.kill(); } catch(e){} }, timeoutMs);

    proc.stdout.on('data', d => {
      const s = d.toString();
      stdout += s;
      if (onStdout) { for (const line of s.split(/\r?\n/)) if (line) onStdout(line); }
    });
    proc.stderr.on('data', d => {
      const s = d.toString();
      stderr += s;
      if (onStderr) onStderr(s);
    });
    proc.on('error', e => { if (timer) clearTimeout(timer); resolve({ ok: false, error: e.message, stderr }); });
    proc.on('close', code => {
      if (timer) clearTimeout(timer);
      resolve({ ok: code === 0, code, stdout, stderr });
    });
  });
}

function spawnWorker(scriptName, args = []) {
  const py = resolvePython();
  const dir = resolvePyDir();
  if (!py || !dir) return null;
  const scriptPath = path.join(dir, scriptName);
  if (!fs.existsSync(scriptPath)) return null;
  const env = Object.assign({}, process.env, { PYTHONIOENCODING: 'utf-8', PYTHONUNBUFFERED: '1' });
  try {
    return spawn(py, [scriptPath, ...args], { windowsHide: true, env });
  } catch (e) { return null; }
}

async function venvCheck() {
  const py = resolvePython();
  if (!py) return { ok: false, python: null, error: 'python.exe non trovato. Attesa: venv311\\Scripts\\python.exe' };
  const r = await runScript('bootstrap.py', ['--check'], { timeoutMs: 60000 });
  if (!r.ok) return { ok: false, python: py, error: r.error || r.stderr || ('exit ' + r.code), stdout: r.stdout };
  try {
    const lastJsonLine = r.stdout.trim().split(/\r?\n/).reverse().find(l => l.startsWith('{'));
    const info = JSON.parse(lastJsonLine);
    return { ok: true, python: py, info };
  } catch (e) {
    return { ok: false, python: py, error: 'parse bootstrap output: ' + e.message, stdout: r.stdout };
  }
}

// Tenta l'installazione silenziosa di pacchetti opzionali (mutagen, pyacoustid, ecc.)
// Ritorna { ok, installed:[], failed:[], stdout, stderr }
async function installOptionalPackages(packages) {
  const py = resolvePython();
  if (!py) return { ok: false, error: 'python.exe non trovato' };
  const pkgs = (packages || []).filter(Boolean);
  if (!pkgs.length) return { ok: true, installed: [], failed: [] };
  return new Promise((resolve) => {
    const args = ['-m', 'pip', 'install', '--quiet', '--disable-pip-version-check', ...pkgs];
    let proc;
    try {
      proc = spawn(py, args, { windowsHide: true, env: Object.assign({}, process.env, { PYTHONIOENCODING: 'utf-8' }) });
    } catch (e) { return resolve({ ok: false, error: 'spawn pip: ' + e.message }); }
    let out = '', err = '';
    proc.stdout.on('data', d => out += d.toString());
    proc.stderr.on('data', d => err += d.toString());
    proc.on('error', e => resolve({ ok: false, error: e.message, stdout: out, stderr: err }));
    proc.on('close', code => resolve({ ok: code === 0, code, stdout: out, stderr: err, installed: code === 0 ? pkgs : [], failed: code === 0 ? [] : pkgs }));
  });
}

module.exports = { resolvePython, resolvePyDir, runScript, spawnWorker, venvCheck, installOptionalPackages };
