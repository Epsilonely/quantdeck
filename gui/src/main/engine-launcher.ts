// Starts the engine when none is running (D-34). The virtualenv's pythonw runs
// `python -m quantdeck_engine run` hidden and detached: pythonw has no console, so Windows opens no
// console window for it, and a detached process keeps running after the GUI closes (D-01). The
// engine's instance lock (D-31) refuses a second engine, so starting one while another is still
// coming up is harmless.

import { spawn } from 'child_process'
import { existsSync } from 'fs'
import { readdir, readFile, stat } from 'fs/promises'
import { join } from 'path'
import type { LaunchState } from '../shared/engine'

const LOG_TAIL_BYTES = 64 * 1024
// "2026-10-16T05:00:39.169Z ERROR   quantdeck_engine: Engine failed to start: ..."
const ERROR_LINE = /^\S+\s+(?:ERROR|CRITICAL)\s+(.*)$/

export class EngineLauncher {
  private launch: LaunchState = { phase: 'idle', message: null }
  private running = false // a process this GUI started hasn't exited yet

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
    const child = spawn(python, ['-m', 'quantdeck_engine', 'run'], {
      cwd: this.engineDir,
      detached: true,
      windowsHide: true,
      stdio: 'ignore' // the engine logs to engine/logs (D-12)
    })
    this.running = true
    this.set('starting', null)
    console.log(`[launcher] started the engine (pid ${child.pid})`)
    child.on('error', (e) => {
      this.running = false
      this.set('failed', `엔진을 시작할 수 없습니다: ${e.message}`)
    })
    child.on('exit', (code) => {
      if (!this.running) return // 'error' already reported it
      this.running = false
      console.log(`[launcher] the engine exited (code ${code})`)
      if (code === 0) {
        this.set('idle', null) // stopped on request (D-34)
        return
      }
      void describeExit(this.engineDir, code, startedAt).then((message) =>
        this.set('failed', message)
      )
    })
    child.unref()
  }

  /** The GUI reached an engine, whichever process it is. */
  connected(): void {
    this.set('idle', null)
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

/** The exit code and the last error the engine logged, from a log file written since the start. */
async function describeExit(
  engineDir: string,
  code: number | null,
  startedAt: number
): Promise<string> {
  const head = code === null ? '엔진이 종료되었습니다' : `엔진이 종료되었습니다 (종료 코드 ${code})`
  try {
    const logDir = join(engineDir, 'logs')
    const names = (await readdir(logDir)).filter((n) => /^engine_.*\.log$/.test(n)).sort()
    const newest = names.at(-1)
    // Older files belong to earlier runs; the clock's resolution allows a second of slack.
    if (newest && (await stat(join(logDir, newest))).mtimeMs >= startedAt - 1000) {
      const text = await readFile(join(logDir, newest), 'utf-8')
      const lines = text.slice(-LOG_TAIL_BYTES).split(/\r?\n/)
      for (let i = lines.length - 1; i >= 0; i--) {
        const match = ERROR_LINE.exec(lines[i])
        if (match) return `${head}: ${match[1]}`
      }
    }
  } catch {
    // Fall through to the generic hint.
  }
  return `${head}. engine/logs의 로그를 확인하세요`
}
