import { useCallback, useEffect, useRef, useState } from 'react'

// ─── Protocol (mirror hub_server.py) ─────────────────────────────────────────
// Server -> client:
//   {type:'init', logs, entries, status, modules, steps, kma}
//   {type:'log', module, msg, ts}
//   {type:'entries', entries}
//   {type:'status_update', nota, status, msg, ts}
//   {type:'module_status', module, connected, ts}
//   {type:'step_detail_update', nota, steps}
//   {type:'kma_state', routes, printers, rules, stats}
//   {type:'command_result', id, action, nota, ok, msg}
// Client -> server:
//   {type:'subscribe', role:'dashboard', id}
//   {type:'command', id, action, nota?, reason?, qty?, file?, machine?, module?}

export interface HubLogEntry {
  type: 'log'
  module: string
  msg: string
  ts: string
}

export interface WmaEntry {
  nota: string
  bahan: string
  total_qty: number
  jenis?: string
  process_type?: string | null
  operator?: string
  machine?: string
  files: string[]
  customer?: string
  verified?: boolean
  sides?: string
  laminating?: string
}

export interface NotaStatus {
  status: string
  msg: string
  ts: string
}

export interface ModuleState {
  connected: boolean
  ts: string
}

export interface HubStep {
  name: string
  machine: string
  material: string
  sdm: string
  qty: string | number
  status: 'pending' | 'ok' | 'error'
  msg?: string
}

export interface KmaRoute {
  file: string
  category: string
  dest: string
  status: string
  msg?: string
  ts: string
  machine?: string
}

export interface KmaPrinter {
  ip: string
  role: string
  status: string
  active: boolean
  machine?: string
}

export interface KmaRule {
  category: string
  dest: string
}

export interface KmaStats {
  ok?: number
  ret?: number
  done?: number
}

export interface KmaState {
  routes: KmaRoute[]
  printers: KmaPrinter[]
  rules: KmaRule[]
  stats: KmaStats
}

export interface OutputFile {
  name: string
  size?: number
  ts?: number
}

export interface CommandResult {
  id: string
  action: string
  nota?: string
  module?: string
  folders?: Record<string, string>
  files?: OutputFile[]
  ok: boolean
  msg: string
}

export interface SendCommandOpts {
  nota?: string
  reason?: string
  qty?: string
  file?: string
  machine?: string
  module?: string
  folders?: Record<string, string>
  dest?: string
  files?: string[]
  operator?: string
}

export type ConnState = 'connecting' | 'open' | 'closed'

export interface KmmaTimer {
  next_in: number
  interval: number
}

export interface HubState {
  conn: ConnState
  logs: HubLogEntry[]
  entries: WmaEntry[]
  entriesAll: WmaEntry[]
  verified: string[]
  kmmaTimer: KmmaTimer | null
  outputFiles: OutputFile[]
  statuses: Record<string, NotaStatus>
  modules: Record<string, ModuleState>
  details: Record<string, HubStep[]>
  results: CommandResult[]
  kma: KmaState
}

/** Default WS: ikuti hostname halaman (LAN IP saat dibuka di PC lain, localhost di PC sendiri). */
export function defaultHubUrl(): string {
  try {
    const host = location.hostname || 'localhost'
    return `ws://${host}:8765`
  } catch {
    return 'ws://localhost:8765'
  }
}

const MAX_LOGS = 200
const RECONNECT_DELAYS = [1000, 2000, 5000, 10000]

