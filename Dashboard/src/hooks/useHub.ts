import { useCallback, useEffect, useRef, useState } from 'react'

// ─── Protocol (mirror hub_server.py) ─────────────────────────────────────────
// Server -> client:
//   {type:'init', logs, entries, entries_all, verified, kmma_timer, output_files, we, status, modules, steps, kma}
//   (we = snapshot WE: {date, monitoring, mode, override, seen_count, printed_count, verified_count, seen, printed, verified})
//   {type:'log', module, msg, ts}
//   {type:'entries', entries}
//   {type:'entries_all', entries}
//   {type:'verified_update', verified}
//   {type:'we_update', date, monitoring, mode, override, seen_count, printed_count, verified_count, seen, printed, verified}
//   {type:'we_state', ...sama spt we_update (ganti-penuh)}
//   {type:'we_verified_update', verified} (defensif: hub saat ini kirim verified_update; digabung union)
//   {type:'kmma_timer_update', next_in, interval}
//   {type:'output_update', files}
//   {type:'status_update', nota, status, msg, ts}
//   {type:'module_status', module, connected, ts}
//   {type:'step_detail_update', nota, steps}
//   {type:'kma_state', routes, printers, rules, stats}
//   {type:'command_result', id, action, nota?, module?, folders?, files?, ok, msg}
// Client -> server:
//   {type:'subscribe', role:'dashboard', id}
//   {type:'command', id, action, nota?, reason?, qty?, file?, machine?, module?, folders?, dest?, files?, operator?}

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
  test?: boolean
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

// ─── WE (WisdomEye): baris file toleran — hanya filename wajib ───────────────
export interface WeFile {
  filename: string
  nota?: string
  bahan?: string
  sides?: string
  laminating?: string
  test?: boolean
  process_type?: string
  customer?: string
  total_qty?: number
  jenis?: string
  printed_ts?: string
  output_name?: string
  operator?: string
  machine?: string
  first_seen_ts?: string
}

export interface WeAuto {
  mode: string
  monitoring: string
  override: boolean
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
  bahan?: string
  sides?: string
  laminating?: string
  test?: boolean
  process_type?: string
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
  /** skey per-baris 6-tuple: "nota||bahan||sides||lam||1/0||process_type" */
  verified: string[]
  kmmaTimer: KmmaTimer | null
  outputFiles: OutputFile[]
  statuses: Record<string, NotaStatus>
  modules: Record<string, ModuleState>
  details: Record<string, HubStep[]>
  results: CommandResult[]
  kma: KmaState
  /** WE: file terlihat (INPUT) + tercetak (Impose/DEVELOP/RICOH/output_moves) */
  weSeen: WeFile[]
  wePrinted: WeFile[]
  /** WE: skey per-baris 6-tuple "nota||bahan||sides||lam||1/0||process_type" */
  weVerified: string[]
  /** WE: mode auto/manual + path MONITORING (null bila hub lama tanpa `we`) */
  weAuto: WeAuto | null
}

export function getAuthToken(): string {
  try { return localStorage.getItem('nsh_token') || '' } catch { return '' }
}
export function getAuthRole(): string {
  try { return localStorage.getItem('nsh_role') || '' } catch { return '' }
}
export function getAuthUser(): string {
  try { return localStorage.getItem('nsh_user') || '' } catch { return '' }
}
export function clearAuth() {
  try {
    localStorage.removeItem('nsh_token')
    localStorage.removeItem('nsh_role')
    localStorage.removeItem('nsh_user')
  } catch {}
  try { document.cookie = 'nsh_token=; Max-Age=0; Path=/; SameSite=Lax' } catch {}
}

/** Default WS: ikuti hostname halaman + ?token= bila ada (LAN IP saat dibuka di PC lain, localhost di PC sendiri). */
export function defaultHubUrl(): string {
  try {
    const host = location.hostname || 'localhost'
    let base = `ws://${host}:8765`
    const tok = getAuthToken()
    if (tok) base += `?token=${encodeURIComponent(tok)}`
    return base
  } catch {
    return 'ws://localhost:8765'
  }
}

const MAX_LOGS = 200
const RECONNECT_DELAYS = [1000, 2000, 5000, 10000]

