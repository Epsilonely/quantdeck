// Starts the engine when none is running (D-34). The virtualenv's pythonw runs
// `python -m quantdeck_engine run` hidden and detached: pythonw has no console, so Windows opens no
// console window for it. The engine's instance lock (D-31) refuses a second engine, so starting one
// while another is still coming up is harmless.
//
// The engine must outlive the GUI (D-01), even when the terminal that runs `npm run dev` is closed
// while the GUI is open: that ends the terminal's whole process tree, and a detached child of
// Electron is still in it (reproduced 2026-10-19). So a short-lived starter, Electron run as plain
// Node, launches the engine, reports its pid, and exits; the engine never has a living parent in
// the GUI's tree. Not being its parent, the GUI can't read its exit code, so it watches the pid and
// reads the engine's log to tell a stop from a failure.

import { spawn } from 'child_process'
import { existsSync } from 'fs'
import { readdir, readFile, stat } from 'fs/promises'
import { join } from 'path'
import type { LaunchState } from '../shared/engine'

const LOG_TAIL_BYTES = 64 * 1024
const WATCH_INTERVAL_MS = 1000
// "2026-10-16T05:00:39.169+09:00 ERROR   quantdeck_engine.cli: Engine failed to start: ..."
const ERROR_LINE = /^\S+\s+(?:ERROR|CRITICAL)\s+(.*)$/
// Logged when `stop_engine` (D-34) or Ctrl+C stops the engine.
const STOPPED_LINE = /^\S+\s+INFO\s+.*Engine stopped by /

// Runs in the starter: argv is the engine's python and its directory. Prints the engine's pid.
const STARTER = `
const { spawn } = require('child_process')
const [python, cwd] = process.argv.slice(1)
const engine = spawn(python, ['-m', 'quantdeck_engine', 'run'], {
  cwd, detached: true, windowsHide: true, stdio: 'ignore'
})
engine.on('error', (e) => { process.stderr.write(e.message); process.exit(1) })
engine.on('spawn', () => { process.stdout.write(String(engine.pid)); process.exit(0) })
`

export class EngineLauncher {
  private launch: LaunchState = { phase: 'idle', message: null }
  private running = false // an engine this GUI started hasn't exited yet

  constructor(
    private readonly engineDir: string,
    private readonly onChange: (launch: LaunchState) => void
  ) {}

  get state(): LaunchState {
    return this.launch
  }

  start(): void {
    if (this.running) return
    const python = pythonPath(this.engineDir)
    if (!existsSync(python)) {
      this.set('failed', `${python}이 없습니다. engine 폴더에서 uv sync를 실행하세요`)
      return
    }
    const startedAt = Date.now()
    const starter = spawn(process.execPath, ['-e', STARTER, python, this.engineDir], {
      env: { ...process.env, ELECTRON_RUN_AS_NODE: '1' },
      windowsHide: true,
      stdio: ['ignore', 'pipe', 'pipe']
    })
    this.running = true
    this.set('starting', null)
    let out = ''
    let err = ''
    starter.stdout.on('data', (chunk) => (out += chunk))
    starter.stderr.on('data', (chunk) => (err += chunk))
    starter.on('error', (e) => {
      this.running = false
      this.set('failed', `엔진을 시작할 수 없습니다: ${e.message}`)
    })
    starter.on('close', (code) => {
      if (!this.running) return // 'error' already reported it
      const pid = Number.parseInt(out, 10)
      if (code !== 0 || !Number.isInteger(pid)) {
        this.running = false
        this.set('failed', `엔진을 시작할 수 없습니다: ${err.trim() || `종료 코드 ${code}`}`)
        return
      }
      console.log(`[launcher] started the engine (pid ${pid})`)
      this.watch(pid, startedAt)
    })
  }

  /** The GUI reached an engine, whichever process it is. */
  connected(): void {
    this.set('idle', null)
  }

  private watch(pid: number, startedAt: number): void {
    const timer = setInterval(() => {
      if (isAlive(pid)) return
      clearInterval(timer)
      this.running = false
      console.log(`[launcher] the engine (pid ${pid}) exited`)
      void describeExit(this.engineDir, startedAt).then((message) =>
        // No message: stopped on request (D-34).
        message === null ? this.set('idle', null) : this.set('failed', message)
      )
    }, WATCH_INTERVAL_MS)
  }

  private set(phase: LaunchState['phase'], message: string | null): void {
    if (this.launch.phase === phase && this.launch.message === message) return
    this.launch = { phase, message }
    this.onChange(this.launch)
  }
}

function pythonPath(engineDir: string): string {
  return process.platform === 'win32'
    ? join(engineDir, '.venv', 'Scripts', 'pythonw.exe')
    : join(engineDir, '.venv', 'bin', 'python')
}

function isAlive(pid: number): boolean {
  try {
    process.kill(pid, 0) // signal 0 only checks that the process exists
    return true
  } catch (e) {
    return (e as NodeJS.ErrnoException).code === 'EPERM'
  }
}

/** Null if the engine logged a stop on request; otherwise why it exited, from the latest error it
 * logged in a log file written since the start. */
async function describeExit(engineDir: string, startedAt: number): Promise<string | null> {
  const head = '엔진이 종료되었습니다'
  try {
    const logDir = join(engineDir, 'logs')
    const names = (await readdir(logDir)).filter((n) => /^engine_.*\.log$/.test(n)).sort()
    const newest = names.at(-1)
    // Older files belong to earlier runs; the clock's resolution allows a second of slack.
    if (newest && (await stat(join(logDir, newest))).mtimeMs >= startedAt - 1000) {
      const text = await readFile(join(logDir, newest), 'utf-8')
      const lines = text.slice(-LOG_TAIL_BYTES).split(/\r?\n/)
      for (let i = lines.length - 1; i >= 0; i--) {
        if (STOPPED_LINE.test(lines[i])) return null
        const match = ERROR_LINE.exec(lines[i])
        if (match) return `${head}: ${match[1]}`
      }
    }
  } catch {
    // Fall through to the generic hint.
  }
  return `${head}. engine/logs의 로그를 확인하세요`
}