function handleIncoming(prev: HubState, d: unknown, cap: number): HubState {
  if (typeof d !== 'object' || d === null) return prev
  const msg = d as Record<string, unknown>

  switch (msg.type) {
    case 'log': {
      const entry = msg as unknown as HubLogEntry
      const logs = [...prev.logs, entry]
      if (logs.length > cap) logs.splice(0, logs.length - cap)
      return { ...prev, logs }
    }
    case 'entries': {
      return { ...prev, entries: (msg.entries as WmaEntry[]) ?? [] }
    }
    case 'entries_all': {
      return { ...prev, entriesAll: (msg.entries as WmaEntry[]) ?? [] }
    }
    case 'verified_update': {
      return { ...prev, verified: (msg.verified as string[]) ?? [] }
    }
    case 'kmma_timer_update': {
      return { ...prev, kmmaTimer: { next_in: Number(msg.next_in ?? 0), interval: Number(msg.interval ?? 180) } }
    }
    case 'output_update': {
      return { ...prev, outputFiles: (msg.files as OutputFile[]) ?? [] }
    }
    case 'status_update': {
      const nota = String(msg.nota ?? '')
      if (!nota) return prev
      return {
        ...prev,
        statuses: {
          ...prev.statuses,
          [nota]: {
            status: String(msg.status ?? ''),
            msg: String(msg.msg ?? ''),
            ts: String(msg.ts ?? ''),
          },
        },
      }
    }
    case 'module_status': {
      const module = String(msg.module ?? '')
      if (!module) return prev
      return {
        ...prev,
        modules: {
          ...prev.modules,
          [module]: { connected: msg.connected === true, ts: String(msg.ts ?? '') },
        },
      }
    }
    case 'step_detail_update': {
      const nota = String(msg.nota ?? '')
      if (!nota) return prev
      return {
        ...prev,
        details: { ...prev.details, [nota]: (msg.steps as HubStep[]) ?? [] },
      }
    }
    case 'kma_state': {
      return {
        ...prev,
        kma: {
          routes: (msg.routes as KmaRoute[]) ?? [],
          printers: (msg.printers as KmaPrinter[]) ?? [],
          rules: (msg.rules as KmaRule[]) ?? [],
          stats: (msg.stats as KmaStats) ?? {},
        },
      }
    }
    case 'command_result': {
      const res: CommandResult = {
        id: String(msg.id ?? ''),
        action: String(msg.action ?? ''),
        nota: msg.nota ? String(msg.nota) : undefined,
        module: msg.module ? String(msg.module) : undefined,
        ok: msg.ok === true,
        msg: String(msg.msg ?? ''),
        folders: (msg.folders as Record<string, string> | undefined) ?? undefined,
        files: (msg.files as OutputFile[] | undefined) ?? undefined,
      }
      const results = [...prev.results, res]
      if (results.length > 20) results.splice(0, results.length - 20)
      return { ...prev, results }
    }
    case 'init': {
      const logs = ((msg.logs as HubLogEntry[]) ?? []).slice(-cap)
      return {
        ...prev,
        logs,
        entries: (msg.entries as WmaEntry[]) ?? [],
        entriesAll: (msg.entries_all as WmaEntry[]) ?? [],
        verified: (msg.verified as string[]) ?? [],
        kmmaTimer: (msg.kmma_timer as KmmaTimer | null) ?? null,
        outputFiles: (msg.output_files as OutputFile[]) ?? [],
        statuses: (msg.status as Record<string, NotaStatus>) ?? {},
        modules: (msg.modules as Record<string, ModuleState>) ?? {},
        details: (msg.steps as Record<string, HubStep[]>) ?? {},
        kma: (msg.kma as KmaState) ?? { routes: [], printers: [], rules: [], stats: {} },
      }
    }
    default:
      return prev
  }
}

export function useHub(url: string = defaultHubUrl(), maxLogs: number = MAX_LOGS) {
  const [state, setState] = useState<HubState>({
    conn: 'connecting',
    logs: [],
    entries: [],
    entriesAll: [],
    verified: [],
    kmmaTimer: null,
    outputFiles: [],
    statuses: {},
    modules: {},
    details: {},
    results: [],
    kma: { routes: [], printers: [], rules: [], stats: {} },
  })
  const capRef = useRef(maxLogs)
  capRef.current = maxLogs
  const wsRef = useRef<WebSocket | null>(null)

  const sendCommand = useCallback((action: string, opts?: SendCommandOpts): string | null => {
    const id = `${Date.now().toString(36)}-${Math.floor(Math.random() * 1e6).toString(36)}`
    const payload: Record<string, unknown> = { type: 'command', id, action }
    if (opts?.nota) payload.nota = opts.nota
    if (opts?.reason) payload.reason = opts.reason
    if (opts?.qty !== undefined && opts.qty !== '') payload.qty = opts.qty
    if (opts?.file) payload.file = opts.file
    if (opts?.machine) payload.machine = opts.machine
    if (opts?.module) payload.module = opts.module
    if (opts?.folders) payload.folders = opts.folders
    if (opts?.dest) payload.dest = opts.dest
    if (opts?.files) payload.files = opts.files
    if (opts?.operator) payload.operator = opts.operator
    try {
      wsRef.current?.send(JSON.stringify(payload))
    } catch {
      return null
    }
    if (wsRef.current?.readyState !== WebSocket.OPEN) return null
    return id
  }, [])

  useEffect(() => {
    let ws: WebSocket | null = null
    let closed = false
    let attempt = 0
    let timer: ReturnType<typeof setTimeout> | undefined

    const connect = () => {
      if (closed) return
      setState((p) => ({ ...p, conn: 'connecting' }))
      try {
        ws = new WebSocket(url)
        wsRef.current = ws
      } catch {
        scheduleReconnect()
        return
      }

      ws.onopen = () => {
        attempt = 0
        setState((p) => ({ ...p, conn: 'open' }))
        ws?.send(JSON.stringify({ type: 'subscribe', role: 'dashboard', id: 'dashboard-web' }))
      }

      ws.onmessage = (e) => {
        try {
          setState((p) => handleIncoming(p, JSON.parse(e.data), capRef.current))
        } catch {
          // ignore malformed frames
        }
      }

      ws.onclose = () => {
        if (closed) return
        if (wsRef.current === ws) wsRef.current = null
        setState((p) => ({ ...p, conn: 'closed' }))
        scheduleReconnect()
      }

      ws.onerror = () => {
        ws?.close()
      }
    }

    const scheduleReconnect = () => {
      if (closed) return
      const delay = RECONNECT_DELAYS[Math.min(attempt, RECONNECT_DELAYS.length - 1)]
      attempt += 1
      timer = setTimeout(connect, delay)
    }

    connect()

    return () => {
      closed = true
      if (timer) clearTimeout(timer)
      wsRef.current = null
      ws?.close()
    }
  }, [url])

  return { ...state, sendCommand }
}