/** Baris WE toleran: hanya filename wajib, sisanya opsional (hub lama/baru). */
function normalizeWeFiles(arr: unknown): WeFile[] {
  if (!Array.isArray(arr)) return []
  const out: WeFile[] = []
  for (const r of arr) {
    if (typeof r !== 'object' || r === null) continue
    const o = r as Record<string, unknown>
    const filename = String(o.filename ?? '')
    if (!filename) continue
    const str = (v: unknown): string | undefined =>
      v === null || v === undefined || v === '' ? undefined : String(v)
    const num = (v: unknown): number | undefined => {
      if (v === null || v === undefined || v === '') return undefined
      const n = Number(v)
      return Number.isFinite(n) ? n : undefined
    }
    out.push({
      filename,
      nota: str(o.nota),
      bahan: str(o.bahan),
      sides: str(o.sides),
      laminating: str(o.laminating),
      test: typeof o.test === 'boolean' ? o.test
        : o.test === 1 || o.test === '1' || o.test === 'true' ? true
        : o.test === 0 || o.test === '0' || o.test === 'false' ? false : undefined,
      process_type: str(o.process_type),
      customer: str(o.customer),
      total_qty: num(o.total_qty),
      jenis: str(o.jenis),
      printed_ts: str(o.printed_ts),
      output_name: str(o.output_name),
      operator: str(o.operator),
      machine: str(o.machine),
      first_seen_ts: str(o.first_seen_ts),
    })
  }
  return out
}

/** Mode WE dari WS (mode/override top-level) atau HTTP (auto:{mode,override} nested). */
function normalizeWeAuto(src: unknown): WeAuto | null {
  if (typeof src !== 'object' || src === null) return null
  const o = src as Record<string, unknown>
  const nested = (typeof o.auto === 'object' && o.auto !== null)
    ? (o.auto as Record<string, unknown>) : undefined
  const modeRaw = o.mode ?? nested?.mode
  // tanpa info mode sama sekali → hub lama: null (bukan default menipu)
  if (modeRaw === null || modeRaw === undefined) return null
  return {
    mode: String(modeRaw || 'wait'),
    monitoring: String(o.monitoring ?? ''),
    override: Boolean(o.override ?? nested?.override ?? false),
  }
}

/** Terapkan payload WE (we_update / we_state / GET /api/we/state) ganti-penuh per daftar. */
function applyWePayload(prev: HubState, src: unknown): HubState {
  if (typeof src !== 'object' || src === null) return prev
  const o = src as Record<string, unknown>
  return {
    ...prev,
    weSeen: o.seen !== undefined ? normalizeWeFiles(o.seen) : prev.weSeen,
    wePrinted: o.printed !== undefined ? normalizeWeFiles(o.printed) : prev.wePrinted,
    weVerified: o.verified !== undefined ? normalizeVerified(o.verified) : prev.weVerified,
    weAuto: normalizeWeAuto(src) ?? prev.weAuto,
  }
}
/** Migrasi rkey legacy 5-tuple (tanpa process_type) -> 6-tuple; nota-only dibiarkan (fallback). */
function normalizeVerified(arr: unknown): string[] {
  if (!Array.isArray(arr)) return []
  return (arr as unknown[]).map(v => {
    const s = String(v ?? '')
    if (!s) return ''
    if (!s.includes('||')) return s
    const parts = s.split('||')
    if (parts.length === 5) return s + '||'
    if (parts.length < 6) {
      while (parts.length < 6) parts.push('')
      return parts.join('||')
    }
    return s
  }).filter(Boolean) as string[]
}

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
      return { ...prev, verified: normalizeVerified(msg.verified) }
    }
    case 'we_state':
    case 'we_update': {
      // ganti-penuh tiga daftar + auto (kunci tak ada → pertahankan lama, anti hapus)
      return applyWePayload(prev, msg)
    }
    case 'we_verified_update': {
      // defensif: hub saat ini siarkan verified_update utk WE (via _mark_verified);
      // tipe ini digabung union agar tak ada verified yg hilang bila hub baru mengirimnya
      const inc = normalizeVerified(msg.verified)
      if (inc.length === 0) return prev
      const merged = new Set([...prev.weVerified, ...inc])
      return { ...prev, weVerified: [...merged] }
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
      const key = msg.key ? String(msg.key) : nota
      return {
        ...prev,
        statuses: {
          ...prev.statuses,
          [key]: {
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
      const key = msg.key ? String(msg.key) : nota
      return {
        ...prev,
        details: { ...prev.details, [key]: (msg.steps as HubStep[]) ?? [] },
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
      // WE aditif: hub lama tanpa `we` → irisan kosong, tanpa crash
      const w = (typeof msg.we === 'object' && msg.we !== null)
        ? (msg.we as Record<string, unknown>) : null
      return {
        ...prev,
        logs,
        entries: (msg.entries as WmaEntry[]) ?? [],
        entriesAll: (msg.entries_all as WmaEntry[]) ?? [],
        verified: normalizeVerified(msg.verified),
        kmmaTimer: (msg.kmma_timer as KmmaTimer | null) ?? null,
        outputFiles: (msg.output_files as OutputFile[]) ?? [],
        weSeen: w ? normalizeWeFiles(w.seen) : [],
        wePrinted: w ? normalizeWeFiles(w.printed) : [],
        weVerified: w ? normalizeVerified(w.verified) : [],
        weAuto: w ? normalizeWeAuto(w) : null,
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
    weSeen: [],
    wePrinted: [],
    weVerified: [],
    weAuto: null,
  })
  const capRef = useRef(maxLogs)
  capRef.current = maxLogs
  const wsRef = useRef<WebSocket | null>(null)

  const sendCommand = useCallback((action: string, opts?: SendCommandOpts): string | null => {
    const id = `${Date.now().toString(36)}-${Math.floor(Math.random() * 1e6).toString(36)}`
    const tok = getAuthToken()
    const payload: Record<string, unknown> = { type: 'command', id, action, token: tok }
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
    if (opts?.bahan !== undefined) payload.bahan = opts.bahan
    if (opts?.sides !== undefined) payload.sides = opts.sides
    if (opts?.laminating !== undefined) payload.laminating = opts.laminating
    if (opts?.test !== undefined) payload.test = opts.test
    if (opts?.process_type !== undefined) payload.process_type = opts.process_type
    try {
      wsRef.current?.send(JSON.stringify(payload))
    } catch {
      return null
    }
    if (wsRef.current?.readyState !== WebSocket.OPEN) return null
    return id
  }, [])

  /** Refresh manual WE via HTTP (pola fetch tab Database: Bearer + base :8766). */
  const weRefresh = useCallback(async (): Promise<boolean> => {
    try {
      let base = 'http://localhost:8766'
      try {
        const u = new URL(url)
        base = `http://${u.hostname || 'localhost'}:8766`
      } catch { /* pakai default localhost */ }
      const tok = getAuthToken()
      const r = await fetch(`${base}/api/we/state`, {
        headers: tok ? { Authorization: `Bearer ${tok}` } : undefined,
      })
      const j = await r.json().catch(() => null)
      if (!r.ok || !j || j.ok !== true) return false
      setState(p => applyWePayload(p, j))
      return true
    } catch {
      return false
    }
  }, [url])

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
        const tok = getAuthToken()
        ws?.send(JSON.stringify({ type: 'subscribe', role: 'dashboard', id: 'dashboard-web', token: tok }))
      }

      ws.onmessage = (e) => {
        try {
          setState((p) => handleIncoming(p, JSON.parse(e.data), capRef.current))
        } catch {
          // ignore malformed frames
        }
      }

      ws.onclose = (e: CloseEvent) => {
        if (closed) return
        if (wsRef.current === ws) wsRef.current = null
        setState((p) => ({ ...p, conn: 'closed' }))
        // 4401 = unauthorized → redirect login
        try {
          if ((e as CloseEvent).code === 4401) {
            clearAuth()
            // avoid loop: only redirect if not already on login
            if (location.pathname !== '/login' && location.hash !== '#/login') {
              location.href = '/login'
            }
            return
          }
        } catch {}
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

  return { ...state, sendCommand, weRefresh }
}
