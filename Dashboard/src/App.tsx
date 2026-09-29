import { useState, useEffect, useRef, useCallback } from 'react'
import { createPortal } from 'react-dom'
import { useHub, defaultHubUrl, getAuthToken, getAuthRole, getAuthUser, clearAuth, type HubLogEntry, type WmaEntry, type NotaStatus, type ModuleState, type HubStep, type CommandResult, type SendCommandOpts, type KmaRoute, type KmaPrinter, type KmaRule, type KmaStats, type KmmaTimer, type OutputFile, type WeFile, type WeAuto } from './hooks/useHub'
import { pushToast, onToast, type ToastItem } from './toast'
import LoginPage from './pages/Login'

// ─── Shared live-data helpers ───────────────────────────────────────────────

function guessLevel(msg: string): StatusLevel {
  if (/gagal|error|fail|expired|!!|✕/i.test(msg)) return 'danger'
  if (/warn|skip|lewati|melebihi|tidak |belum |retry|⚠/i.test(msg)) return 'warning'
  return 'success'
}

// ─── Module folder view (Lite: INPUT/OUTPUT/UDA, KMA: INPUT/RETURN/UDA) ─────
// Read-only: path diambil via get_folders, ditulis otomatis oleh engine WE.
// Gaya sama dengan blok Monitoring WE: hanya menampilkan kemana path melihat.

interface FolderField {
  key: string
  label: string
}

function useModuleFolders(
  module: string,
  live: boolean,
  sendCommand: (action: string, opts?: SendCommandOpts) => string | null,
  results: CommandResult[],
) {
  const [values, setValues] = useState<Record<string, string>>({})
  const [loaded, setLoaded] = useState(false)
  const [saving, setSaving] = useState(false)
  const pendingRef = useRef<Set<string>>(new Set())

  useEffect(() => {
    if (!live || loaded) return
    const id = sendCommand('get_folders', { module })
    if (id) pendingRef.current.add(id)
  }, [live, loaded, module, sendCommand])

  useEffect(() => {
    for (const r of results ?? []) {
      if (!pendingRef.current.has(r.id)) continue
      pendingRef.current.delete(r.id)
      if (r.action === 'get_folders') {
        if (r.ok && r.folders) {
          setValues(r.folders)
          setLoaded(true)
        } else {
          pushToast(`Gagal baca path ${module}: ${r.msg}`, 'danger')
        }
      } else if (r.action === 'set_folders') {
        setSaving(false)
        pushToast(r.ok ? `Path ${module}: ${r.msg}` : `Gagal simpan path: ${r.msg}`, r.ok ? 'success' : 'danger')
      }
    }
  }, [results, module])

  const save = (next: Record<string, string>) => {
    if (!live) {
      pushToast('Hub offline — path tidak dikirim', 'warning')
      return
    }
    const id = sendCommand('set_folders', { module, folders: next })
    if (!id) {
      pushToast('Hub offline — path tidak dikirim', 'warning')
      return
    }
    pendingRef.current.add(id)
    setSaving(true)
  }

  return { values, setValues, loaded, saving, save }
}

function PathView({ fields, values }: {
  fields: FolderField[]
  values: Record<string, string>
}) {
  return (
    <div className="card px-5 py-4">
      <SectionHeader label="Path Folder" />
      <div className="flex flex-col gap-1.5">
        {fields.map(f => (
          <div key={f.key} className="flex items-center gap-2.5">
            <span className="section-label shrink-0" style={{ minWidth: 64 }}>{f.label}</span>
            <span className="font-mono truncate flex-1" title={values[f.key] ?? ''}
              style={{ fontSize: 'var(--text-xs)', color: 'var(--color-text-secondary)' }}>
              {values[f.key] || '—'}
            </span>
          </div>
        ))}
      </div>
      <div style={{ fontSize: 10, color: 'var(--color-text-dim)', marginTop: 6 }}>
        Path otomatis dari engine WE — berubah sendiri mengikuti folder tanggal
      </div>
    </div>
  )
}

// ─── Types ────────────────────────────────────────────────────────────────────

type Page = 'ringkasan' | 'nshlite' | 'kma' | 'we' | 'wma' | 'kmma' | 'log' | 'pengaturan' | 'database'
type ConnStatus = 'connected' | 'connecting' | 'disconnected'
type StatusLevel = 'success' | 'warning' | 'danger' | 'violet' | 'accent' | 'muted'

const NAV: { id: Page; icon: string; label: string }[] = [
  { id: 'ringkasan',  icon: 'dashboard', label: 'HOME'        },
  { id: 'nshlite',   icon: 'layers',    label: 'NSH Lite'    },
  { id: 'kma',       icon: 'router',    label: 'KMA'         },
  { id: 'we',        icon: 'eye',       label: 'WisdomEye'},
  { id: 'wma',       icon: 'workflow',  label: 'WMA'         },
  { id: 'kmma',      icon: 'package',   label: 'KMMA'        },
  { id: 'log',       icon: 'terminal',  label: 'Log'         },
  { id: 'pengaturan',icon: 'settings',  label: 'Pengaturan'  },
  { id: 'database',  icon: 'database',  label: 'Database'    },
]
const PAGE_TITLE: Record<Page, string> = {
  ringkasan: 'HOME', nshlite: 'NSH Lite', kma: 'KMA Router',
  we: 'WisdomEye',
  wma: 'WMA', kmma: 'KMMA', log: 'Log Monitor', pengaturan: 'Pengaturan', database: 'Database',
}
const CONN_CFG: Record<ConnStatus, { status: StatusLevel; label: string; live: boolean }> = {
  connected:    { status: 'success', label: 'Terhubung',    live: true  },
  connecting:   { status: 'warning', label: 'Menghubungkan',live: false },
  disconnected: { status: 'danger',  label: 'Terputus',     live: false },
}

// ─── Icon sprite ──────────────────────────────────────────────────────────────

const PATHS: Record<string, string> = {
  dashboard:     'M3 13h8V3H3v10zm0 8h8v-6H3v6zm10 0h8V11h-8v10zm0-18v6h8V3h-8z',
  layers:        'M12 2L2 7l10 5 10-5-10-5zM2 17l10 5 10-5M2 12l10 5 10-5',
  router:        'M9 3H5a2 2 0 0 0-2 2v4m6-6h10a2 2 0 0 1 2 2v4M9 3v18m0 0h10a2 2 0 0 0 2-2V9M9 21H5a2 2 0 0 1-2-2V9m0 0h18',
  printer:       'M6 9V2h12v7M6 18H4a2 2 0 0 1-2-2v-5a2 2 0 0 1 2-2h16a2 2 0 0 1 2 2v5a2 2 0 0 1-2 2h-2M6 14h12v8H6v-8z',
  workflow:      'M22 12h-4l-3 9L9 3l-3 9H2',
  terminal:      'M4 17l6-6-6-6M12 19h8',
  settings:      'M12 15a3 3 0 1 0 0-6 3 3 0 0 0 0 6zM19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83-2.83l.06-.06A1.65 1.65 0 0 0 4.68 15a1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 2.83-2.83l.06.06A1.65 1.65 0 0 0 9 4.68a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 2.83l-.06.06A1.65 1.65 0 0 0 19.4 9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z',
  search:        'M21 21l-6-6m2-5a7 7 0 1 1-14 0 7 7 0 0 1 14 0z',
  chevronLeft:   'M15 18l-6-6 6-6',
  chevronRight:  'M9 18l6-6-6-6',
  chevronDown:   'M6 9l6 6 6-6',
  x:             'M18 6L6 18M6 6l12 12',
  check:         'M20 6L9 17l-5-5',
  send:          'M22 2L11 13M22 2l-7 20-4-9-9-4 20-7z',
  skip:          'M5 4l8 8-8 8M13 4l8 8-8 8',
  refresh:       'M23 4v6h-6M1 20v-6h6M3.51 9a9 9 0 0 1 14.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0 0 20.49 15',
  info:          'M12 16v-4m0-4h.01M22 12a10 10 0 1 1-20 0 10 10 0 0 1 20 0z',
  alertTriangle: 'M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0zM12 9v4M12 17h.01',
  eye:           'M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8zM12 9a3 3 0 1 0 0 6 3 3 0 0 0 0-6z',
  download:      'M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4M7 10l5 5 5-5M12 15V3',
  wifiOff:       'M1 1l22 22M16.72 11.06A10.94 10.94 0 0 1 19 12.55M5 12.55a10.94 10.94 0 0 1 5.17-2.39M10.71 5.05A16 16 0 0 1 22.56 9M1.42 9a15.91 15.91 0 0 1 4.7-2.88M8.53 16.11a6 6 0 0 1 6.95 0M12 20h.01',
  activity:      'M22 12h-4l-3 9L9 3l-3 9H2',
  save:          'M19 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11l5 5v11a2 2 0 0 1-2 2zM17 21v-8H7v8M7 3v5h8',
  menu:          'M3 12h18M3 6h18M3 18h18',
  package:       'M16.5 9.4l-9-5.19M21 16V8a2 2 0 0 0-1-1.73l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8a2 2 0 0 0 1 1.73l7 4a2 2 0 0 0 2 0l7-4A2 2 0 0 0 21 16zM3.27 6.96L12 12.01l8.73-5.05M12 22.08V12',
  user:          'M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2M12 11a4 4 0 1 0 0-8 4 4 0 0 0 0 8z',
  clock:         'M12 22a10 10 0 1 0 0-20 10 10 0 0 0 0 20zM12 6v6l4 2',
  power:         'M18.36 6.64a9 9 0 1 1-12.73 0M12 2v10',
  database:      'M3 12a9 4 0 0 0 9 4 9 4 0 0 0 9-4M3 12v6a9 4 0 0 0 9 4 9 4 0 0 0 9-4v-6M3 8a9 4 0 0 1 9-4 9 4 0 0 1 9 4 9 4 0 0 1-9 4 9 4 0 0 1-9-4z',
  table:         'M3 3h18v18H3zM3 9h18M3 15h18M9 3v18M15 3v18',
}

const Icon = ({ name, size = 18, className = '', style }: { name: string; size?: number; className?: string; style?: React.CSSProperties }) => {
  const d = PATHS[name]
  if (!d) return null
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none"
      stroke="currentColor" strokeWidth={1.75} strokeLinecap="round" strokeLinejoin="round"
      className={`shrink-0 ${className}`} style={style}>
      <path d={d} />
    </svg>
  )
}

// ─── Token-mapped color lookup ────────────────────────────────────────────────

const STATUS_COLOR: Record<StatusLevel, string> = {
  success: 'var(--color-success)',
  warning: 'var(--color-warning)',
  danger:  'var(--color-danger)',
  violet:  'var(--color-violet)',
  accent:  'var(--color-accent)',
  muted:   'var(--color-muted)',
}
const BADGE_CFG: Record<StatusLevel, { bg: string; fg: string }> = {
  success: { bg: 'var(--color-success-ghost)', fg: 'var(--color-success-fg)' },
  warning: { bg: 'var(--color-warning-ghost)', fg: 'var(--color-warning-fg)' },
  danger:  { bg: 'var(--color-danger-ghost)',  fg: 'var(--color-danger-fg)'  },
  violet:  { bg: 'var(--color-violet-ghost)',  fg: 'var(--color-violet-fg)'  },
  accent:  { bg: 'var(--color-accent-ghost)',  fg: 'var(--color-accent-fg)'  },
  muted:   { bg: 'var(--color-muted-ghost)',   fg: 'var(--color-muted-fg)'   },
}

// ─── Primitives ───────────────────────────────────────────────────────────────

const StatusDot = ({ status, live = false, shake = false }: { status: StatusLevel; live?: boolean; shake?: boolean }) => (
  <span
    className={`inline-block rounded-full shrink-0 ${live ? 'animate-pulse-dot' : ''} ${shake ? 'animate-shake' : ''}`}
    style={{
      width: 8, height: 8,
      background: STATUS_COLOR[status] ?? STATUS_COLOR.muted,
      boxShadow: live ? `0 0 0 3px ${(STATUS_COLOR[status] ?? STATUS_COLOR.muted)}25` : undefined,
    }}
  />
)

const Badge = ({ label, status, size = 'sm' }: { label: string; status: StatusLevel; size?: 'xs' | 'sm' }) => {
  const c = BADGE_CFG[status] ?? BADGE_CFG.muted
  return (
    <span className="inline-flex items-center gap-1 rounded-full font-bold uppercase tracking-wider"
      style={{
        padding: size === 'xs' ? '2px 7px' : '3px 9px',
        fontSize: size === 'xs' ? 'var(--text-label)' : 'var(--text-label)',
        letterSpacing: '0.04em', background: c.bg, color: c.fg,
      }}>
      <StatusDot status={status} />
      {label}
    </span>
  )
}

const CategoryBadge = ({ label }: { label: string }) => {
  const hue = label.charCodeAt(0) * 37 % 360
  return (
    <span className="inline-block font-bold uppercase tracking-widest rounded-sm"
      style={{
        padding: '2px 8px', fontSize: 'var(--text-label)',
        background: `hsl(${hue},50%,25%)`, color: `hsl(${hue},80%,75%)`,
        border: `1px solid hsl(${hue},50%,35%)`,
      }}>{label}</span>
  )
}

const Skeleton = ({ w = '100%', h = 16, rounded = 6 }: { w?: string | number; h?: number; rounded?: number }) => (
  <div className="animate-shimmer" style={{ width: w, height: h, borderRadius: rounded }} />
)

const EmptyState = ({ icon, title, description }: { icon: string; title: string; description: string }) => (
  <div className="flex flex-col items-center justify-center gap-3 py-12 px-6"
    style={{ color: 'var(--color-text-muted)' }}>
    <Icon name={icon} size={40} />
    <div className="font-semibold" style={{ fontSize: 'var(--text-md)', color: 'var(--color-text-secondary)' }}>{title}</div>
    <div className="text-center max-w-xs" style={{ fontSize: 'var(--text-sm)' }}>{description}</div>
  </div>
)

const SectionHeader = ({ label, action }: { label: string; action?: React.ReactNode }) => (
  <div className="flex items-center justify-between mb-3">
    <span className="section-label">{label}</span>
    {action}
  </div>
)

// ─── Toast system ─────────────────────────────────────────────────────────────

const ToastContainer = () => {
  const [toasts, setToasts] = useState<(ToastItem & { out?: boolean })[]>([])
  useEffect(() => {
    return onToast((t: ToastItem) => {
      setToasts(p => [...p, t])
      setTimeout(() => {
        setToasts(p => p.map(x => x.id === t.id ? { ...x, out: true } : x))
        setTimeout(() => setToasts(p => p.filter(x => x.id !== t.id)), 300)
      }, 4000)
    })
  }, [])

  return (
    <div className="fixed flex flex-col gap-2" style={{ top: 20, right: 20, zIndex: 9999 }}>
      {toasts.map(t => {
        const color = STATUS_COLOR[t.type]
        return (
          <div key={t.id} className={`flex items-center gap-2.5 rounded-xl font-medium ${t.out ? 'animate-toast-out' : 'animate-toast-in'}`}
            style={{
              padding: '10px 16px', maxWidth: 340,
              background: 'var(--color-panel)',
              border: `1px solid ${color}50`,
              borderLeft: `3px solid ${color}`,
              boxShadow: 'var(--shadow-panel)',
              fontSize: 'var(--text-sm)', color: 'var(--color-text)',
            }}>
            <StatusDot status={t.type} />
            {t.message}
          </div>
        )
      })}
    </div>
  )
}

// ─── KPI Card ─────────────────────────────────────────────────────────────────

const KpiCard = ({ label, value, unit, delta, status, icon }: {
  label: string; value: number; unit?: string; delta?: string; status: StatusLevel; icon: string
}) => {
  const [display, setDisplay] = useState(value)
  const [flash, setFlash] = useState(false)
  const prev = useRef(value)

  useEffect(() => {
    if (value === prev.current) return
    setFlash(true)
    const start = prev.current, end = value
    let step = 0
    const timer = setInterval(() => {
      step++
      setDisplay(Math.round(start + (end - start) * step / 20))
      if (step >= 20) { clearInterval(timer); prev.current = value }
    }, 15)
    setTimeout(() => setFlash(false), 700)
    return () => clearInterval(timer)
  }, [value])

  const color = STATUS_COLOR[status]

  return (
    <div className={`card px-6 py-5 cursor-default transition-[transform,box-shadow] duration-150 ${flash ? 'animate-kpi-flash' : ''}`}
      style={{ borderLeft: `3px solid ${color}` }}
      onMouseEnter={e => {
        const el = e.currentTarget as HTMLElement
        el.style.transform = 'translateY(-2px)'
        el.style.boxShadow = `var(--shadow-lifted), 0 0 0 1px ${color}30`
      }}
      onMouseLeave={e => {
        const el = e.currentTarget as HTMLElement
        el.style.transform = ''
        el.style.boxShadow = ''
      }}>
      <div className="flex justify-between items-start">
        {/* Left: label + value */}
        <div className="flex flex-col gap-2">
          <div className="section-label">{label}</div>
          <div className="flex items-baseline gap-1.5">
            <span className={`font-mono font-bold leading-none ${flash ? 'animate-count-flash' : ''}`}
              style={{ fontSize: 'var(--text-num)', color: 'var(--color-text)' }}>
              {display.toLocaleString()}
            </span>
            {unit && <span style={{ fontSize: 'var(--text-sm)', color: 'var(--color-text-muted)' }}>{unit}</span>}
          </div>
          {delta && (
            <span className="font-semibold" style={{ fontSize: 'var(--text-label)', color, marginTop: 2 }}>{delta}</span>
          )}
        </div>
        {/* Icon well */}
        <div className="flex items-center justify-center rounded-[10px]"
          style={{ width: 40, height: 40, background: `${color}20`, color }}>
          <Icon name={icon} size={20} />
        </div>
      </div>
    </div>
  )
}

// ─── Live clock ───────────────────────────────────────────────────────────────

const LiveClock = () => {
  const [now, setNow] = useState(new Date())
  useEffect(() => { const t = setInterval(() => setNow(new Date()), 1000); return () => clearInterval(t) }, [])
  const DAY = ['Min','Sen','Sel','Rab','Kam','Jum','Sab']
  const MON = ['Jan','Feb','Mar','Apr','Mei','Jun','Jul','Agu','Sep','Okt','Nov','Des']
  const hh = now.getHours().toString().padStart(2,'0')
  const mm = now.getMinutes().toString().padStart(2,'0')
  const ss = now.getSeconds().toString().padStart(2,'0')
  return (
    <div className="text-right">
      <div className="font-mono font-semibold tracking-wider" style={{ fontSize: 'var(--text-md)', color: 'var(--color-text)' }}>
        {hh}:{mm}:{ss}
      </div>
      <div style={{ fontSize: 'var(--text-label)', color: 'var(--color-text-muted)' }}>
        {DAY[now.getDay()]}, {now.getDate()} {MON[now.getMonth()]} {now.getFullYear()}
      </div>
    </div>
  )
}

// ─── Page: Ringkasan ──────────────────────────────────────────────────────────

// Kunci status kanonis per-baris: "nota||bahan||sides||lam||1/0||process_type"
function rkey(e: { nota: string; bahan?: string | null; sides?: string | null; laminating?: string | null; test?: boolean; process_type?: string | null }): string {
  return `${e.nota}||${e.bahan ?? ''}||${e.sides ?? ''}||${e.laminating ?? ''}||${e.test ? '1' : '0'}||${e.process_type ?? ''}`
}
function stOf(statuses: Record<string, NotaStatus>, e: { nota: string; bahan?: string | null; sides?: string | null; laminating?: string | null; test?: boolean; process_type?: string | null }): NotaStatus | undefined {
  return statuses[rkey(e)] ?? statuses[e.nota]
}

const MODULE_HEALTH = [
  { name: 'NSH Lite',     modKey: 'lite', ctl: true,  desc: 'Imposisi & antrean berjalan normal' },
  { name: 'KMA Router',   modKey: 'kma',  ctl: true,  desc: 'Routing file ke hotfolder printer' },
  { name: 'WMA Polling',  modKey: 'wma',  ctl: true,  desc: 'Agregasi entri nota' },
  { name: 'KMMA Tracker', modKey: 'kmma', ctl: true,  desc: 'Otomasi step KonsepMedia' },
]

const ModulePowerButtons = ({ isOn, name, modKey, onCmd }: {
  isOn: boolean
  name: string
  modKey: string | null
  onCmd: (action: 'module_on' | 'module_off' | 'module_restart', modKey: string | null, label: string) => void
}) => (
  <div className="flex items-center gap-0.5 shrink-0">
    {isOn ? (
      <button title={`Matikan ${name}`} onClick={() => onCmd('module_off', modKey, name)}
        className="rounded-md p-1.5 transition-colors hover:bg-white/5 cursor-pointer" style={{ color: 'var(--color-danger)' }}>
        <Icon name="power" size={13} />
      </button>
    ) : (
      <button title={`Hidupkan ${name}`} onClick={() => onCmd('module_on', modKey, name)}
        className="rounded-md p-1.5 transition-colors hover:bg-white/5 cursor-pointer" style={{ color: 'var(--color-success)' }}>
        <Icon name="power" size={13} />
      </button>
    )}
    <button title={`Restart ${name}`} onClick={() => onCmd('module_restart', modKey, name)}
      className="rounded-md p-1.5 transition-colors hover:bg-white/5 cursor-pointer" style={{ color: 'var(--color-accent)' }}>
      <Icon name="refresh" size={13} />
    </button>
  </div>
)

const PageRingkasan = ({ connected, logs, entriesAll, entries = [], statuses, verified = [], modules, live, sendCommand, results, kmaRoutes, weSeen = [], wePrinted = [], weVerified = [], weAuto = null, outputFiles = [], onGo }: {
  connected: boolean
  logs: HubLogEntry[]
  entriesAll: WmaEntry[]
  entries?: WmaEntry[]
  statuses: Record<string, NotaStatus>
  verified?: string[]
  modules: Record<string, ModuleState>
  live: boolean
  sendCommand: (action: string, opts?: SendCommandOpts) => string | null
  results: CommandResult[]
  kmaRoutes: KmaRoute[]
  weSeen?: WeFile[]
  wePrinted?: WeFile[]
  weVerified?: string[]
  weAuto?: WeAuto | null
  outputFiles?: OutputFile[]
  onGo?: (p: Page) => void
}) => {
  const pendingRef = useRef<Set<string>>(new Set())

  useEffect(() => {
    for (const r of results ?? []) {
      if (!pendingRef.current.has(r.id)) continue
      pendingRef.current.delete(r.id)
      pushToast(r.ok ? `OK: ${r.action} — ${r.msg}` : `Gagal: ${r.action} — ${r.msg}`, r.ok ? 'success' : 'danger')
    }
  }, [results])

  const doModuleCommand = (action: 'module_on' | 'module_off' | 'module_restart', modKey: string | null, label: string) => {
    if (!live || !modKey) {
      pushToast('Hub offline — perintah tidak dikirim', 'warning')
      return
    }
    const id = sendCommand(action, { module: modKey })
    if (!id) {
      pushToast('Hub offline — perintah tidak dikirim', 'warning')
      return
    }
    pendingRef.current.add(id)
    pushToast(`Perintah dikirim: ${action} ${label}`, 'accent')
  }
  const health = MODULE_HEALTH.map(m => {
    const mod = m.modKey ? modules[m.modKey] : undefined
    if (!mod) return { ...m, status: 'muted' as StatusLevel, lastRun: '—', desc: 'Menunggu koneksi hub' }
    return { ...m, status: (mod.connected ? 'success' : 'danger') as StatusLevel, lastRun: mod.ts || '—' }
  })

  // ── Akses Cepat: hitungan baca-saja, navigasi saja (tanpa aksi tulis) ──
  // WE pending — disalin verbatim dari PageWe (~1362-1369): printedSet + pendingCount
  const qaPrintedSet = new Set((wePrinted ?? []).map(f => f.filename))
  const qaWePending = (weSeen ?? []).filter(f => !qaPrintedSet.has(f.filename)).length
  // WMA menunggu aksi — disalin verbatim dari PageWma mid (~1692-1695) + isVerified (~1685)
  const qaIsVerified = (e: WmaEntry) => (verified ?? []).includes(rkey(e))
  const qaWmaMid = (entriesAll ?? []).filter(e => {
    const s = stOf(statuses ?? {}, e)?.status
    return (s === 'ok' || s === 'error' || s === 'manual') && !qaIsVerified(e)
  }).length
  // KMMA aktif — disalin verbatim dari PageKmma visibleEntries (~1886-1887)
  const qaKmmaAktif = (entries ?? []).filter(e => !qaIsVerified(e)).length
  // Lite output — jumlah file OUTPUT (sumber: PageNshLite outputFiles)
  const qaLiteCount = (outputFiles ?? []).length
  // KMA pending — disalin via kmaStatus (~1086-1092): bukan ok/done/return/error/ricoh
  const qaKmaPending = (kmaRoutes ?? []).filter(r => kmaStatus(r.status).label === 'PENDING').length
  // Log — tanpa badge, hanya hitungan baris
  const qaLogCount = (logs ?? []).length
  // weVerified disediakan utk kompatibilitas prop (HOME hanya tampilkan pending)
  void weVerified
  // Mode pill — disalin verbatim dari PageWe (~1531-1536) + isManual (~1332) + tgl (~1529-1530)
  const qaIsManual = !!weAuto?.override
  const qaNow = new Date()
  const qaTgl = `${qaNow.getDate()} ${WE_MON[qaNow.getMonth()]} ${qaNow.getFullYear()} (${WE_DAY[qaNow.getDay()]})`
  const qaIsWait = !weAuto || weAuto.mode === 'wait'
  const qaPill = qaIsWait
    ? { label: 'MENUNGGU folder', status: 'muted' as StatusLevel }
    : qaIsManual
      ? { label: 'MANUAL override', status: 'warning' as StatusLevel }
      : { label: `AUTO ${qaTgl}`, status: 'success' as StatusLevel }
  // 6 ubin — tanpa Database (ikuti filter sidebar ~3047: non-admin buang database)
  const qaTiles: { id: Page; icon: string; label: string; count: number; sub: string; color: string }[] = [
    { id: 'nshlite', icon: 'layers', label: 'NSH Lite', count: qaLiteCount, sub: 'File OUTPUT', color: qaLiteCount > 0 ? 'var(--color-accent)' : 'var(--color-text-dim)' },
    { id: 'kma', icon: 'router', label: 'KMA', count: qaKmaPending, sub: 'Menunggu routing', color: qaKmaPending > 0 ? 'var(--color-warning)' : 'var(--color-text-dim)' },
    { id: 'we', icon: 'eye', label: 'WisdomEye', count: qaWePending, sub: 'Menunggu cetak', color: qaWePending > 0 ? 'var(--color-accent)' : 'var(--color-text-dim)' },
    { id: 'wma', icon: 'workflow', label: 'WMA', count: qaWmaMid, sub: 'Menunggu aksi', color: qaWmaMid > 0 ? 'var(--color-warning)' : 'var(--color-text-dim)' },
    { id: 'kmma', icon: 'package', label: 'KMMA', count: qaKmmaAktif, sub: 'Antrean aktif', color: qaKmmaAktif > 0 ? 'var(--color-accent)' : 'var(--color-text-dim)' },
    { id: 'log', icon: 'terminal', label: 'Log', count: qaLogCount, sub: 'Baris log', color: 'var(--color-text-dim)' },
  ]

  return (
    <div className="flex flex-col gap-6">
      {/* Offline banner */}
      {!connected && (
        <div className="flex items-center gap-2.5 rounded-[10px] px-4 py-2.5"
          style={{ background: 'var(--color-panel)', border: '1px solid var(--color-warning-ghost)', fontSize: 'var(--text-sm)', color: 'var(--color-warning-fg)' }}>
          <Icon name="alertTriangle" size={16} />
          <span className="font-semibold">Hub Offline</span>
          <span style={{ color: 'var(--color-text-secondary)' }}>— Data di bawah adalah referensi terakhir, bukan data live</span>
          <span className="section-label ml-auto">DATA REFERENSI</span>
        </div>
      )}

      {/* Blok Akses Cepat: navigasi saja, tanpa aksi tulis */}
      <div className="card p-5">
        <SectionHeader label="Akses Cepat" action={
          <div className="flex items-center gap-1.5 min-w-0">
            <span className="font-mono truncate" title={weAuto?.monitoring || ''}
              style={{ fontSize: 'var(--text-xs)', color: 'var(--color-text-secondary)', maxWidth: 220 }}>
              {weAuto?.monitoring || 'Belum ada folder MONITORING'}
            </span>
            <Badge label={qaPill.label} status={qaPill.status} />
          </div>
        } />
        <div className="grid gap-3 modul-grid" style={{ gridTemplateColumns: 'repeat(3, 1fr)' }}>
          {qaTiles.map((t, i) => (
            <button key={t.id} onClick={() => onGo?.(t.id)} className="w-full text-left rounded-lg p-3.5 animate-fade-rise"
              style={{ background: 'var(--color-bg)', border: '1px solid var(--color-border)', animationDelay: `${Math.min(i * 30, 300)}ms`, cursor: 'pointer' }}>
              <div className="flex items-center gap-2 mb-2">
                <Icon name={t.icon} size={16} style={{ color: 'var(--color-text-secondary)' } as any} />
                <span className="font-semibold truncate flex-1" style={{ fontSize: 'var(--text-sm)', color: 'var(--color-text)' }}>{t.label}</span>
                <Icon name="chevronRight" size={14} style={{ color: 'var(--color-text-dim)' } as any} />
              </div>
              <div className="flex items-baseline gap-2">
                <span className="font-mono font-bold" style={{ fontSize: 'var(--text-num-sm)', color: t.color }}>{t.count.toLocaleString()}</span>
                <span style={{ fontSize: 10, color: 'var(--color-text-dim)' }}>{t.sub}</span>
              </div>
            </button>
          ))}
        </div>
      </div>

      {/* Blok Modul: kartu status + tombol power per modul */}
      <div className="card p-5">
        <SectionHeader label="Modul" action={
          <span style={{ fontSize: 'var(--text-label)', color: 'var(--color-text-muted)' }}>
            {health.filter(h => h.status === 'success').length}/{health.length} aktif
          </span>
        } />
        <div className="grid gap-3 modul-grid" style={{ gridTemplateColumns: 'repeat(4, 1fr)' }}>
          {health.map((m, i) => (
            <div key={i} className="rounded-lg p-3.5 flex flex-col gap-2.5" style={{ background: 'var(--color-bg)' }}>
              <div className="flex items-center gap-2">
                <StatusDot status={m.status} live={m.status === 'success'} />
                <div className="flex-1 min-w-0">
                  <div className="font-semibold truncate" style={{ fontSize: 'var(--text-sm)', color: 'var(--color-text)' }}>{m.name}</div>
                  <div className="truncate" style={{ fontSize: 10, color: 'var(--color-text-dim)' }}>{m.lastRun}</div>
                </div>
                {m.ctl && (
                  <ModulePowerButtons isOn={m.status === 'success'} name={m.name} modKey={m.modKey} onCmd={doModuleCommand} />
                )}
              </div>
            </div>
          ))}
        </div>
      </div>
    </div>
  )
}

// ─── Page: NSH Lite ───────────────────────────────────────────────────────────

function fmtSize(n?: number): string {
  if (n === undefined || n === null) return ''
  if (n < 1024) return `${n} B`
  if (n < 1048576) return `${(n / 1024).toFixed(1)} KB`
  return `${(n / 1048576).toFixed(1)} MB`
}

// Watcher file OUTPUT lite: pilih batch -> kirim ke DEVELOP / RICOH (via hub).
// Dipakai sebagai blok Hasil Impose tab NSH Lite.
function OutputWatcher({ connected, sendCommand, results, files, title = 'Hasil Impose', subtitle, onPreview }: {
  connected: boolean
  sendCommand: (action: string, opts?: SendCommandOpts) => string | null
  results: CommandResult[]
  files: OutputFile[]
  title?: string
  subtitle?: string
  onPreview?: (file: OutputFile) => void
}) {
  const [sel, setSel] = useState<Set<string>>(new Set())
  const [loading, setLoading] = useState(false)
  const [listErr, setListErr] = useState('')
  const [operator, setOperator] = useState(() => {
    try { return localStorage.getItem('nsh-operator') || '' } catch { return '' }
  })
  const pendingRef = useRef<Set<string>>(new Set())

  // files mengalir via push hub (event-driven); command hanya untuk refresh manual
  const refresh = useCallback((quiet = false) => {
    if (!connected) return
    const id = sendCommand('list_output')
    if (id) { pendingRef.current.add(id); if (!quiet) { setLoading(true); setListErr('') } }
  }, [connected, sendCommand])

  useEffect(() => { refresh() }, [refresh])

  useEffect(() => {
    for (const r of results ?? []) {
      if (!pendingRef.current.has(r.id)) continue
      pendingRef.current.delete(r.id)
      if (r.action === 'list_output') {
        setLoading(false)
        if (!(r.ok && r.files)) setListErr(r.msg || 'gagal membaca OUTPUT')
        else setListErr('')
      } else if (r.action === 'move_output') {
        if (r.ok) {
          pushToast(r.msg, 'success')
          setSel(new Set())
          refresh()
        } else {
          pushToast(`Gagal pindah: ${r.msg}`, 'danger')
        }
      }
    }
  }, [results, refresh])

  const toggle = (n: string) => setSel(p => { const s = new Set(p); if (s.has(n)) s.delete(n); else s.add(n); return s })
  const toggleAll = () => setSel(p => (p.size === files.length && files.length > 0) ? new Set() : new Set(files.map(f => f.name)))

  const pickOperator = (op: string) => {
    setOperator(op)
    try { localStorage.setItem('nsh-operator', op) } catch { /* abaikan */ }
  }

  const doMove = (dest: 'develop' | 'ricoh') => {
    if (sel.size === 0) { pushToast('Pilih file dulu', 'warning'); return }
    if (!operator) { pushToast('Pilih operator dulu (Nanas/Eric/Rahmadi)', 'warning'); return }
    if (!connected) { pushToast('Hub offline — perintah tidak dikirim', 'warning'); return }
    const id = sendCommand('move_output', { dest, files: [...sel], operator })
    if (!id) { pushToast('Hub offline — perintah tidak dikirim', 'warning'); return }
    pendingRef.current.add(id)
    pushToast(`Memindah ${sel.size} file ke ${dest.toUpperCase()} (${operator})…`, 'accent')
  }

  // VIEW modal: snapshot file terpilih + halaman per-file + cache-buster preview.
  // Guard sama seperti doMove minus syarat operator. Sub-panel tools (BW/hapus)
  // hidup di dalam ViewModal sehingga unmount = reset otomatis.
  const [viewOpen, setViewOpen] = useState(false)
  const [viewNames, setViewNames] = useState<string[]>([])
  const [viewIdx, setViewIdx] = useState(0)
  const [viewPages, setViewPages] = useState<Record<string, number>>({})
  const [imgTs, setImgTs] = useState(0)
  const openView = () => {
    if (sel.size === 0) { pushToast('Pilih file dulu', 'warning'); return }
    if (!connected) { pushToast('Hub offline — perintah tidak dikirim', 'warning'); return }
    const ordered = files.filter(f => sel.has(f.name)).map(f => f.name)
    setViewNames(ordered.length ? ordered : [...sel])
    setViewIdx(0)
    setViewPages({})
    setViewOpen(true)
  }
  const closeView = () => setViewOpen(false)

  return (
    <div className="card overflow-hidden">
      <div className="flex items-center gap-3 px-5 py-3.5" style={{ borderBottom: '1px solid var(--color-border)' }}>
        <span className="section-label">{title}</span>
        <span className="rounded-full px-1.5 font-bold" style={{ background: 'var(--color-border)', fontSize: 10 }}>{files.length}</span>
        {subtitle && (
          <span style={{ fontSize: 'var(--text-label)', color: 'var(--color-text-muted)' }}>{subtitle}</span>
        )}
        <button className="btn btn-ghost btn-sm ml-auto" title="Muat ulang" onClick={() => refresh()}>
          <Icon name="refresh" size={13} />
        </button>
      </div>
      {listErr && (
        <div className="flex items-center gap-2.5 px-5 py-2.5"
          style={{ borderBottom: '1px solid var(--color-border)', color: 'var(--color-warning-fg)', fontSize: 'var(--text-xs)' }}>
          <Icon name="alertTriangle" size={14} />
          <span className="flex-1">{listErr}</span>
          <button className="btn btn-secondary btn-sm" onClick={() => refresh()}>Coba lagi</button>
        </div>
      )}
      <div className="flex flex-col gap-3 px-5 py-4">
      <div style={{ fontSize: 'var(--text-label)', color: 'var(--color-text-muted)' }}>
        {loading ? 'Memuat…' : `${files.length} file PDF di OUTPUT siap kirim`}
      </div>
      <div className="flex flex-col gap-1.5 overflow-y-auto" style={{ maxHeight: 380 }}>
        {files.length > 0 && (
          // Baris master bahasa sama dengan baris file — logika checked/onChange tidak berubah
          <label className="flex items-center gap-2 rounded-lg px-2.5 py-2 cursor-pointer"
            style={{ fontSize: 'var(--text-xs)', color: 'var(--color-text-muted)', background: 'var(--color-bg)', border: '1px solid var(--color-border)' }}>
            <input type="checkbox" checked={sel.size === files.length && files.length > 0} onChange={toggleAll} />
            Pilih semua
            <span className="rounded-full font-mono font-semibold shrink-0" style={{ background: 'var(--color-border)', color: 'var(--color-text-secondary)', fontSize: 10, padding: '1px 8px' }}>{sel.size}/{files.length}</span>
          </label>
        )}
        {files.map((f, i) => (
          // Animasi masuk saat mount via utilitas yang ada; key stabil agar centang hanya ubah warna tanpa mengulang animasi
          <label key={f.name} className="animate-fade-rise flex items-center gap-2 rounded-lg px-2.5 py-2 cursor-pointer"
            style={{ animationDelay: `${Math.min(i * 30, 300)}ms`, background: sel.has(f.name) ? 'var(--color-nav-active)' : 'var(--color-bg)', border: sel.has(f.name) ? '1px solid var(--color-accent-ghost)' : '1px solid var(--color-border)' }}>
            <input type="checkbox" checked={sel.has(f.name)} onChange={() => toggle(f.name)} />
            <span className="font-mono truncate flex-1 hover:underline" title={f.name} style={{ fontSize: 'var(--text-xs)', cursor: 'pointer' }} onClick={() => onPreview?.(f)}>{f.name}</span>
            <span className="font-mono rounded-full shrink-0" style={{ fontSize: 10, color: 'var(--color-text-dim)', background: 'var(--color-panel)', border: '1px solid var(--color-border)', padding: '1px 8px' }}>{fmtSize(f.size)}</span>
          </label>
        ))}
        {files.length === 0 && !loading && (
          <div style={{ fontSize: 'var(--text-xs)', color: 'var(--color-text-dim)' }}>
            {connected ? 'Output kosong' : 'Hub offline'}
          </div>
        )}
      </div>
      {sel.size > 0 ? (
        // Bar batch hanya muncul saat ada pilihan — animasi sekali saat mount via utilitas yang ada
        <div className="animate-fade-rise flex flex-wrap items-center gap-2.5" style={{ background: 'var(--color-nav-active)', border: '1px solid var(--color-border)', borderRadius: 10, padding: '12px 14px' }}>
          <span className="rounded-full font-semibold shrink-0" style={{ background: 'var(--color-accent-ghost)', color: 'var(--color-accent)', fontSize: 'var(--text-xs)', padding: '3px 10px' }}>{sel.size} dipilih</span>
          <div className="flex gap-1.5 flex-wrap items-center">
            <span className="section-label">Operator</span>
            {['Nanas', 'Eric', 'Rahmadi'].map(op => (
              <button key={op} className="chip"
                onClick={() => pickOperator(op)}
                style={{
                  background: operator === op ? 'var(--color-nav-active)' : 'var(--color-panel)',
                  color:      operator === op ? 'var(--color-accent)'     : 'var(--color-text-secondary)',
                  border: `1px solid ${operator === op ? 'var(--color-accent-ghost)' : 'var(--color-border)'}`,
                }}>{op}</button>
            ))}
          </div>
          <div className="flex items-center gap-2 ml-auto">
            <button className="btn btn-primary btn-sm" onClick={() => doMove('develop')}>
              <Icon name="send" size={13} /> DEVELOP
            </button>
            <button className="btn btn-secondary btn-sm" onClick={() => doMove('ricoh')}>
              <Icon name="send" size={13} /> RICOH
            </button>
            <button className="btn btn-secondary btn-sm" onClick={openView}>
              <Icon name="eye" size={13} /> VIEW
            </button>
            <button className="btn btn-ghost btn-sm" title="Batalkan pilihan" onClick={() => setSel(new Set())}>
              <Icon name="x" size={13} />
            </button>
          </div>
        </div>
      ) : (
        // Petunjuk ramping saat belum ada pilihan agar layout tidak lompat
        <div style={{ fontSize: 10, color: 'var(--color-text-dim)', padding: '2px 0' }}>Pilih file untuk memindah batch…</div>
      )}
      <div style={{ fontSize: 10, color: 'var(--color-text-dim)' }}>ke folder DEVELOP / RICOH (setelan KMA) • tercatat + Selesai di .54</div>
      </div>
      {viewOpen && viewNames.length > 0 && (
        <ViewModal names={viewNames} idx={Math.min(viewIdx, viewNames.length - 1)}
          onIdx={setViewIdx} pageMap={viewPages}
          onPage={(nm, p) => setViewPages(prev => ({ ...prev, [nm]: p }))}
          imgTs={imgTs} onEdited={() => setImgTs(t => t + 1)} onClose={closeView} />
      )}
    </div>
  )
}

// Parser spesifikasi halaman "3" / "2-5" / "1,3,5" (+ kombinasi "1,3-5").
// Hasil dedupe + sort; semua pesan error Bahasa Indonesia.
function parsePageSpec(raw: string, total: number): { ok: true; pages: number[] } | { ok: false; err: string } {
  const t = (raw || '').trim()
  if (!t) return { ok: false, err: 'Isi nomor halaman dulu (mis. 3 atau 2-5).' }
  const out = new Set<number>()
  for (const tok of t.split(',')) {
    const s = tok.trim()
    if (!s) continue
    if (s.includes('-')) {
      const parts = s.split('-').map(p => p.trim())
      if (parts.length !== 2 || !parts[0] || !parts[1]) return { ok: false, err: `Rentang tak valid: "${s}". Contoh: 2-5.` }
      const a = Number(parts[0]); const b = Number(parts[1])
      if (!Number.isInteger(a) || !Number.isInteger(b)) return { ok: false, err: `Rentang tak valid: "${s}". Pakai angka bulat.` }
      if (a > b) return { ok: false, err: `Rentang terbalik: "${s}". Tulis kecil-besar (mis. 2-5).` }
      if (a < 1 || b > total) return { ok: false, err: `Halaman harus 1..${total}.` }
      for (let p = a; p <= b; p++) out.add(p)
    } else {
      const n = Number(s)
      if (!Number.isInteger(n)) return { ok: false, err: `"${s}" bukan nomor halaman. Contoh: 3 atau 2-5.` }
      if (n < 1 || n > total) return { ok: false, err: `Halaman harus 1..${total}.` }
      out.add(n)
    }
  }
  if (out.size === 0) return { ok: false, err: 'Isi nomor halaman dulu (mis. 3 atau 2-5).' }
  return { ok: true, pages: [...out].sort((a, b) => a - b) }
}

// VIEW modal NSH Lite: 1 blok card (~90vw/maks 1200px, ~85vh) berisi 3 panel —
// kiri viewer besar (raster /api/output-page + &t= cache-buster), kanan-atas
// daftar file terpilih (klik = ganti file; kunjungan pertama = hal. 1, kunjungan
// ulang = ingat halaman terakhir via pageMap), kanan-bawah tools BW/Hapus Halaman.
// Klik backdrop / X / ESC = tutup. Tanpa CSS baru (card/btn/input/chip/section-label
// + animate-fade-rise saja); z-index 9000 di bawah toast (9999) agar toast tetap di atas.
function ViewModal({ names, idx, onIdx, pageMap, onPage, imgTs, onEdited, onClose }: {
  names: string[]
  idx: number
  onIdx: (i: number) => void
  pageMap: Record<string, number>
  onPage: (name: string, page: number) => void
  imgTs: number
  onEdited: () => void
  onClose: () => void
}) {
  const [tool, setTool] = useState<null | 'bw' | 'del'>(null)
  const [spec, setSpec] = useState('')
  const [specErr, setSpecErr] = useState('')
  const [busy, setBusy] = useState(false)
  const [infos, setInfos] = useState<Record<string, OutputInfo>>({})
  const [fails, setFails] = useState<Set<string>>(new Set())
  const [imgFail, setImgFail] = useState(false)
  const hubHttp = (() => {
    try { return `http://${location.hostname}:8766` } catch { return 'http://localhost:8766' }
  })()
  const cur = names[idx] ?? ''
  const info = cur ? infos[cur] : undefined
  const total = info && info.pages > 0 ? info.pages : 0
  const page = Math.max(0, pageMap[cur] ?? 0)
  // Aturan tombol sama seperti PreviewPanel: 1 halaman → sembunyi dua-duanya;
  // halaman pertama → sembunyi Back; halaman terakhir → sembunyi Next.
  const showBack = total <= 0 ? true : total > 1 && page > 0
  const showNext = total <= 0 ? true : total > 1 && page < total - 1
  const imgSrc = cur && !imgFail ? `${hubHttp}/api/output-page?name=${encodeURIComponent(cur)}&page=${page + 1}&t=${imgTs}` : null

  // Jumlah halaman per file: paralel saat modal dibuka, aman-batal bila ditutup cepat.
  useEffect(() => {
    let cancelled = false
    const tok = getAuthToken()
    setInfos({})
    setFails(new Set())
    names.forEach(nm => {
      fetch(`${hubHttp}/api/output-info?name=${encodeURIComponent(nm)}`, {
        headers: tok ? { Authorization: `Bearer ${tok}` } : undefined,
      }).then(r => r.json()).then(j => {
        if (cancelled) return
        if (j && j.ok && typeof j.pages === 'number' && j.pages > 0) {
          setInfos(p => ({ ...p, [nm]: { pages: j.pages, width_pt: Number(j.width_pt) || 0, height_pt: Number(j.height_pt) || 0, size: Number(j.size) || 0 } }))
        } else {
          setFails(p => new Set(p).add(nm))
        }
      }).catch(() => { if (!cancelled) setFails(p => new Set(p).add(nm)) })
    })
    return () => { cancelled = true }
  }, [hubHttp, names])

  // ESC = tutup (listener + cleanup sepele).
  useEffect(() => {
    const h = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose() }
    window.addEventListener('keydown', h)
    return () => window.removeEventListener('keydown', h)
  }, [onClose])

  // Ganti file/halaman/edit baru → coba muat gambar lagi.
  useEffect(() => { setImgFail(false) }, [cur, page, imgTs])

  const reloadInfo = (nm: string, after?: (n: number) => void) => {
    const tok = getAuthToken()
    fetch(`${hubHttp}/api/output-info?name=${encodeURIComponent(nm)}`, {
      headers: tok ? { Authorization: `Bearer ${tok}` } : undefined,
    }).then(r => r.json()).then(j => {
      if (j && j.ok && typeof j.pages === 'number' && j.pages > 0) {
        setInfos(p => ({ ...p, [nm]: { pages: j.pages, width_pt: Number(j.width_pt) || 0, height_pt: Number(j.height_pt) || 0, size: Number(j.size) || 0 } }))
        setFails(p => { const s = new Set(p); s.delete(nm); return s })
        after?.(j.pages)
      } else {
        setFails(p => new Set(p).add(nm))
        after?.(0)
      }
    }).catch(() => { setFails(p => new Set(p).add(nm)); after?.(0) })
  }

  const applyTool = async () => {
    if (!cur || busy || !tool) return
    const parsed = parsePageSpec(spec, total)
    if (!parsed.ok) { setSpecErr(parsed.err); return }
    if (tool === 'del' && parsed.pages.length >= total) {
      setSpecErr('Tidak bisa hapus semua halaman.')
      return
    }
    setSpecErr('')
    setBusy(true)
    try {
      const tok = getAuthToken()
      const ep = tool === 'bw' ? 'output-bw' : 'output-delpages'
      const r = await fetch(`${hubHttp}/api/${ep}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', ...(tok ? { Authorization: `Bearer ${tok}` } : {}) },
        body: JSON.stringify({ name: cur, pages: parsed.pages }),
      })
      const j = await r.json().catch(() => null)
      if (r.ok && j && j.ok) {
        pushToast(String(j.msg || 'Berhasil'), 'success')
        setSpec('')
        setTool(null)
        onEdited()
        if (tool === 'del') {
          reloadInfo(cur, np => { if (np > 0 && page > np - 1) onPage(cur, np - 1) })
        } else {
          reloadInfo(cur)
        }
      } else {
        const m = (j && j.msg) || `HTTP ${r.status}`
        pushToast(`Gagal: ${m}`, 'danger')
        setSpecErr(String(m))
      }
    } catch (e: unknown) {
      pushToast(`Gagal: ${e instanceof Error ? e.message : String(e)}`, 'danger')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div onClick={onClose} style={{ position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.6)', zIndex: 9000, display: 'flex', alignItems: 'center', justifyContent: 'center', padding: 20 }}>
      <div onClick={e => e.stopPropagation()} className="card animate-fade-rise" style={{ width: '90vw', maxWidth: 1200, height: '85vh', maxHeight: '85vh', display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>
        <div className="flex items-center gap-3 px-5 py-3.5" style={{ borderBottom: '1px solid var(--color-border)' }}>
          <span className="section-label truncate">VIEW — {cur} ({idx + 1}/{names.length})</span>
          <button className="btn btn-ghost btn-sm ml-auto" title="Tutup" onClick={onClose}>
            <Icon name="x" size={14} />
          </button>
        </div>
        <div className="grid gap-3 p-4" style={{ gridTemplateColumns: '1fr 300px', flex: 1, minHeight: 0 }}>
          <div className="flex flex-col gap-2" style={{ minHeight: 0 }}>
            <div style={{ background: 'var(--color-bg)', border: '1px solid var(--color-border)', overflow: 'hidden', flex: 1, minHeight: 0, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
              {imgSrc ? (
                <img key={imgSrc} src={imgSrc} alt={cur} onError={() => setImgFail(true)}
                  style={{ width: '100%', height: '100%', objectFit: 'contain', display: 'block' }} />
              ) : (
                <div style={{ fontSize: 'var(--text-sm)', color: 'var(--color-text-dim)', padding: 24, textAlign: 'center' }}>
                  {cur ? 'Gagal memuat preview' : 'Tidak ada file'}
                </div>
              )}
            </div>
            <div className="flex items-center justify-between">
              {showBack ? (
                <button className="btn btn-ghost btn-sm" disabled={page === 0} onClick={() => onPage(cur, Math.max(0, page - 1))}>
                  <Icon name="chevronLeft" size={14} /> Back
                </button>
              ) : <span />}
              <span style={{ fontSize: 'var(--text-xs)', color: 'var(--color-text-muted)' }}>Page {page + 1}{total > 0 ? ` / ${total}` : ''}</span>
              {showNext ? (
                <button className="btn btn-ghost btn-sm" onClick={() => onPage(cur, total > 0 ? Math.min(total - 1, page + 1) : page + 1)}>
                  Next <Icon name="chevronRight" size={14} />
                </button>
              ) : <span />}
            </div>
          </div>
          <div className="grid gap-3" style={{ gridTemplateRows: 'minmax(0,1fr) auto', minHeight: 0 }}>
            <div className="flex flex-col gap-1.5 overflow-y-auto" style={{ minHeight: 0 }}>
              <div className="section-label">File dipilih ({names.length})</div>
              {names.map((nm, i) => {
                const inf = infos[nm]
                const fail = fails.has(nm)
                const active = i === idx
                return (
                  <button key={nm} onClick={() => onIdx(i)} className="flex items-center gap-2 rounded-lg px-2.5 py-2 text-left"
                    style={{ fontSize: 'var(--text-xs)', background: active ? 'var(--color-nav-active)' : 'var(--color-bg)', border: active ? '1px solid var(--color-accent-ghost)' : '1px solid var(--color-border)', color: 'var(--color-text-secondary)', cursor: 'pointer' }}>
                    <span className="font-mono truncate flex-1" title={nm}>{nm}</span>
                    <span className="font-mono shrink-0" style={{ fontSize: 10, color: 'var(--color-text-dim)' }}>{inf ? `${inf.pages} hlm` : fail ? '?' : '…'}</span>
                  </button>
                )
              })}
            </div>
            <div>
              <div className="section-label mb-1">Tools</div>
              {tool === null ? (
                <div className="flex flex-col gap-2">
                  <div className="flex gap-2">
                    <button className="btn btn-secondary btn-sm" onClick={() => { setTool('bw'); setSpec(''); setSpecErr('') }}>BW</button>
                    <button className="btn btn-secondary btn-sm" onClick={() => { setTool('del'); setSpec(''); setSpecErr('') }}>Hapus Halaman</button>
                  </div>
                  <div style={{ fontSize: 10, color: 'var(--color-text-dim)' }}>Edit sementara sebelum kirim</div>
                </div>
              ) : (
                <div className="flex flex-col gap-2 rounded-lg px-2.5 py-2" style={{ background: 'var(--color-bg)', border: '1px solid var(--color-border)' }}>
                  <div className="section-label">{tool === 'bw' ? 'BW — halaman jadi hitam-putih' : 'Hapus halaman'}</div>
                  <input className="input font-mono" value={spec} onChange={e => setSpec(e.target.value)}
                    onKeyDown={e => { if (e.key === 'Enter') applyTool() }}
                    placeholder="Halaman: 3 atau 2-5" spellCheck={false} disabled={busy}
                    style={{ fontSize: 'var(--text-xs)' }} />
                  {specErr && <div style={{ fontSize: 10, color: 'var(--color-danger-fg)' }}>{specErr}</div>}
                  <div className="flex gap-2">
                    <button className="btn btn-primary btn-sm" onClick={applyTool} disabled={busy}>{busy ? 'Memproses…' : 'Terapkan'}</button>
                    <button className="btn btn-ghost btn-sm" onClick={() => { setTool(null); setSpec(''); setSpecErr('') }} disabled={busy}>Batal</button>
                  </div>
                </div>
              )}
            </div>
          </div>
        </div>
      </div>
    </div>
  )
}

// Modul Lite belum ada backend — antrean kosong sampai ada sumber data
// (mock Lite dihapus — blok Hasil Impose live dari OUTPUT)

const PageNshLite = ({ connected, sendCommand, results, outputFiles }: {
  connected: boolean
  sendCommand: (action: string, opts?: SendCommandOpts) => string | null
  results: CommandResult[]
  outputFiles: OutputFile[]
}) => {
  const folders = useModuleFolders('lite', connected, sendCommand, results)
  const [previewFile, setPreviewFile] = useState<OutputFile | null>(null)
  const flagOn = (k: string) => (folders.values[k] ?? '1') !== '0'
  const setFlag = (k: string, on: boolean) => {
    const next = { ...folders.values, [k]: on ? '1' : '0' }
    folders.setValues(next)
    folders.save(next)
  }

  return (
    <div className="flex flex-col gap-4">
      {!connected && (
        <div className="flex items-center gap-2.5 rounded-[10px] px-4 py-2.5"
          style={{ background: 'var(--color-panel)', border: '1px solid var(--color-warning-ghost)', fontSize: 'var(--text-sm)', color: 'var(--color-warning-fg)' }}>
          <Icon name="alertTriangle" size={16} />
          <span className="font-semibold">Data referensi</span>
          <span style={{ color: 'var(--color-text-secondary)' }}>— modul Lite belum ada backend</span>
        </div>
      )}
      {/* Otomatisasi Lite */}
      <div className="card px-5 py-4">
        <SectionHeader label="Otomatisasi" />
        <div className="flex gap-2.5 flex-wrap">
          {[
            { key: 'auto_impose', label: 'Impose Otomatis' },
            { key: 'copy_kisscut', label: 'Copy Kisscut/Diecut' },
          ].map(t => {
            const on = flagOn(t.key)
            return (
              <button key={t.key} className="btn btn-sm"
                onClick={() => setFlag(t.key, !on)}
                style={{
                  background: on ? 'var(--color-success-ghost)' : 'var(--color-panel)',
                  color: on ? 'var(--color-success-fg)' : 'var(--color-text-dim)',
                  border: '1px solid var(--color-border)',
                }}>
                <span className="rounded-full" style={{
                  width: 8, height: 8,
                  background: on ? 'var(--color-success)' : 'var(--color-text-dim)',
                }} />
                {t.label}: {on ? 'ON' : 'OFF'}
              </button>
            )
          })}
        </div>
        <div style={{ fontSize: 10, color: 'var(--color-text-dim)', marginTop: 6 }}>
          OFF = file dibiarkan di INPUT (tidak di-impose / tidak di-copy) • Lite restart otomatis ±30 dtk
        </div>
      </div>
      {/* Path folder (read-only, otomatis WE) */}
      <PathView
        fields={[
          { key: 'input_folder', label: 'INPUT' },
          { key: 'output_folder', label: 'OUTPUT' },
          { key: 'uda_folder', label: 'UDA' },
        ]}
        values={folders.values}
      />
      <div className="grid gap-4" style={{ gridTemplateColumns: '1fr 360px' }}>
        <OutputWatcher connected={connected} sendCommand={sendCommand} results={results} files={outputFiles}
          title="Hasil Impose" subtitle="File di OUTPUT siap kirim ke DEVELOP / RICOH" onPreview={setPreviewFile} />
        <PreviewPanel file={previewFile} />
      </div>
    </div>
  )
}

// Preview Panel untuk NSH Lite — 1 halaman, tanpa scroll, Next/Back Page 1-2, fit, tanpa rounded
type OutputInfo = { pages: number; width_pt: number; height_pt: number; size: number }

function PreviewPanel({ file }: { file: OutputFile | null }) {
  const [page, setPage] = useState(0)
  const [info, setInfo] = useState<OutputInfo | null>(null)
  const [imgFail, setImgFail] = useState(false)
  const hubHttp = (() => {
    try { return `http://${location.hostname}:8766` } catch { return 'http://localhost:8766' }
  })()
  useEffect(() => {
    setPage(0)
    setInfo(null)
    setImgFail(false)
    const fname = file?.name
    if (!fname) return
    let cancelled = false
    const tok = getAuthToken()
    fetch(`${hubHttp}/api/output-info?name=${encodeURIComponent(fname)}`, {
      headers: tok ? { Authorization: `Bearer ${tok}` } : undefined,
    }).then(r => r.json()).then(j => {
      if (cancelled) return
      if (j && j.ok && typeof j.pages === 'number' && j.pages > 0) {
        setInfo({ pages: j.pages, width_pt: Number(j.width_pt) || 0, height_pt: Number(j.height_pt) || 0, size: Number(j.size) || 0 })
        setPage(p => Math.max(0, Math.min(p, j.pages - 1)))
      } else {
        setInfo(null)
      }
    }).catch(() => { if (!cancelled) setInfo(null) })
    return () => { cancelled = true }
  }, [file?.name, hubHttp])
  const pages = info && info.pages > 0 ? info.pages : 0
  const dimsKnown = !!info && info.width_pt > 0 && info.height_pt > 0
  // Aturan tombol: 1 halaman → sembunyi dua-duanya; halaman pertama → sembunyi Back;
  // halaman terakhir → sembunyi Next; tak diketahui → perilaku lama (dua-duanya tampil).
  const showBack = pages <= 0 ? true : pages > 1 && page > 0
  const showNext = pages <= 0 ? true : pages > 1 && page < pages - 1
  const pdfSrc = file ? `${hubHttp}/api/output-file?name=${encodeURIComponent(file.name)}#page=${page + 1}` : null
  // Render 1 halaman jadi gambar via /api/output-page: <img> tidak punya scroll
  // (iframe memakai PDF viewer browser yg selalu bisa di-scroll). Cookie auth
  // ikut terkirim otomatis seperti iframe. Gagal (hub lama) → fallback iframe.
  const imgSrc = file && !imgFail ? `${hubHttp}/api/output-page?name=${encodeURIComponent(file.name)}&page=${page + 1}` : null
  return (
    <div className="card p-5 flex flex-col gap-3 h-fit">
      <SectionHeader label="Preview Panel" />
      {!file ? (
        <div style={{ fontSize: 'var(--text-sm)', color: 'var(--color-text-dim)', padding: '24px 0', textAlign: 'center' }}>
          Klik file di Hasil Impose untuk preview
        </div>
      ) : (
        <>
          <div className="font-mono truncate" title={file.name} style={{ fontSize: 'var(--text-xs)', color: 'var(--color-accent)' }}>{file.name}</div>
          <div style={dimsKnown && info
            ? { background: 'var(--color-bg)', border: '1px solid var(--color-border)', overflow: 'hidden', width: '100%', aspectRatio: `${info.width_pt} / ${info.height_pt}`, maxHeight: 560, margin: 'auto' }
            : { background: 'var(--color-bg)', border: '1px solid var(--color-border)', height: 520, overflow: 'hidden' }}>
            {imgSrc ? (
              <img key={imgSrc} src={imgSrc} alt={file.name} onError={() => setImgFail(true)}
                style={{ width: '100%', height: '100%', border: 'none', objectFit: 'contain', display: 'block' }} />
            ) : (
              <iframe key={pdfSrc ?? ''} src={pdfSrc ?? ''} title={file.name} style={{ width: '100%', height: '100%', border: 'none' }} />
            )}
          </div>
          <div className="flex items-center justify-between">
            {showBack ? (
              <button className="btn btn-ghost btn-sm" disabled={page === 0} onClick={() => setPage(p => Math.max(0, p - 1))}>
                <Icon name="chevronLeft" size={14} /> Back
              </button>
            ) : <span />}
            <span style={{ fontSize: 'var(--text-xs)', color: 'var(--color-text-muted)' }}>Page {page + 1}{pages > 0 ? ` / ${pages}` : ''}</span>
            {showNext ? (
              <button className="btn btn-ghost btn-sm" onClick={() => setPage(p => pages > 0 ? Math.min(pages - 1, p + 1) : p + 1)}>
                Next <Icon name="chevronRight" size={14} />
              </button>
            ) : <span />}
          </div>
        </>
      )}
    </div>
  )
}

// ─── Page: KMA ────────────────────────────────────────────────────────────────

function kmaStatus(status: string): { label: string; level: StatusLevel } {
  if (status === 'ok' || status === 'done') return { label: 'SELESAI', level: 'success' }
  if (status === 'return') return { label: 'SKIP', level: 'violet' }
  if (status === 'error') return { label: 'GAGAL', level: 'danger' }
  if (status === 'ricoh') return { label: 'RICOH', level: 'accent' }
  return { label: 'PENDING', level: 'warning' }
}

const PageKma = ({ connected, routes, printers, rules, stats, sendCommand, results }: {
  connected: boolean
  routes: KmaRoute[]
  printers: KmaPrinter[]
  rules: KmaRule[]
  stats: { ok?: number; ret?: number; done?: number }
  sendCommand: (action: string, opts?: SendCommandOpts) => string | null
  results: CommandResult[]
}) => {
  const folders = useModuleFolders('kma', connected, sendCommand, results)
  if (!connected) return (
    <EmptyState icon="wifiOff" title="Hub Offline" description="Jalankan route.py --hub untuk mengaktifkan modul KMA Router" />
  )

  const files = [...routes].reverse().map((r, i) => ({ ...r, ...kmaStatus(r.status), key: `${r.file}||${r.ts}||${i}` }))

  return (
    <div className="flex flex-col gap-4">
      <PathView
        fields={[
          { key: 'input_folder', label: 'INPUT' },
          { key: 'return_folder', label: 'RETURN' },
          { key: 'uda_folder', label: 'UDA' },
          { key: 'ricoh_folder', label: 'RICOH' },
        ]}
        values={folders.values}
      />
      {/* Status printer — di atas Daftar File, 3 blok bersebelahan */}
      <div className="card p-5">
        <SectionHeader label="Status Printer" />
          <div className="grid gap-3" style={{ gridTemplateColumns: 'repeat(3, 1fr)' }}>
            {printers.length === 0 && (
              <div style={{ fontSize: 'var(--text-xs)', color: 'var(--color-text-dim)' }}>Menunggu status printer…</div>
            )}
            {printers.map((p, i) => {
              const ready = p.status === 'siap'
              const unknown = p.status !== 'siap' && p.status !== 'mati'
              const lvl: StatusLevel = ready ? 'success' : unknown ? 'muted' : 'danger'
              const isRicoh = p.ip === 'RICOH'
              const mCount = (st: string) => routes.filter(r => (r.machine ?? '') === (p.machine ?? '') && r.status === st).length
              return (
              <div key={i} className={`card card-accent-${lvl} px-4 py-3.5`}>
                <div className="flex items-center justify-between mb-2">
                  <div className="flex items-center gap-2">
                    <Icon name="printer" size={16} style={{ color: 'var(--color-text-secondary)' } as any} />
                    <span className="font-bold font-mono" style={{ fontSize: 'var(--text-base)' }}>{p.ip}</span>
                    <span className="section-label">{isRicoh ? 'WATCHER' : (p.machine || '')}</span>
                    {p.active && p.ip !== '192.168.10.158' && (
                      <span className="rounded font-bold"
                        style={{ fontSize: 10, background: 'var(--color-success-ghost)', color: 'var(--color-success-fg)', padding: '2px 6px' }}>
                        AKTIF
                      </span>
                    )}
                  </div>
                  <Badge label={ready ? 'SIAP' : unknown ? 'OFF' : 'MATI'} status={lvl} />
                </div>
                <div className="flex gap-4">
                  {isRicoh ? (
                    <div>
                      <div className="section-label mb-0.5">FILE</div>
                      <span className="font-mono font-bold" style={{ fontSize: 'var(--text-num-sm)', color: 'var(--color-accent)' }}>
                        {mCount('ricoh').toLocaleString()}
                      </span>
                    </div>
                  ) : (
                    [['OK', mCount('ok') + mCount('done'), 'var(--color-success)'], ['RET', mCount('return'), 'var(--color-warning)'], ['ERR', mCount('error'), 'var(--color-danger)']].map(([k, v, c]) => (
                    <div key={k as string}>
                      <div className="section-label mb-0.5">{k}</div>
                      <span className="font-mono font-bold" style={{ fontSize: 'var(--text-num-sm)', color: c as string }}>
                        {Number(v).toLocaleString()}
                      </span>
                    </div>
                    ))
                  )}
                </div>
              </div>
              )
            })}
          </div>
        </div>
      {/* File list */}
      <div className="card overflow-hidden">
        <div className="px-5 py-3.5" style={{ borderBottom: '1px solid var(--color-border)' }}>
          <SectionHeader label="Daftar File Router" />
        </div>
        <div className="overflow-x-auto">
          <table className="data-table">
            <thead>
              <tr>
                <th>Nama File</th><th>Bahan</th><th>Hotfolder</th><th>Waktu</th><th>Status</th><th>Mesin</th>
              </tr>
            </thead>
            <tbody>
              {files.length === 0 && (
                <tr><td colSpan={6}>
                  <EmptyState icon="router" title="Belum ada file" description="Router belum memproses file apa pun sesi ini" />
                </td></tr>
              )}
              {files.map((f) => (
                <tr key={f.key}>
                  <td><span className="font-mono" style={{ fontSize: 'var(--text-xs)', color: 'var(--color-accent-fg)' }}>{f.file}</span></td>
                  <td><CategoryBadge label={f.category} /></td>
                  <td><span className="font-mono" style={{ fontSize: 'var(--text-xs)', color: 'var(--color-text-secondary)' }}>→ {f.dest}</span></td>
                  <td><span className="font-mono" style={{ fontSize: 'var(--text-xs)', color: 'var(--color-text-muted)' }}>{f.ts}</span></td>
                  <td><Badge label={f.label} status={f.level} /></td>
                  <td><span className="font-mono font-semibold" style={{ fontSize: 'var(--text-xs)', color: 'var(--color-accent-fg)' }}>{f.machine ?? '—'}</span></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  )
}

// ─── Page: WE (WisdomEye) ───────────────────────────────────────────────────
// Struktur klon PageWma: bar monitoring read-only + 3 rak (Seen/Printed/Verified).
// Aksi tulis (Rescan, Auto/Manual) admin-only via HTTP Bearer; baca boleh semua user.
// Perintah WS tidak dipakai di sini (sendCommand hanya diteruskan agar seragam).

// Kunci baris WE = 6-tuple spt rkey agar cocok dgn skey verified hub.
function weSkey(f: WeFile): string {
  return `${f.nota ?? ''}||${f.bahan ?? ''}||${f.sides ?? ''}||${f.laminating ?? ''}||${f.test ? '1' : '0'}||${f.process_type ?? ''}`
}

const WE_DAY = ['Min', 'Sen', 'Sel', 'Rab', 'Kam', 'Jum', 'Sab']
const WE_MON = ['Jan', 'Feb', 'Mar', 'Apr', 'Mei', 'Jun', 'Jul', 'Agu', 'Sep', 'Okt', 'Nov', 'Des']

const PageWe = ({ live, sendCommand, results, weSeen = [], wePrinted = [], weVerified = [], weAuto = null, hubUrl, weRefresh }: {
  live: boolean
  sendCommand: (action: string, opts?: SendCommandOpts) => string | null
  results: CommandResult[]
  weSeen: WeFile[]
  wePrinted: WeFile[]
  weVerified: string[]
  weAuto: WeAuto | null
  hubUrl: string
  weRefresh: () => Promise<boolean>
}) => {
  const isAdmin = getAuthRole() === 'admin'
  const base = hubHttpBase(hubUrl)
  const [q, setQ] = useState('')
  const [refreshing, setRefreshing] = useState(false)
  const [busy, setBusy] = useState(false)
  // aksi per-baris: filename -> 'printed' | 'delete' | 'verify' (tombol ybs disabled)
  const [acting, setActing] = useState<Record<string, string>>({})
  // aksi per-baris: filename -> 'verify' (tombol verify batch; hasil via pendingRef).
  // Batch: seleksi multi-baris + blok aksi mengambang (fixed, di bawah).
  // Operator/mesin meniru pola OutputWatcher ('nsh-operator') dgn kunci sendiri
  // 'we-operator'/'we-machine' agar tidak saling timpa.
  const [sel, setSel] = useState<Set<string>>(new Set())
  const [batchBusy, setBatchBusy] = useState<null | 'printed' | 'verify' | 'hapus'>(null)
  const [confirmBatchHapus, setConfirmBatchHapus] = useState(false)
  const [weOperator, setWeOperator] = useState(() => {
    try { return localStorage.getItem('we-operator') || '' } catch { return '' }
  })
  const [weMachine, setWeMachine] = useState(() => {
    try { return localStorage.getItem('we-machine') || 'Develop 1' } catch { return 'Develop 1' }
  })
  const pickWeOperator = (v: string) => {
    setWeOperator(v)
    try { localStorage.setItem('we-operator', v) } catch { /* abaikan */ }
  }
  const pickWeMachine = (v: string) => {
    setWeMachine(v)
    try { localStorage.setItem('we-machine', v) } catch { /* abaikan */ }
  }
  // Batal/X: hanya bersihkan pilihan (+ status konfirmasi), tanpa server call.
  const clearSel = () => { setSel(new Set()); setConfirmBatchHapus(false) }
  const pendingRef = useRef<Set<string>>(new Set())
  // peta id perintah verify -> filename (agar hasil WS membuka lagi tepat 1 baris)
  const verifyFileRef = useRef<Map<string, string>>(new Map())
  // Cap waktu data WE terakhir berubah (utk penanda umur data + deteksi basi).
  const [weUpdatedAt, setWeUpdatedAt] = useState<number>(0)
  useEffect(() => {
    setWeUpdatedAt(Date.now())
  }, [weSeen, wePrinted, weVerified])
  // Sembuh-sendiri: refresh HTTP tiap 30 dtk saat live, agar tampilan konvergen
  // walau ada pesan WS yg hilang. Interval dibersihkan saat unmount/offline.
  useEffect(() => {
    if (!live) return
    const t = setInterval(() => { weRefresh().catch(() => undefined) }, 30000)
    return () => clearInterval(t)
  }, [live, weRefresh])

  useEffect(() => {
    for (const r of results ?? []) {
      if (!pendingRef.current.has(r.id)) continue
      pendingRef.current.delete(r.id)
      if (r.action === 'verify') {
        const fn = verifyFileRef.current.get(r.id)
        verifyFileRef.current.delete(r.id)
        if (fn) {
          setActing(p => {
            if (p[fn] !== 'verify') return p
            const n = { ...p }
            delete n[fn]
            return n
          })
        }
      }
      pushToast(r.ok ? `OK: ${r.action} — ${r.msg}` : `Gagal: ${r.action} — ${r.msg}`, r.ok ? 'success' : 'danger')
    }
  }, [results])

  const authHdr = () => {
    const tok = getAuthToken()
    return tok ? { Authorization: `Bearer ${tok}` } : undefined
  }

  const doManualRefresh = async () => {
    if (!live) { pushToast('Hub offline — menampilkan data WE terakhir', 'warning'); return }
    setRefreshing(true)
    const ok = await weRefresh()
    setRefreshing(false)
    pushToast(ok ? 'State WE dimuat ulang' : 'Gagal muat ulang state WE', ok ? 'success' : 'danger')
  }

  const doRescan = async () => {
    if (!live) { pushToast('Hub offline — perintah tidak dikirim', 'warning'); return }
    setBusy(true)
    try {
      const r = await fetch(`${base}/api/we/rescan`, { method: 'POST', headers: authHdr() })
      const j = await r.json().catch(() => null)
      if (r.ok && j && j.ok) {
        pushToast(`Rescan OK: ${j.seen ?? '?'} seen, ${j.printed ?? '?'} printed, ${j.verified ?? '?'} verified`, 'success')
        await weRefresh()
      } else {
        pushToast(`Rescan gagal: ${(j && j.msg) || `HTTP ${r.status}`}`, 'danger')
      }
    } catch (e: unknown) {
      pushToast(`Rescan gagal: ${e instanceof Error ? e.message : String(e)}`, 'danger')
    } finally {
      setBusy(false)
    }
  }

  const isManual = !!weAuto?.override
  const doToggleAuto = async () => {
    if (!live) { pushToast('Hub offline — perintah tidak dikirim', 'warning'); return }
    const next = isManual ? 'auto' : 'manual'
    setBusy(true)
    try {
      const r = await fetch(`${base}/api/we/auto`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', ...(authHdr() || {}) },
        body: JSON.stringify({ mode: next }),
      })
      const j = await r.json().catch(() => null)
      if (r.ok && j && j.ok) {
        // tanpa optimistis lokal — konfirmasi final datang dari we_update berikutnya
        pushToast(`Mode ${next.toUpperCase()} dikirim — menunggu konfirmasi hub…`, 'accent')
      } else {
        pushToast(`Gagal ubah mode: ${(j && j.msg) || `HTTP ${r.status}`}`, 'danger')
      }
    } catch (e: unknown) {
      pushToast(`Gagal ubah mode: ${e instanceof Error ? e.message : String(e)}`, 'danger')
    } finally {
      setBusy(false)
    }
  }

  // Hapus file fisik per-baris DIHILANGKAN dari blok SEEN (keputusan UI);
  // endpoint DELETE /api/we/file tetap ada di hub utk tab Database (admin).

  // Status per-baris dari props (tanpa fetch baru):
  // verified = skey 6-tuple ada di weVerified; printed = filename ada di wePrinted.
  const verSet = new Set(weVerified)
  const printedSet = new Set((wePrinted ?? []).map(f => f.filename))
  // Mesin/Operator TERCATAT di tabel printed (moves/tombol manual), sedangkan
  // baris tampil dari tabel seen (tanpa kolom itu) — gabung di sini agar tulisan
  // berganti begitu user input dari NSH Lite / floating blok.
  const printedMap = new Map((wePrinted ?? []).map(p => [p.filename, p] as const))
  // Hitungan SEEN = menunggu cetak (belum printed); DB tetap riwayat penuh.
  const pendingCount = weSeen.filter(f => !printedSet.has(f.filename)).length
  // Urutan: terbaru dulu berdasar waktu file masuk (first_seen_ts desc);
  // tanpa ts dianggap paling lama. Deterministik, didokumentasikan di sini.
  const ordered = [...weSeen].sort((a, b) => (b.first_seen_ts || '').localeCompare(a.first_seen_ts || ''))

  const matchQ = (f: WeFile, s: string) => {
    if (!s) return true
    const t = s.toLowerCase()
    return f.filename.toLowerCase().includes(t)
      || (f.nota || '').toLowerCase().includes(t)
      || (f.customer || '').toLowerCase().includes(t)
      || (f.bahan || '').toLowerCase().includes(t)
  }
  const rows = ordered.filter(f => matchQ(f, q))
  // Seleksi bertahan saat cari (q); select-all hanya utk baris TERFILTER.
  const byName = new Map(weSeen.map(f => [f.filename, f]))
  const toggle = (fn: string) => setSel(p => { const s = new Set(p); if (s.has(fn)) s.delete(fn); else s.add(fn); return s })
  const toggleAll = () => setSel(p => {
    const all = rows.length > 0 && rows.every(f => p.has(f.filename))
    return all ? new Set() : new Set(rows.map(f => f.filename))
  })
  const allChecked = rows.length > 0 && rows.every(f => sel.has(f.filename))

  // Batch Printed: POST /api/we/mark-printed {filename, operator, machine} per file.
  // Server upsert idempoten — coba semua, rangkum hitungan ok/gagal via toast.
  const doBatchPrinted = async () => {
    if (!live) { pushToast('Hub offline — perintah tidak dikirim', 'warning'); return }
    if (sel.size === 0) { pushToast('Pilih file dulu', 'warning'); return }
    setBatchBusy('printed')
    let ok = 0, gagal = 0
    try {
      for (const fn of sel) {
        try {
          const r = await fetch(`${base}/api/we/mark-printed`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json', ...(authHdr() || {}) },
            body: JSON.stringify({ filename: fn, operator: weOperator, machine: weMachine }),
          })
          const j = await r.json().catch(() => null)
          if (r.ok && j && j.ok) ok++
          else gagal++
        } catch { gagal++ }
      }
      pushToast(gagal ? `${ok} printed, ${gagal} gagal` : `${ok} printed`, gagal ? 'danger' : 'success')
      await weRefresh()
    } finally {
      setBatchBusy(null)
    }
  }

  // Batch Verify: WS 'verify' 6-tuple per baris terpilih (jalur sama spt WMA);
  // yg sudah VERIFIED dilewati (dihitung di toast).
  const doBatchVerify = () => {
    if (!live) { pushToast('Hub offline — perintah tidak dikirim', 'warning'); return }
    if (sel.size === 0) { pushToast('Pilih file dulu', 'warning'); return }
    setBatchBusy('verify')
    try {
      let kirim = 0, lewati = 0
      for (const fn of sel) {
        const f = byName.get(fn)
        if (!f) { lewati++; continue }
        if (verSet.has(weSkey(f))) { lewati++; continue }
        const id = sendCommand('verify', { nota: f.nota ?? '', bahan: f.bahan ?? '', sides: f.sides ?? '', laminating: f.laminating ?? '', test: f.test ?? false, process_type: f.process_type ?? '' })
        if (!id) { pushToast('Hub offline — perintah tidak dikirim', 'warning'); break }
        pendingRef.current.add(id)
        verifyFileRef.current.set(id, f.filename)
        setActing(p => ({ ...p, [f.filename]: 'verify' }))
        kirim++
      }
      pushToast(lewati ? `${kirim} verify dikirim, ${lewati} dilewati (sudah verified)` : `${kirim} verify dikirim`, 'accent')
    } finally {
      setBatchBusy(null)
    }
  }

  // Batch Hapus: HANYA cabut tanda SEEN (DELETE /api/we/seen per file) — file di
  // disk TETAP ada; rescan memunculkan lagi bila berkas masih di MONITORING.
  // Dua-langkah inline spt pola baris; BUKAN /api/we/file (itu hapus disk, admin).
  const doBatchUnsee = async () => {
    if (!live) { pushToast('Hub offline — perintah tidak dikirim', 'warning'); return }
    if (sel.size === 0) { pushToast('Pilih file dulu', 'warning'); return }
    if (!confirmBatchHapus) { setConfirmBatchHapus(true); return }
    setBatchBusy('hapus')
    let ok = 0, gagal = 0
    try {
      for (const fn of sel) {
        try {
          const r = await fetch(`${base}/api/we/seen?name=${encodeURIComponent(fn)}`, {
            method: 'DELETE',
            headers: authHdr(),
          })
          const j = await r.json().catch(() => null)
          if (r.ok && j && j.ok) ok++
          else gagal++
        } catch { gagal++ }
      }
      pushToast(gagal ? `${ok} dihapus tandanya, ${gagal} gagal` : `${ok} dihapus dari daftar — file di disk tetap ada`, gagal ? 'danger' : 'success')
      setConfirmBatchHapus(false)
      await weRefresh()
    } finally {
      setBatchBusy(null)
    }
  }

  // Warna blok per status (tanpa pill/badge): Seen=Biru, Printed=Oren, Verified=Hijau.
  const statusPal = (f: WeFile): { bg: string; bd: string; ver: boolean } => {
    if (verSet.has(weSkey(f))) return { bg: 'var(--color-success-ghost)', bd: 'var(--color-success)', ver: true }
    if (printedSet.has(f.filename)) return { bg: 'var(--color-warning-ghost)', bd: 'var(--color-warning)', ver: false }
    return { bg: 'var(--color-accent-ghost)', bd: 'var(--color-accent)', ver: false }
  }

  // Baris file: klik DI MANA SAJA pada blok = pilih batch (checkbox hanya indikator).
  // Warna blok menunjukkan status; yg dipilih ditandai ring + checkbox.
  // Tanpa badge kertas/duplex/status, tanpa tombol aksi per-baris (via floating blok).
  const weRow = (f: WeFile, i: number) => {
    const pal = statusPal(f)
    const checked = sel.has(f.filename)
    const pm = printedMap.get(f.filename)
    const mach = pm?.machine || f.machine || '—'
    const opr = pm?.operator || f.operator || '—'
    return (
      <div key={f.filename} onClick={() => toggle(f.filename)}
        className="animate-fade-rise rounded-lg px-3.5 py-2.5"
        style={{ animationDelay: `${Math.min(i * 30, 300)}ms`, cursor: 'pointer', background: pal.bg, border: `1px solid ${pal.bd}`, boxShadow: checked ? `0 0 0 2px ${pal.bd}` : undefined, opacity: pal.ver ? 0.6 : 1 }}>
        <div className="flex items-center gap-2 mb-1">
          <input type="checkbox" checked={checked} onChange={() => toggle(f.filename)} onClick={e => e.stopPropagation()} title="Pilih baris ini" />
          <span className="font-mono font-semibold truncate flex-1" title={f.filename}
            style={{ fontSize: 'var(--text-xs)', color: 'var(--color-accent)' }}>{f.filename}</span>
          <span className="font-mono shrink-0" title={f.first_seen_ts ? `Masuk: ${f.first_seen_ts}` : ''}
            style={{ fontSize: 10, color: 'var(--color-text-dim)' }}>
            {(f.first_seen_ts || '').slice(11) || ''}
          </span>
          <span className="font-mono font-semibold shrink-0" title={f.nota || ''}
            style={{ fontSize: 'var(--text-xs)', color: 'var(--color-text)' }}>{f.nota || ''}</span>
        </div>
        {f.customer ? (
          <div className="truncate mb-1" title={f.customer}
            style={{ fontSize: 'var(--text-sm)', color: 'var(--color-text-secondary)' }}>
            {f.customer}
          </div>
        ) : null}
        <div className="flex items-center gap-2" style={{ fontSize: 10, color: 'var(--color-text-dim)' }}>
          <span>Mesin: <span className="font-semibold" style={{ color: 'var(--color-text-secondary)' }}>{mach}</span></span>
          <span>•</span>
          <span>Operator: <span className="font-semibold" style={{ color: 'var(--color-text-secondary)' }}>{opr}</span></span>
        </div>
      </div>
    )
  }

  if (!live && weSeen.length === 0 && wePrinted.length === 0) {
    return (
      <EmptyState
        icon="eye"
        title="Hub Offline"
        description="Hubungkan ke hub untuk data WE live"
      />
    )
  }

  const now = new Date()
  const tgl = `${now.getDate()} ${WE_MON[now.getMonth()]} ${now.getFullYear()} (${WE_DAY[now.getDay()]})`
  const isWait = !weAuto || weAuto.mode === 'wait'
  const pill = isWait
    ? { label: 'MENUNGGU folder', status: 'muted' as StatusLevel }
    : isManual
      ? { label: 'MANUAL override', status: 'warning' as StatusLevel }
      : { label: `AUTO ${tgl}`, status: 'success' as StatusLevel }

  return (
    <div className="flex flex-col gap-4">
      <div className="card px-5 py-4">
        <SectionHeader label="Monitoring WE"
          action={
            <div className="flex items-center gap-1.5">
              <button className="btn btn-ghost btn-sm" title="Muat ulang state WE" onClick={doManualRefresh} disabled={refreshing}>
                <Icon name="refresh" size={13} /> {refreshing ? 'Memuat…' : 'Refresh'}
              </button>
              {isAdmin && (
                <>
                  <button className="btn btn-secondary btn-sm" title="Pindai ulang folder MONITORING" onClick={doRescan} disabled={busy || !live}>
                    <Icon name="refresh" size={13} /> Rescan
                  </button>
                  <button className="btn btn-secondary btn-sm" title="Ganti mode auto/manual" onClick={doToggleAuto} disabled={busy || !live}>
                    {isManual ? 'Ke AUTO' : 'Ke MANUAL'}
                  </button>
                </>
              )}
            </div>
          }
        />
        <div className="flex items-center gap-2.5 flex-wrap">
          <span className="font-mono truncate flex-1" title={weAuto?.monitoring || ''}
            style={{ fontSize: 'var(--text-xs)', color: 'var(--color-text-secondary)', minWidth: 160 }}>
            {weAuto?.monitoring || 'Belum ada folder MONITORING'}
          </span>
          <Badge label={pill.label} status={pill.status} />
        </div>
      </div>
      <div className="card p-4 flex flex-col gap-2.5">
        <div className="flex items-center gap-2">
          <StatusDot status="accent" live={live} />
          <span className="section-label">SEEN</span>
          <span className="rounded-full px-1.5 font-bold" style={{ background: 'var(--color-border)', fontSize: 10 }}>{q ? `${rows.length}/${weSeen.length}` : pendingCount}</span>
        </div>
        <div style={{ fontSize: 10, color: 'var(--color-text-dim)' }}>Menunggu cetak — terbaru dulu (waktu masuk) • {wePrinted.length} printed dari {weSeen.length} terlihat{weUpdatedAt ? ` • data ${new Date(weUpdatedAt).toLocaleTimeString('id-ID', { hour: '2-digit', minute: '2-digit', second: '2-digit' })}` : ''}</div>
        <div className="relative">
          <Icon name="search" size={13} style={{ color: 'var(--color-text-muted)', position: 'absolute', left: 8, top: '50%', transform: 'translateY(-50%)' } as any} />
          <input className="input" placeholder="Cari file / nota / pelanggan…" value={q} onChange={e => setQ(e.target.value)} style={{ paddingLeft: 28, fontSize: 'var(--text-xs)' }} />
        </div>
        <div className="flex flex-col gap-2 overflow-y-auto" style={{ maxHeight: 460 }}>
          {rows.length > 0 && (
            <label className="flex items-center gap-2 rounded-lg px-2.5 py-2 cursor-pointer"
              style={{ fontSize: 'var(--text-xs)', color: 'var(--color-text-muted)', background: 'var(--color-bg)', border: '1px solid var(--color-border)' }}>
              <input type="checkbox" checked={allChecked} onChange={toggleAll} />
              Pilih semua
              <span className="rounded-full font-mono font-semibold shrink-0" style={{ background: 'var(--color-border)', color: 'var(--color-text-secondary)', fontSize: 10, padding: '1px 8px' }}>{sel.size}/{rows.length}</span>
            </label>
          )}
          {rows.length === 0 && (
            <div style={{ fontSize: 'var(--text-xs)', color: 'var(--color-text-dim)' }}>{q ? `Tidak ada hasil untuk "${q}"` : (live ? 'Belum ada file terlihat' : 'Menunggu hub…')}</div>
          )}
          {rows.map((f, i) => weRow(f, i))}
        </div>
      </div>
      {sel.size > 0 && typeof document !== 'undefined' && createPortal(
        // Portal ke body: ancestor halaman ber-animasi (transform) membuat
        // position:fixed relatif ke konten + animation-fill menimpa translateX.
        // Div luar (tanpa animasi) pegang centering; div dalam yg beranimasi.
        <div style={{ position: 'fixed', left: '50%', transform: 'translateX(-50%)', bottom: 16, zIndex: 9000, maxWidth: 'calc(100vw - 32px)' }}>
        <div className="card animate-fade-rise" style={{ boxShadow: '0 12px 32px rgba(0,0,0,0.45)' }}>
          <div className="flex flex-wrap items-center justify-center gap-x-3 gap-y-2" style={{ padding: '12px 16px' }}>
            <div className="flex items-center gap-2">
              <span className="rounded-full font-semibold shrink-0" style={{ background: 'var(--color-accent-ghost)', color: 'var(--color-accent)', fontSize: 'var(--text-xs)', padding: '3px 10px' }}>{sel.size} dipilih</span>
              <button className="btn btn-ghost btn-sm" title="Batalkan pilihan" onClick={clearSel}>
                <Icon name="x" size={13} />
              </button>
            </div>
            <span style={{ width: 1, alignSelf: 'stretch', background: 'var(--color-border)' }} />
            <div className="flex items-center gap-2 flex-wrap justify-center">
              <button className="btn btn-primary btn-sm" disabled={!live || !!batchBusy} onClick={doBatchPrinted}>
                <Icon name="printer" size={13} /> {batchBusy === 'printed' ? 'Memproses…' : 'Printed'}
              </button>
              <button className="btn btn-secondary btn-sm" disabled={!live || !!batchBusy} onClick={doBatchVerify}>
                <Icon name="check" size={13} /> {batchBusy === 'verify' ? 'Memproses…' : 'Verify'}
              </button>
              {confirmBatchHapus ? (
                <>
                  <button className="btn btn-danger btn-sm" disabled={!live || !!batchBusy} title="Hapus dari daftar? File di disk tetap ada." onClick={doBatchUnsee}>
                    {batchBusy === 'hapus' ? 'Memproses…' : 'Hapus tandanya?'}
                  </button>
                  <button className="btn btn-ghost btn-sm" disabled={!!batchBusy} onClick={() => setConfirmBatchHapus(false)}>
                    Batal
                  </button>
                </>
              ) : (
                <button className="btn btn-ghost btn-sm" disabled={!live || !!batchBusy} title="Hanya hapus tanda SEEN — file di disk tetap ada" onClick={() => setConfirmBatchHapus(true)} style={{ color: 'var(--color-danger)' }}>
                  Hapus
                </button>
              )}
              <button className="btn btn-ghost btn-sm" title="Kembali ke Seen (batalkan pilihan, baris tetap)" onClick={clearSel}>
                Batal
              </button>
            </div>
            <span style={{ width: 1, alignSelf: 'stretch', background: 'var(--color-border)' }} />
            <div className="flex items-center gap-2">
              <select className="input" value={weMachine} onChange={e => pickWeMachine(e.target.value)} title="Mesin" style={{ fontSize: 'var(--text-xs)', padding: '6px 8px' }}>
                {['Develop 1', 'Develop 2', 'RICOH'].map(m => <option key={m} value={m}>{m}</option>)}
              </select>
              <select className="input" value={weOperator} onChange={e => pickWeOperator(e.target.value)} title="Operator" style={{ fontSize: 'var(--text-xs)', padding: '6px 8px' }}>
                <option value="">—</option>
                {['Nanas', 'Eric', 'Rahmadi'].map(o => <option key={o} value={o}>{o}</option>)}
              </select>
            </div>
          </div>
        </div>
        </div>,
        document.body
      )}
    </div>
  )
}

// ─── Page: WMA ────────────────────────────────────────────────────────────────

const PageWma = ({ entriesAll = [], statuses = {}, verified = [], live, sendCommand, results }: {
  entriesAll: WmaEntry[]
  statuses: Record<string, NotaStatus>
  verified: string[]
  live: boolean
  sendCommand: (action: string, opts?: SendCommandOpts) => string | null
  results: CommandResult[]
}) => {
  const pendingRef = useRef<Set<string>>(new Set())

  useEffect(() => {
    for (const r of results ?? []) {
      if (!pendingRef.current.has(r.id)) continue
      pendingRef.current.delete(r.id)
      pushToast(r.ok ? `OK: ${r.action} — ${r.msg}` : `Gagal: ${r.action} — ${r.msg}`, r.ok ? 'success' : 'danger')
    }
  }, [results])

  const doVerify = (e: WmaEntry) => {
    if (!live) {
      pushToast('Hub offline — perintah tidak dikirim', 'warning')
      return
    }
    const id = sendCommand('verify', { nota: e.nota, bahan: e.bahan ?? '', sides: e.sides ?? '', laminating: e.laminating ?? '', test: e.test ?? false, process_type: e.process_type ?? '' })
    if (!id) {
      pushToast('Hub offline — perintah tidak dikirim', 'warning')
      return
    }
    pendingRef.current.add(id)
  }

  const isVerified = (e: WmaEntry) => verified.includes(rkey(e))

  // Kiri: belum diproses / retry-menunggu-timer = Antrian | Tengah: ok/error/manual, belum verified (Menunggu Aksi) | Kanan: verified (Selesai)
  const left = entriesAll.filter(e => {
    const s = stOf(statuses, e)?.status
    return (!s || s === 'retry') && !isVerified(e)
  })
  const mid = entriesAll.filter(e => {
    const s = stOf(statuses, e)?.status
    return (s === 'ok' || s === 'error' || s === 'manual') && !isVerified(e)
  })
  const right = entriesAll.filter(e => isVerified(e))
  const [selesaiSearch, setSelesaiSearch] = useState('')
  const rightFiltered = right.filter(e => {
    if (!selesaiSearch) return true
    const q = selesaiSearch.toLowerCase()
    return e.nota.toLowerCase().includes(q) || (e.customer || '').toLowerCase().includes(q) || (e.bahan || '').toLowerCase().includes(q)
  })

  const wmaReason = (e: WmaEntry): string | null => {
    const st = stOf(statuses, e)
    const m = st?.msg ?? ''
    if (/^test:/i.test(m)) return 'TEST'
    if (/tidak ada di km/i.test(m)) return 'Nota tidak ada di KM'
    if (/bahan tidak sesuai/i.test(m)) return 'Bahan Tidak Sesuai'
    if (/jumlah tidak sesuai/i.test(m)) return 'QTY tidak sesuai'
    if (/laminat|divisi lain|ada step/i.test(m)) return 'Menunggu divisi lain'
    if ((st?.status === 'error' || st?.status === 'manual') && m)
      return m.length > 80 ? m.slice(0, 77) + '…' : m
    return null
  }

  const row = (e: WmaEntry, showVerify: boolean) => {
    const reason = wmaReason(e)
    const tag = [e.sides ? e.sides.toUpperCase() : '', e.laminating ? e.laminating.toUpperCase() : '', e.test ? 'TEST' : ''].filter(Boolean).join('/')
    return (
    <div key={rkey(e)} className="rounded-lg px-3.5 py-2.5" style={{ background: 'var(--color-bg)' }}>
      <div className="flex items-center gap-2 mb-1">
        <span className="font-mono font-semibold truncate" style={{ fontSize: 'var(--text-xs)', color: 'var(--color-accent)' }}>{e.nota}</span>
        <span className="font-mono ml-auto shrink-0" style={{ fontSize: 'var(--text-xs)', color: 'var(--color-text)' }}>×{e.total_qty ?? 0}</span>
      </div>
      <div className="truncate mb-1" title={e.customer || e.bahan}
        style={{ fontSize: 'var(--text-sm)', color: 'var(--color-text-secondary)' }}>
        {e.customer || '—'}
      </div>
      <div className="flex items-center gap-1.5 flex-wrap">
        <CategoryBadge label={e.bahan ?? '-'} />
        {tag && (
          <span className="rounded font-mono font-semibold"
            style={{ fontSize: 10, background: 'var(--color-panel)', color: 'var(--color-accent-fg)', padding: '2px 6px', border: '1px solid var(--color-border)' }}>
            {tag}
          </span>
        )}
        <span style={{ fontSize: 10, color: 'var(--color-text-dim)' }}>{e.operator || '—'}</span>
        {(reason || showVerify) && (
          <div className="ml-auto flex items-center gap-1.5">
            {reason && (
              <div style={{ fontSize: 10, color: 'var(--color-warning-fg)' }}>{reason}</div>
            )}
            {showVerify && (
              <button className="btn btn-primary btn-sm" onClick={() => doVerify(e)}>
                <Icon name="check" size={12} /> Verified
              </button>
            )}
          </div>
        )}
      </div>
    </div>
    )
  }

  const rack = (title: string, sub: string, level: StatusLevel, list: WmaEntry[], verifyBtn: boolean, empty: string) => (
    <div className="card p-4 flex flex-col gap-2.5">
      <div className="flex items-center gap-2">
        <StatusDot status={level} live={live && level !== 'muted'} />
        <span className="section-label">{title}</span>
        <span className="rounded-full px-1.5 font-bold" style={{ background: 'var(--color-border)', fontSize: 10 }}>{list.length}</span>
      </div>
      <div style={{ fontSize: 10, color: 'var(--color-text-dim)' }}>{sub}</div>
      <div className="flex flex-col gap-2 overflow-y-auto" style={{ maxHeight: 460 }}>
        {list.length === 0 && (
          <div style={{ fontSize: 'var(--text-xs)', color: 'var(--color-text-dim)' }}>{empty}</div>
        )}
        {list.map(e => row(e, verifyBtn))}
      </div>
    </div>
  )

  if (!live && entriesAll.length === 0) {
    return (
      <EmptyState
        icon="layers"
        title="Hub Offline"
        description="Hubungkan ke hub untuk data WMA live"
      />
    )
  }

  return (
    <div className="grid gap-4 wma-racks" style={{ gridTemplateColumns: 'repeat(3, 1fr)' }}>
      {rack('Antrian', 'Belum diproses KMMA', 'accent', left, false, live ? 'Antrean kosong' : 'Menunggu hub…')}
      {rack('Menunggu Aksi', 'Sudah diproses — perlu verifikasi', 'warning', mid, true, 'Tidak ada yang menunggu')}
      <div className="card p-4 flex flex-col gap-2.5">
        <div className="flex items-center gap-2">
          <StatusDot status="success" live={live} />
          <span className="section-label">Selesai</span>
          <span className="rounded-full px-1.5 font-bold" style={{ background: 'var(--color-border)', fontSize: 10 }}>{rightFiltered.length}/{right.length}</span>
        </div>
        <div style={{ fontSize: 10, color: 'var(--color-text-dim)' }}>Sudah diverifikasi</div>
        <div className="relative">
          <Icon name="search" size={13} style={{ color: 'var(--color-text-muted)', position: 'absolute', left: 8, top: '50%', transform: 'translateY(-50%)' } as any} />
          <input className="input" placeholder="Cari nota / pelanggan / bahan…" value={selesaiSearch} onChange={e => setSelesaiSearch(e.target.value)} style={{ paddingLeft: 28, fontSize: 'var(--text-xs)' }} />
        </div>
        <div className="flex flex-col gap-2 overflow-y-auto" style={{ maxHeight: 420 }}>
          {rightFiltered.length === 0 && (
            <div style={{ fontSize: 'var(--text-xs)', color: 'var(--color-text-dim)' }}>{selesaiSearch ? `Tidak ada hasil untuk "${selesaiSearch}"` : 'Belum ada yang selesai'}</div>
          )}
          {rightFiltered.map(e => row(e, false))}
        </div>
      </div>
    </div>
  )
}

// ─── Page: KMMA ───────────────────────────────────────────────────────────────

type Step = { name: string; machine: string; material: string; sdm: string; qty: number; status: 'done'|'active'|'pending'|'error'; warn: string | null }
const StepDot = ({ s }: { s: Step['status'] }) => {
  const cfg = {
    done:    { bg: 'var(--color-success)', border: 'var(--color-success)' },
    active:  { bg: 'var(--color-accent)',  border: 'var(--color-accent)'  },
    pending: { bg: 'var(--color-panel)',   border: 'var(--color-border)'  },
    error:   { bg: 'var(--color-danger)',  border: 'var(--color-danger)'  },
  }
  const c = cfg[s]
  return (
    <div className="flex items-center justify-center rounded-full shrink-0"
      style={{ width: 20, height: 20, background: c.bg, border: `2px solid ${c.border}`, zIndex: 1 }}>
      {s === 'done'  && <Icon name="check" size={10} className="" style={{ color: '#fff', strokeWidth: 3 } as any} />}
      {s === 'active' && <div className="rounded-full" style={{ width: 6, height: 6, background: '#fff' }} />}
      {s === 'error' && <Icon name="x"     size={10} className="" style={{ color: '#fff', strokeWidth: 3 } as any} />}
    </div>
  )
}

function kmmaStepFromHub(s: HubStep): Step {
  return {
    name: s.name,
    machine: s.machine,
    material: s.material,
    sdm: s.sdm,
    qty: Number(s.qty) || 0,
    status: s.status === 'ok' ? 'done' : s.status === 'error' ? 'error' : 'pending',
    warn: s.status === 'error'
      ? (s.msg || 'Gagal menyimpan step.')
      : (/laminat|finishing|divisi/i.test(s.name) && s.status !== 'ok'
        ? (s.msg || 'Ada step Laminating — harus diselesaikan divisi lain sebelum lanjut.')
        : null),
  }
}

function kmmaStepsHeuristic(e: WmaEntry, st?: NotaStatus): Step[] {
  const done = st?.status === 'ok'
  const failed = st?.status === 'error'
  const msg = st?.msg ?? ''
  const proc = (e.process_type || '').toLowerCase()
  const qty = e.total_qty ?? 0
  const op = e.operator || '—'
  const steps: Step[] = [{
    name: 'Digital A3+', machine: e.machine || '—', material: e.bahan ?? '—',
    sdm: op, qty, status: done || (failed && proc !== '') ? 'done' : 'pending',
    warn: !proc && failed && msg ? msg : null,
  }]
  if (/laminat|finishing/i.test(msg) || /laminat/i.test(e.bahan ?? '')) {
    steps.push({
      name: 'Laminating', machine: 'Divisi lain', material: '—', sdm: '—', qty,
      status: done ? 'done' : 'pending',
      warn: /laminat|finishing|divisi/i.test(msg) ? msg : null,
    })
  }
  if (proc === 'kisscut' || proc === 'diecut') {
    steps.push({
      name: proc === 'kisscut' ? 'Kisscut' : 'Diecut', machine: 'Vulcan',
      material: '—', sdm: op, qty, status: done ? 'done' : 'pending',
      warn: failed ? (msg || 'Gagal menyimpan step.') : null,
    })
  }
  return steps
}

const PageKmma = ({ entries, statuses, details, live, sendCommand, results, timer, verified = [] }: {
  entries: WmaEntry[]
  statuses: Record<string, NotaStatus>
  details: Record<string, HubStep[]>
  live: boolean
  sendCommand: (action: string, opts?: SendCommandOpts) => string | null
  results: CommandResult[]
  timer: KmmaTimer | null
  verified?: string[]
}) => {
  // Sembunyikan baris terverifikasi spt WMA (tetap hidup di WMA Selesai)
  const isVerified = (e: WmaEntry) => verified.includes(rkey(e))
  const visibleEntries = entries.filter(e => !isVerified(e))
  const rowKeys = visibleEntries.map(rkey)
  const [openRow, setOpenRow]       = useState<string | null>(null)
  const [overrides, setOverrides] = useState<Record<string, number>>({})
  // Lipatan seksi Menunggu Aksi saja — independen dari openRow per-baris
  const [tungguOpen, setTungguOpen] = useState<boolean | null>(null)
  const pendingRef = useRef<Set<string>>(new Set())

  // Timer cycle: detik berjalan lokal, sinkron ulang tiap laporan KMMA (5 dtk)
  const [nowTs, setNowTs] = useState(() => Date.now())
  const recvRef = useRef<number>(0)
  const baseRef = useRef<number>(0)
  useEffect(() => {
    if (timer) { recvRef.current = Date.now(); baseRef.current = timer.next_in }
  }, [timer])
  useEffect(() => {
    const t = setInterval(() => setNowTs(Date.now()), 1000)
    return () => clearInterval(t)
  }, [])
  const elapsed = Math.floor((nowTs - recvRef.current) / 1000)
  const remain = timer ? Math.max(0, baseRef.current - elapsed) : null
  const timerStale = !live || !timer || elapsed > 15
  const mmss = remain === null ? '—'
    : `${String(Math.floor(remain / 60)).padStart(2, '0')}:${String(remain % 60).padStart(2, '0')}`
  const pct = timer && timer.interval > 0 && remain !== null
    ? Math.min(100, Math.max(0, (remain / timer.interval) * 100)) : 0

  const open = openRow && rowKeys.includes(openRow) ? openRow : (rowKeys[0] ?? null)

  const racks = visibleEntries.map(row => {
    const st = stOf(statuses, row)
    const stName = st?.status
    const rk = rkey(row)
    const real = details[rk] ?? details[row.nota]
    return {
      key: rk,
      nota: row.nota,
      customer: row.customer || '—',
      bahan: row.bahan ?? '-',
      qty: row.total_qty ?? 0,
      operator: row.operator || '—',
      status: (stName === 'ok' ? 'success' : stName === 'manual' ? 'warning' : stName === 'error' ? 'danger' : 'accent') as StatusLevel,
      statusLabel: stName === 'ok' ? 'SELESAI' : stName === 'manual' ? 'MANUAL' : stName === 'error' ? 'ERROR' : 'ANTRI',
      steps: real?.length ? real.map(kmmaStepFromHub) : kmmaStepsHeuristic(row, st),
    }
  })

  // Kelompok visual: Aktif (ANTRI) selalu bentang; Menunggu Aksi (MANUAL/ERROR/sisa SELESAI) bisa dilipat
  const aktif = racks.filter(r => r.statusLabel === 'ANTRI')
  const menunggu = racks.filter(r => r.statusLabel !== 'ANTRI')
  // Bawaan lipat bila ramai (>5), bentang bila sepi — pilihan pengguna menang
  const menungguOpen = tungguOpen ?? (menunggu.length <= 5)

  useEffect(() => {
    for (const r of results ?? []) {
      if (!pendingRef.current.has(r.id)) continue
      pendingRef.current.delete(r.id)
      pushToast(r.ok ? `OK: ${r.action} — ${r.msg}` : `Gagal: ${r.action} — ${r.msg}`, r.ok ? 'success' : 'danger')
    }
  }, [results])

  const doCommand = (nota: string, action: 'retry' | 'skip', qty?: string) => {
    if (!live || !nota) {
      pushToast('Hub offline — perintah tidak dikirim', 'warning')
      return
    }
    const id = action === 'skip'
      ? sendCommand('skip', { nota, reason: 'manual dari dashboard' })
      : sendCommand('retry', { nota, qty })
    if (!id) {
      pushToast('Hub offline — perintah tidak dikirim', 'warning')
      return
    }
    pendingRef.current.add(id)
    pushToast(`Perintah dikirim: ${action} ${nota}${qty ? ` (qty ${qty})` : ''}`, 'accent')
  }

  // Kartu rak tunggal — dipakai ulang seksi Aktif + Menunggu Aksi (logika buka/tombol sama)
  const rackCard = (r: (typeof racks)[number]) => {
    const isOpen = open === r.key
    return (
    <div key={r.key} className="card overflow-hidden">
      {/* Rak nota: Pelanggan | NOTA | Bahan | qty | Operator */}
      <button onClick={() => setOpenRow(isOpen ? null : r.key)}
        className="w-full text-left cursor-pointer transition-colors hover:bg-white/[0.02]">
        <div className="flex items-center gap-x-5 gap-y-2 flex-wrap px-5 py-3.5">
          <div className="min-w-0" style={{ flex: '2 1 180px' }}>
            <div className="section-label mb-0.5">Pelanggan</div>
            <div className="font-bold truncate" title={r.customer}
              style={{ fontSize: 'var(--text-base)', color: 'var(--color-text)' }}>{r.customer}</div>
          </div>
          <div className="min-w-0" style={{ flex: '1 1 150px' }}>
            <div className="section-label mb-0.5">Nota</div>
            <div className="font-mono font-semibold truncate"
              style={{ fontSize: 'var(--text-sm)', color: 'var(--color-accent)' }}>{r.nota}</div>
          </div>
          <div className="min-w-0" style={{ flex: '1 1 150px' }}>
            <div className="section-label mb-0.5">Bahan</div>
            <div className="truncate" title={r.bahan}
              style={{ fontSize: 'var(--text-sm)', color: 'var(--color-text-secondary)' }}>{r.bahan}</div>
          </div>
          <div>
            <div className="section-label mb-0.5">Qty</div>
            <div className="font-mono font-semibold" style={{ fontSize: 'var(--text-sm)' }}>{r.qty.toLocaleString()}</div>
          </div>
          <div>
            <div className="section-label mb-0.5">Operator</div>
            <div style={{ fontSize: 'var(--text-sm)', color: 'var(--color-text-secondary)' }}>{r.operator}</div>
          </div>
          <div className="ml-auto flex items-center gap-2.5 shrink-0">
            <Badge label={r.statusLabel} status={r.status} />
            <Icon name="chevronDown" size={16}
              style={{ color: 'var(--color-text-muted)', transform: isOpen ? 'rotate(180deg)' : undefined, transition: 'transform 0.2s' } as any} />
          </div>
        </div>
      </button>

      {/* Dropdown: Alur Produksi */}
      {isOpen && (
      <div className="px-6 py-5" style={{ borderTop: '1px solid var(--color-border)' }}>
        <SectionHeader label="Alur Produksi" action={
          r.statusLabel === 'MANUAL' ? (
            <button className="btn btn-secondary btn-sm" onClick={() => doCommand(r.nota, 'skip')}>
              <Icon name="skip" size={13} /> Lewati Permanen
            </button>
          ) : undefined
        } />
        <div className="flex flex-col">
          {r.steps.map((step, i) => {
            const isLast = i === r.steps.length - 1
            const pct = step.status === 'done' ? '100%' : step.status === 'active' ? '50%' : '0%'
            const key = `${r.key}-${i}`
            const qty = overrides[key] ?? step.qty

        return (
          <div key={i} className="flex gap-4">
            {/* Connector column */}
            <div className="flex flex-col items-center shrink-0">
              <StepDot s={step.status} />
              {!isLast && (
                <div className="timeline-connector flex-1" style={{ minHeight: 48 }}>
                  <div className="timeline-connector-fill" style={{ height: pct }} />
                </div>
              )}
            </div>

            {/* Step content */}
            <div className="flex-1 pb-5 last:pb-0 -mt-0.5">
              <div className="flex items-center gap-2.5 mb-2">
                <span className="font-bold" style={{
                  fontSize: 'var(--text-md)',
                  color: step.status === 'pending' ? 'var(--color-text-muted)' : 'var(--color-text)',
                }}>{step.name}</span>
                {step.status === 'active' && (
                  <span className="rounded-full font-bold"
                    style={{ fontSize: 10, background: 'var(--color-accent-ghost)', color: 'var(--color-accent)', padding: '2px 7px' }}>
                    SEDANG BERJALAN
                  </span>
                )}
              </div>

              {/* Warning / error banner */}
              {step.warn && (
                <div className="flex items-start gap-2 rounded-lg px-3 py-2 mb-2"
                  style={{
                    background: step.status === 'error' ? 'var(--color-danger-ghost)' : 'var(--color-warning-ghost)',
                    border: `1px solid ${step.status === 'error' ? 'var(--color-danger)' : 'var(--color-warning)'}30`,
                    fontSize: 'var(--text-xs)',
                    color: step.status === 'error' ? 'var(--color-danger-fg)' : 'var(--color-warning-fg)',
                  }}>
                  <Icon name="alertTriangle" size={14} className="shrink-0 mt-0.5" />
                  {step.warn}
                </div>
              )}

              {/* Meta grid */}
              <div className="grid grid-cols-4 gap-2 rounded-lg px-3.5 py-2.5 four-col" style={{ background: 'var(--color-bg)' }}>
                {[['Mesin', step.machine], ['Material', step.material], ['SDM', step.sdm]].map(([k, v]) => (
                  <div key={k as string}>
                    <div className="section-label mb-0.5">{k}</div>
                    <div style={{ fontSize: 'var(--text-sm)', color: 'var(--color-text-secondary)' }}>{String(v)}</div>
                  </div>
                ))}
                <div>
                  <div className="section-label mb-0.5">Qty</div>
                  {step.status === 'pending' ? (
                    <div className="flex items-center gap-1.5">
                      <input className="input font-mono" type="number" value={qty}
                        onChange={e => setOverrides(p => ({ ...p, [key]: +e.target.value }))}
                        style={{ width: 80, padding: '4px 8px', fontSize: 'var(--text-xs)' }} />
                        <button className="btn btn-ghost btn-sm"
                          onClick={() => doCommand(r.nota, 'retry', String(qty))}>
                          <Icon name="save" size={13} />
                        </button>
                    </div>
                  ) : (
                    <span className="font-mono font-semibold" style={{ fontSize: 'var(--text-sm)' }}>
                      {step.qty.toLocaleString()}
                    </span>
                  )}
                </div>
              </div>

              {(step.status === 'active' || step.status === 'error') && (
                <div className="flex gap-2 mt-2.5">
                  <button className="btn btn-success btn-sm"
                    onClick={() => doCommand(r.nota, 'retry')}>
                    <Icon name="refresh" size={13} /> Simpan Ulang
                  </button>
                  <button className="btn btn-secondary btn-sm"
                    onClick={() => doCommand(r.nota, 'skip')}>
                    <Icon name="skip" size={13} /> Lewati Permanen
                  </button>
                </div>
              )}
            </div>
          </div>
        )
      })}
      </div>
      </div>
      )}
    </div>
    )
  }

  if (!live) {
    return (
      <EmptyState
        icon="package"
        title="Hub Offline"
        description="Hubungkan ke hub untuk data KMMA live"
      />
    )
  }
  if (racks.length === 0) {
    return (
      <EmptyState
        icon="package"
        title="Belum ada nota"
        description="Belum ada entri nota dari WMA"
      />
    )
  }

  return (
    <div className="flex flex-col gap-2.5">
      {/* Bar timer cycle 3 menit */}
      <div className="card px-5 py-3.5 flex items-center gap-4 flex-wrap">
        <StatusDot status={live && !timerStale ? 'success' : 'danger'} live={live && !timerStale} shake={!live} />
        <div>
          <div className="section-label mb-0.5">Timer Cycle KMMA</div>
          <div className="font-mono font-bold" style={{ fontSize: 'var(--text-xl)', color: 'var(--color-text)' }}>{timerStale ? '—' : mmss}</div>
        </div>
        <div className="flex-1 min-w-[120px] h-1 rounded-full" style={{ background: 'var(--color-border)' }}>
          <div className="h-1 rounded-full" style={{ width: `${pct}%`, background: 'var(--color-accent)', transition: 'width 1s linear' }} />
        </div>
        <span style={{ fontSize: 'var(--text-label)', color: 'var(--color-text-muted)' }}>
          {timerStale ? 'menunggu KMMA…' : 'proses ulang tiap 3 menit'}
        </span>
      </div>
      {/* Seksi Aktif — antrean berjalan, selalu bentang */}
      <div className="flex flex-col gap-2.5">
        <div className="flex items-center gap-2 px-1">
          <StatusDot status="accent" live={live} />
          <span className="section-label">Aktif</span>
          <span className="rounded-full px-1.5 font-bold" style={{ background: 'var(--color-border)', fontSize: 10 }}>{aktif.length}</span>
        </div>
        {aktif.length === 0 && (
          <div style={{ fontSize: 'var(--text-xs)', color: 'var(--color-text-dim)' }}>tidak ada antrean aktif</div>
        )}
        {aktif.map(rackCard)}
      </div>
      {/* Seksi Menunggu Aksi — perlu campur tangan, bisa dilipat */}
      <div className="flex flex-col gap-2.5">
        <button onClick={() => setTungguOpen(o => !(o ?? (menunggu.length <= 5)))}
          className="flex items-center gap-2 px-1 w-full text-left cursor-pointer">
          <StatusDot status="warning" live={live} />
          <span className="section-label">Menunggu Aksi</span>
          <span className="rounded-full px-1.5 font-bold" style={{ background: 'var(--color-border)', fontSize: 10 }}>{menunggu.length}</span>
          <span className="ml-auto flex items-center" style={{ color: 'var(--color-text-muted)' }}>
            <Icon name="chevronDown" size={16}
              style={{ transform: menungguOpen ? 'rotate(180deg)' : undefined, transition: 'transform 0.2s' } as any} />
          </span>
        </button>
        {menungguOpen && menunggu.length === 0 && (
          <div style={{ fontSize: 'var(--text-xs)', color: 'var(--color-text-dim)' }}>tidak ada yang menunggu aksi</div>
        )}
        {menungguOpen && menunggu.map(rackCard)}
      </div>
    </div>
  )
}

// ─── Page: Log ────────────────────────────────────────────────────────────────

const PageLog = ({ logs, live }: { logs: HubLogEntry[]; live: boolean }) => {
  const [tab, setTab]           = useState<'WMA'|'KMMA'|'HUB'|'KMA'>('HUB')
  const [filter, setFilter]     = useState('')
  const [autoScroll, setAutoScroll] = useState(true)
  const logRef = useRef<HTMLDivElement>(null)

  const liveLines = logs.map(l => ({
    ts: l.ts, level: guessLevel(l.msg) === 'danger' ? 'ERROR' : guessLevel(l.msg) === 'warning' ? 'WARN' : 'INFO',
    src: l.module, msg: l.msg,
  }))
  const lines = liveLines.filter(l =>
    (tab === 'HUB' || l.src === tab) &&
    (!filter || l.msg.toLowerCase().includes(filter.toLowerCase()) || l.level.includes(filter.toUpperCase()))
  )

  useEffect(() => {
    if (autoScroll && logRef.current) logRef.current.scrollTop = logRef.current.scrollHeight
  }, [lines, autoScroll])

  return (
    <div className="flex flex-col gap-3">
      {/* Toolbar */}
      <div className="flex items-center gap-2 flex-wrap">
        {(['WMA','KMMA','HUB','KMA'] as const).map(t => (
          <button key={t} className="chip font-mono"
            onClick={() => setTab(t)}
            style={{
              background: tab === t ? 'var(--color-nav-active)' : 'var(--color-panel)',
              color:      tab === t ? 'var(--color-accent)'     : 'var(--color-text-secondary)',
              border: `1px solid ${tab === t ? 'var(--color-accent-ghost)' : 'var(--color-border)'}`,
            }}>{t}</button>
        ))}
        <input className="input" placeholder="Filter pesan…" value={filter}
          onChange={e => setFilter(e.target.value)} style={{ width: 200 }} />
        <span className="ml-auto" style={{ fontSize: 'var(--text-xs)', color: 'var(--color-text-muted)' }}>
          {lines.length} baris
        </span>
        <button className="btn btn-secondary btn-sm"
          onClick={() => setAutoScroll(v => !v)}
          style={{ color: autoScroll ? 'var(--color-accent)' : 'var(--color-text-muted)' }}>
          <Icon name="chevronDown" size={14} />
          {autoScroll ? 'Auto-scroll ON' : 'Auto-scroll OFF'}
        </button>
      </div>

      {/* Terminal */}
      <div className="overflow-hidden" style={{ background: 'var(--color-panel-deep)', border: '1px solid var(--color-border)', borderRadius: 'var(--radius-card)' }}>
        {/* Chrome dots */}
        <div className="flex items-center gap-2 px-4 py-2.5"
          style={{ background: 'var(--color-bg)', borderBottom: '1px solid var(--color-border-subtle)' }}>
          {['var(--color-danger)','var(--color-warning)','var(--color-success)'].map((c, i) => (
            <div key={i} className="rounded-full" style={{ width: 10, height: 10, background: c }} />
          ))}
          <span className="font-mono ml-2" style={{ fontSize: 'var(--text-label)', color: 'var(--color-text-muted)' }}>
            NSH HUB — {tab} Log Stream
          </span>
        </div>
        {/* Lines */}
        <div ref={logRef} className="font-mono overflow-y-auto" style={{ padding: '12px 16px', maxHeight: 440, fontSize: 'var(--text-xs)', lineHeight: '1.7' }}>
          {lines.map((l, i) => (
            <div key={i} className={`flex gap-3 log-${l.level.toLowerCase()}`}>
              <span className="shrink-0" style={{ color: 'var(--color-text-dim)' }}>{l.ts}</span>
              <span className="font-bold shrink-0" style={{ minWidth: 44 }}>{l.level}</span>
              <span className="shrink-0" style={{ color: 'var(--color-text-muted)', minWidth: 40 }}>[{l.src}]</span>
              <span>{l.msg}</span>
            </div>
          ))}
          {lines.length === 0 && (
            <div style={{ color: 'var(--color-text-dim)' }}>// Tidak ada log yang cocok</div>
          )}
        </div>
      </div>
    </div>
  )
}

// ─── Database browser (phpMyAdmin mini) — read-only SQLite ───────────────────
// Kenapa SQLite bukan PHP/MySQL: sudah ada nsh.db SQLite (WAL, tanpa server),
// tambah PHP/MySQL = overkill (butuh XAMPP, driver, migrasi). Cukup expose
// SQLite via hub HTTP read-only (whitelist, parameterised, limit/offset).
function hubHttpBase(hubUrl: string): string {
  try {
    const u = new URL(hubUrl)
    const host = u.hostname || location.hostname || 'localhost'
    const port = u.port === '8765' ? '8766' : '8766'
    return `http://${host}:${port}`
  } catch {
    try { return `http://${location.hostname || 'localhost'}:8766` } catch { return 'http://localhost:8766' }
  }
}

type DbTable = { name: string; count: number; columns: string[]; pk?: string[]; indexes?: {name:string; unique:boolean; columns:string[]}[] }

const DatabasePanel = ({ hubUrl }: { hubUrl: string }) => {
  const base = hubHttpBase(hubUrl)
  const [tables, setTables] = useState<DbTable[]>([])
  const [sel, setSel] = useState<string>('')
  const [cols, setCols] = useState<string[]>([])
  const [rows, setRows] = useState<Record<string, unknown>[]>([])
  const [total, setTotal] = useState(0)
  const [limit] = useState(20)
  const [offset, setOffset] = useState(0)
  const [search, setSearch] = useState('')
  const [searchInput, setSearchInput] = useState('')
  const [loadingTables, setLoadingTables] = useState(false)
  const [loadingRows, setLoadingRows] = useState(false)
  const [err, setErr] = useState('')
  const [editing, setEditing] = useState<{ r: number; c: string; v: string } | null>(null)
  const [saving, setSaving] = useState(false)

  const tokAuth = getAuthToken()
  const authHdr: Record<string,string> | undefined = tokAuth ? { Authorization: `Bearer ${tokAuth}` } : undefined
  const fetchTables = useCallback(async () => {
    setLoadingTables(true); setErr('')
    try {
      const r = await fetch(`${base}/api/db/tables`, { headers: authHdr })
      const j = await r.json()
      if (!j.ok) throw new Error(j.msg || 'gagal')
      setTables(j.tables || [])
      if (!sel && j.tables?.length) setSel(j.tables[0].name)
    } catch (e: unknown) {
      setErr(e instanceof Error ? e.message : String(e))
    } finally { setLoadingTables(false) }
  }, [base, sel])

  const fetchRows = useCallback(async () => {
    if (!sel) return
    setLoadingRows(true); setErr('')
    try {
      const qs = new URLSearchParams({ name: sel, limit: String(limit), offset: String(offset), search })
      const r = await fetch(`${base}/api/db/table?${qs}`, { headers: authHdr })
      const j = await r.json()
      if (!j.ok) throw new Error(j.msg || 'gagal')
      setCols(j.columns || [])
      setRows(j.rows || [])
      setTotal(j.total || 0)
    } catch (e: unknown) {
      setErr(e instanceof Error ? e.message : String(e))
    } finally { setLoadingRows(false) }
  }, [base, sel, limit, offset, search])

  useEffect(() => { fetchTables() }, [fetchTables])
  useEffect(() => { fetchRows() }, [fetchRows])
  // debounced search
  useEffect(() => {
    const t = setTimeout(() => { setSearch(searchInput); setOffset(0) }, 320)
    return () => clearTimeout(t)
  }, [searchInput])
  // reset offset when table changes
  useEffect(() => { setOffset(0); setSearch(''); setSearchInput('') }, [sel])

  const page = Math.floor(offset / limit) + 1
  const pages = Math.max(1, Math.ceil(total / limit))
  const fmt = (v: unknown) => {
    if (v === null || v === undefined) return <span style={{ color: 'var(--color-text-dim)' }}>NULL</span>
    const s = String(v)
    if (s.length > 120) return s.slice(0, 120) + '…'
    return s
  }

  const pkOfSel = tables.find(t => t.name === sel)?.pk || []
  const startEdit = (r: number, c: string) => {
    const v = rows[r]?.[c]
    setEditing({ r, c, v: v == null ? '' : String(v) })
  }
  const cancelEdit = () => setEditing(null)
  const saveEdit = async () => {
    if (!editing || !sel) return
    const { r, c, v } = editing
    const row = rows[r]
    if (!row) return
    // bangun WHERE dari PK, fallback ke semua kolom bila tanpa PK (update by rowid tidak ada)
    const whereCols = pkOfSel.length ? pkOfSel : cols
    const whereVals = whereCols.map(k => row[k])
    // cegah update PK itu sendiri tanpa konfirmasi? boleh, tapi tetap where pakai nilai lama
    const esc = (x: unknown) => {
      if (x == null) return 'NULL'
      const s = String(x).replace(/'/g, "''")
      return `'${s}'`
    }
    const setVal = v === '' ? "''" : esc(v)
    // jika kolom yang diedit adalah bagian PK, where pakai nilai lama (row[c]), set pakai nilai baru
    const whereClause = whereCols.map(k => {
      const orig = row[k]
      return `"${k.replace(/"/g, '""')}"=${orig == null ? 'NULL' : esc(orig)}`
    }).join(' AND ')
    const sql = `UPDATE "${sel.replace(/"/g, '""')}" SET "${c.replace(/"/g, '""')}"=${setVal} WHERE ${whereClause}`
    if (!whereClause) { pushToast('PK kosong, tidak bisa update', 'danger'); return }
    // konfirmasi bila tanpa WHERE spesifik? sudah ada where
    setSaving(true)
    try {
      const res = await fetch(`${base}/api/db/exec`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', ...(authHdr || {}) },
        body: JSON.stringify({ sql }),
      })
      const j = await res.json()
      if (!j.ok) throw new Error(j.msg || 'gagal')
      pushToast(`Update ${sel}.${c} OK (${j.rowcount ?? 1})`, 'success')
      setEditing(null)
      await fetchRows()
      // refresh count tabel
      fetchTables()
    } catch (e: unknown) {
      pushToast(e instanceof Error ? e.message : String(e), 'danger')
    } finally { setSaving(false) }
  }

  return (
    <div className="card overflow-hidden" style={{ border: '1px solid var(--color-border)' }}>
      <div className="flex items-center gap-2 px-4 py-3" style={{ borderBottom: '1px solid var(--color-border)', background: 'var(--color-bg)' }}>
        <Icon name="database" size={14} style={{ color: 'var(--color-accent)' } as any} />
        <span className="section-label">Browse Database — nsh.db</span>
        <span className="rounded-full px-1.5 font-bold ml-auto" style={{ background: 'var(--color-border)', fontSize: 10 }}>{tables.length} tabel</span>
        <button className="btn btn-ghost btn-sm" title="Muat ulang" onClick={() => { fetchTables(); fetchRows() }} disabled={loadingTables || loadingRows}>
          <Icon name="refresh" size={13} /> {loadingTables || loadingRows ? '…' : 'Refresh'}
        </button>
      </div>
      {err && (
        <div className="flex items-center gap-2 px-4 py-2" style={{ background: 'var(--color-danger-ghost)', color: 'var(--color-danger-fg)', fontSize: 'var(--text-xs)', borderBottom: '1px solid var(--color-border)' }}>
          <Icon name="alertTriangle" size={13} /> {err}
        </div>
      )}
      <div className="grid" style={{ gridTemplateColumns: '200px 1fr', minHeight: 380 }}>
        {/* Tabel list */}
        <div className="overflow-y-auto" style={{ borderRight: '1px solid var(--color-border)', background: 'var(--color-panel)', maxHeight: 520 }}>
          <div className="px-3 py-2 section-label" style={{ borderBottom: '1px solid var(--color-border-subtle)' }}>Tabel</div>
          {loadingTables && <div className="px-3 py-3" style={{ fontSize: 'var(--text-xs)', color: 'var(--color-text-muted)' }}>Memuat…</div>}
          {tables.map(t => (
            <button key={t.name} onClick={() => setSel(t.name)}
              className="w-full text-left flex items-center gap-2 px-3 py-2.5 cursor-pointer"
              style={{
                background: sel === t.name ? 'var(--color-nav-active)' : 'transparent',
                borderLeft: sel === t.name ? '2px solid var(--color-accent)' : '2px solid transparent',
                borderBottom: '1px solid var(--color-border-subtle)',
              }}>
              <Icon name="table" size={13} style={{ color: sel === t.name ? 'var(--color-accent)' : 'var(--color-text-muted)' } as any} />
              <span className="font-mono truncate flex-1" style={{ fontSize: 'var(--text-xs)', color: sel === t.name ? 'var(--color-text)' : 'var(--color-text-secondary)' }}>{t.name}</span>
              <span className="rounded-full px-1 font-bold shrink-0" style={{ background: 'var(--color-border)', fontSize: 9 }}>{t.count}</span>
            </button>
          ))}
          {!loadingTables && tables.length === 0 && <div className="px-3 py-3" style={{ fontSize: 'var(--text-xs)', color: 'var(--color-text-dim)' }}>Tidak ada tabel</div>}
        </div>
        {/* Data panel */}
        <div className="flex flex-col min-w-0">
          <div className="flex items-center gap-2 px-3 py-2 flex-wrap" style={{ borderBottom: '1px solid var(--color-border-subtle)', background: 'var(--color-bg)' }}>
            <div className="flex items-center gap-1.5 flex-1 min-w-[160px]">
              <Icon name="search" size={13} style={{ color: 'var(--color-text-muted)' } as any} />
              <input className="input" placeholder="Filter (LIKE %…%)…" value={searchInput}
                onChange={e => setSearchInput(e.target.value)} style={{ flex: 1, padding: '6px 10px', fontSize: 'var(--text-xs)' }} />
            </div>
            <span style={{ fontSize: 'var(--text-xs)', color: 'var(--color-text-muted)' }}>{total} baris</span>
          </div>
          <div className="overflow-auto" style={{ maxHeight: 400 }}>
            <table className="data-table" style={{ minWidth: 640 }}>
              <thead>
                <tr>{cols.map(c => <th key={c} style={{ whiteSpace: 'nowrap' }}>{c}</th>)}
                {cols.length === 0 && <th>—</th>}</tr>
              </thead>
              <tbody>
                {loadingRows && <tr><td colSpan={Math.max(1, cols.length)} style={{ padding: 16, color: 'var(--color-text-muted)', fontSize: 'var(--text-xs)' }}>Memuat…</td></tr>}
                {!loadingRows && rows.length === 0 && <tr><td colSpan={Math.max(1, cols.length)}><EmptyState icon="database" title="Tidak ada data" description={search ? `Tidak ada baris cocok "${search}"` : 'Tabel kosong atau hub offline'} /></td></tr>}
                {!loadingRows && rows.map((r, i) => (
                  <tr key={i}>
                    {cols.map(c => {
                      const isEdit = editing?.r === i && editing?.c === c
                      return (
                        <td key={c} title={String(r[c] ?? '')} style={{ maxWidth: 240, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', fontSize: 'var(--text-xs)' }}>
                          {isEdit ? (
                            <input className="input" autoFocus value={editing.v}
                              onChange={e => setEditing({ r: i, c, v: e.target.value })}
                              onKeyDown={e => { if (e.key === 'Enter') saveEdit(); if (e.key === 'Escape') cancelEdit() }}
                              onBlur={saveEdit}
                              onClick={e => e.stopPropagation()}
                              style={{ padding: '4px 6px', fontSize: 'var(--text-xs)', minWidth: 120 }}
                              disabled={saving} />
                          ) : (
                            <div className="flex items-center gap-1" onClick={() => !saving && startEdit(i, c)} style={{ cursor: 'pointer' }}>
                              <span className="font-mono truncate flex-1" style={{ color: r[c] == null ? 'var(--color-text-dim)' : 'var(--color-text-secondary)' }}>{fmt(r[c])}</span>
                            </div>
                          )}
                        </td>
                      )
                    })}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div className="flex items-center gap-2 px-3 py-2 mt-auto" style={{ borderTop: '1px solid var(--color-border-subtle)', background: 'var(--color-bg)' }}>
            <span style={{ fontSize: 'var(--text-xs)', color: 'var(--color-text-muted)' }}>Hal {page}/{pages} • {total} baris</span>
            <div className="ml-auto flex items-center gap-1.5">
              <button className="btn btn-ghost btn-sm" disabled={offset === 0} onClick={() => setOffset(o => Math.max(0, o - limit))}>
                <Icon name="chevronLeft" size={13} /> Prev
              </button>
              <span className="font-mono" style={{ fontSize: 'var(--text-xs)', color: 'var(--color-text-muted)', minWidth: 64, textAlign: 'center' }}>{offset + 1}–{Math.min(offset + limit, total)} / {total}</span>
              <button className="btn btn-ghost btn-sm" disabled={offset + limit >= total} onClick={() => setOffset(o => o + limit)}>
                Next <Icon name="chevronRight" size={13} />
              </button>
            </div>
          </div>
        </div>
      </div>
    </div>
  )
}

// ─── PageDatabase: Tab Kiri admin only — Table + Schema + SQL Editor ──────────
const PageDatabase = ({ hubUrl }: { hubUrl: string }) => {
  const role = getAuthRole()
  if (role !== 'admin') {
    return <EmptyState icon="database" title="Akses ditolak" description="Halaman Database hanya untuk admin" />
  }
  const base = hubHttpBase(hubUrl)
  const [sub, setSub] = useState<'table'|'schema'|'sql'>('table')
  const [tables, setTables] = useState<DbTable[]>([])
  const [sel, setSel] = useState<string>('')
  const [cols, setCols] = useState<string[]>([])
  const [rows, setRows] = useState<Record<string, unknown>[]>([])
  const [total, setTotal] = useState(0)
  const [limit] = useState(20)
  const [offset, setOffset] = useState(0)
  const [search, setSearch] = useState('')
  const [searchInput, setSearchInput] = useState('')
  const [loadingTables, setLoadingTables] = useState(false)
  const [loadingRows, setLoadingRows] = useState(false)
  const [err, setErr] = useState('')
  const [editing, setEditing] = useState<{ r: number; c: string; v: string } | null>(null)
  const [saving, setSaving] = useState(false)
  // SQL editor
  const [sql, setSql] = useState('SELECT * FROM wma_entries LIMIT 20')
  const [sqlRunning, setSqlRunning] = useState(false)
  const [sqlErr, setSqlErr] = useState('')
  const [sqlCols, setSqlCols] = useState<string[]>([])
  const [sqlRows, setSqlRows] = useState<Record<string,unknown>[]>([])
  const [sqlRowCount, setSqlRowCount] = useState<number| null>(null)
  const [sqlLastId, setSqlLastId] = useState<number| null>(null)

  const tok = getAuthToken()
  const hdr: Record<string,string> = tok ? { Authorization: `Bearer ${tok}` } : {}
  const hdrJson: Record<string,string> = { 'Content-Type': 'application/json', ...hdr }

  const fetchTables = useCallback(async () => {
    setLoadingTables(true); setErr('')
    try {
      const tok2 = getAuthToken()
      const h2: Record<string,string> | undefined = tok2 ? { Authorization: `Bearer ${tok2}` } : undefined
      const r = await fetch(`${base}/api/db/tables`, { headers: h2 })
      const j = await r.json()
      if (!j.ok) throw new Error(j.msg||'gagal')
      setTables(j.tables||[])
      if (!sel && j.tables?.length) setSel(j.tables[0].name)
    } catch(e:unknown){ setErr(e instanceof Error?e.message:String(e)) }
    finally{ setLoadingTables(false) }
  }, [base, sel])

  const fetchRows = useCallback(async () => {
    if (!sel) return
    setLoadingRows(true); setErr('')
    try {
      const tok2 = getAuthToken()
      const h2: Record<string,string> | undefined = tok2 ? { Authorization: `Bearer ${tok2}` } : undefined
      const qs = new URLSearchParams({ name: sel, limit: String(limit), offset: String(offset), search })
      const r = await fetch(`${base}/api/db/table?${qs}`, { headers: h2 })
      const j = await r.json()
      if (!j.ok) throw new Error(j.msg||'gagal')
      setCols(j.columns||[]); setRows(j.rows||[]); setTotal(j.total||0)
    } catch(e:unknown){ setErr(e instanceof Error?e.message:String(e)) }
    finally{ setLoadingRows(false) }
  }, [base, sel, limit, offset, search])

  useEffect(()=>{ fetchTables() }, [fetchTables])
  useEffect(()=>{ if(sub==='table') fetchRows() }, [fetchRows, sub])
  useEffect(()=>{ const t=setTimeout(()=>{ setSearch(searchInput); setOffset(0)},320); return()=>clearTimeout(t)},[searchInput])
  useEffect(()=>{ setOffset(0); setSearch(''); setSearchInput('') }, [sel])

  const runSql = async () => {
    const s = sql.trim()
    if (!s) { pushToast('SQL kosong','warning'); return }
    // confirm untuk INSERT/UPDATE/DELETE tanpa WHERE atau berpotensi besar
    const upper = s.toUpperCase()
    const needsWhere = /\b(UPDATE|DELETE)\b/.test(upper) && !/\bWHERE\b/.test(upper)
    const isWrite = /\b(INSERT|UPDATE|DELETE)\b/.test(upper)
    if (isWrite) {
      const msg = needsWhere
        ? `${upper.includes('DELETE')?'DELETE':'UPDATE'} tanpa WHERE akan mengubah semua baris. Lanjutkan?`
        : `${isWrite?'Perintah WRITE akan mengubah data':'Eksekusi'} — lanjutkan?`
      if (!window.confirm(msg)) return
    }
    setSqlRunning(true); setSqlErr(''); setSqlCols([]); setSqlRows([]); setSqlRowCount(null); setSqlLastId(null)
    try {
      const tok3=getAuthToken()
      const h3: Record<string,string> = {'Content-Type':'application/json'}
      if(tok3) h3['Authorization']=`Bearer ${tok3}`
      const r = await fetch(`${base}/api/db/exec`, { method:'POST', headers: h3, body: JSON.stringify({ sql: s }) })
      const j = await r.json()
      if (!j.ok) throw new Error(j.msg||'gagal')
      setSqlCols(j.columns||[]); setSqlRows(j.rows||[]); setSqlRowCount(j.rowcount??null); setSqlLastId(j.lastRowId??null)
      if (j.rowcount!=null && j.rowcount>100) pushToast(`rowCount ${j.rowcount} — operasi besar, periksa kembali`,'warning')
      else pushToast(j.columns?.length? `OK ${j.rows?.length||0} baris` : `OK rowCount ${j.rowcount}`, 'success')
      // refresh table count after write
      if (isWrite) fetchTables()
    } catch(e:unknown){
      const m = e instanceof Error?e.message:String(e)
      setSqlErr(m); pushToast(m,'danger')
    } finally { setSqlRunning(false) }
  }

  const selTable = tables.find(t=>t.name===sel)
  const fmt = (v:unknown)=> {
    if (v===null||v===undefined) return <span style={{color:'var(--color-text-dim)'}}>NULL</span>
    const s=String(v); return s.length>120?s.slice(0,120)+'…':s
  }
  const pkOfSel = selTable?.pk || []
  const startEdit = (r:number, c:string) => {
    const v = rows[r]?.[c]
    setEditing({ r, c, v: v==null ? '' : String(v) })
  }
  const cancelEdit = () => setEditing(null)
  const saveEdit = async () => {
    if (!editing || !sel) return
    const { r, c, v } = editing
    const row = rows[r]
    if (!row) return
    const whereCols = pkOfSel.length ? pkOfSel : cols
    if (!whereCols.length) { pushToast('PK kosong, tidak bisa update', 'danger'); return }
    const esc = (x:unknown) => {
      if (x==null) return 'NULL'
      const s=String(x).replace(/'/g,"''")
      return `'${s}'`
    }
    const setVal = v==='' ? "''" : esc(v)
    const whereClause = whereCols.map(k=>{
      const orig=row[k]
      return orig==null ? `"${k.replace(/"/g,'""')}" IS NULL` : `"${k.replace(/"/g,'""')}"=${esc(orig)}`
    }).join(' AND ')
    const sql = `UPDATE "${sel.replace(/"/g,'""')}" SET "${c.replace(/"/g,'""')}"=${setVal} WHERE ${whereClause}`
    setSaving(true)
    try{
      const hdr2: Record<string,string> = {}
      const tok2=getAuthToken()
      if(tok2) hdr2['Authorization']=`Bearer ${tok2}`
      hdr2['Content-Type']='application/json'
      const res=await fetch(`${base}/api/db/exec`,{method:'POST', headers: hdr2, body: JSON.stringify({sql})})
      const j=await res.json()
      if(!j.ok) throw new Error(j.msg||'gagal')
      pushToast(`Update ${sel}.${c} OK (${j.rowcount??1})`,'success')
      setEditing(null)
      await fetchRows()
      fetchTables()
    }catch(e:unknown){ pushToast(e instanceof Error?e.message:String(e),'danger') }
    finally{ setSaving(false) }
  }

  return (
    <div className="flex flex-col gap-4">
      <div className="flex items-center gap-2 flex-wrap">
        {(['table','schema','sql'] as const).map(t=> (
          <button key={t} className="chip font-mono" onClick={()=>setSub(t)}
            style={{ background: sub===t?'var(--color-nav-active)':'var(--color-panel)', color: sub===t?'var(--color-accent)':'var(--color-text-secondary)', border:`1px solid ${sub===t?'var(--color-accent-ghost)':'var(--color-border)'}` }}>
            {t==='table'?'Table':t==='schema'?'Schema':'SQL Editor'}
          </button>
        ))}
        <span className="ml-auto rounded px-1.5 py-0.5 font-mono" style={{fontSize:10, background:'var(--color-violet-ghost)', color:'var(--color-violet-fg)', border:'1px solid var(--color-violet)'}}>admin only</span>
        <button className="btn btn-ghost btn-sm" onClick={fetchTables} disabled={loadingTables}><Icon name="refresh" size={13}/> Refresh</button>
      </div>
      {err && <div className="flex items-center gap-2 px-4 py-2 rounded" style={{background:'var(--color-danger-ghost)', color:'var(--color-danger-fg)', fontSize:'var(--text-xs)'}}><Icon name="alertTriangle" size={13}/>{err}</div>}

      {sub==='table' && (
        <div className="card overflow-hidden" style={{border:'1px solid var(--color-border)'}}>
          <div className="grid" style={{gridTemplateColumns:'200px 1fr', minHeight:420}}>
            <div className="overflow-y-auto" style={{borderRight:'1px solid var(--color-border)', background:'var(--color-panel)', maxHeight:560}}>
              <div className="px-3 py-2 section-label" style={{borderBottom:'1px solid var(--color-border-subtle)'}}>Tabel</div>
              {loadingTables && <div className="px-3 py-3" style={{fontSize:'var(--text-xs)', color:'var(--color-text-muted)'}}>Memuat…</div>}
              {tables.map(t=>(
                <button key={t.name} onClick={()=>setSel(t.name)} className="w-full text-left flex items-center gap-2 px-3 py-2.5 cursor-pointer"
                  style={{background: sel===t.name?'var(--color-nav-active)':'transparent', borderLeft: sel===t.name?'2px solid var(--color-accent)':'2px solid transparent', borderBottom:'1px solid var(--color-border-subtle)'}}>
                  <Icon name="table" size={13} style={{color: sel===t.name?'var(--color-accent)':'var(--color-text-muted)'} as any}/>
                  <span className="font-mono truncate flex-1" style={{fontSize:'var(--text-xs)', color: sel===t.name?'var(--color-text)':'var(--color-text-secondary)'}}>{t.name}</span>
                  <span className="rounded-full px-1 font-bold shrink-0" style={{background:'var(--color-border)', fontSize:9}}>{t.count}</span>
                </button>
              ))}
            </div>
            <div className="flex flex-col min-w-0">
              <div className="flex items-center gap-2 px-3 py-2 flex-wrap" style={{borderBottom:'1px solid var(--color-border-subtle)', background:'var(--color-bg)'}}>
                <div className="flex items-center gap-1.5 flex-1 min-w-[160px]">
                  <Icon name="search" size={13} style={{color:'var(--color-text-muted)'} as any}/>
                  <input className="input" placeholder="Filter LIKE %…%…" value={searchInput} onChange={e=>setSearchInput(e.target.value)} style={{flex:1, padding:'6px 10px', fontSize:'var(--text-xs)'}}/>
                </div>
                <span style={{fontSize:'var(--text-xs)', color:'var(--color-text-muted)'}}>{total} baris</span>
              </div>
              <div className="overflow-auto" style={{maxHeight:420}}>
                <table className="data-table" style={{minWidth:640}}>
                  <thead><tr>{cols.map(c=><th key={c} style={{whiteSpace:'nowrap'}}>{c}</th>)}{cols.length===0&&<th>—</th>}</tr></thead>
                  <tbody>
                    {loadingRows && <tr><td colSpan={Math.max(1,cols.length)} style={{padding:16, color:'var(--color-text-muted)', fontSize:'var(--text-xs)'}}>Memuat…</td></tr>}
                    {!loadingRows && rows.length===0 && <tr><td colSpan={Math.max(1,cols.length)}><EmptyState icon="database" title="Tidak ada data" description={search?`Tidak ada baris cocok "${search}"`:'Tabel kosong'}/></td></tr>}
                    {!loadingRows && rows.map((r,i)=>(
                      <tr key={i}>{cols.map(c=>{
                        const isEdit = editing?.r===i && editing?.c===c
                        return (
                          <td key={c} title={String(r[c]??'')} style={{maxWidth:240, overflow:'hidden', textOverflow:'ellipsis', whiteSpace:'nowrap', fontSize:'var(--text-xs)', cursor: isEdit ? 'default' : 'pointer'}}
                            onClick={()=>!isEdit && !saving && startEdit(i,c)}>
                            {isEdit ? (
                              <input className="input" autoFocus value={editing.v} onChange={e=>setEditing({r:i,c,v:e.target.value})} onKeyDown={e=>{if(e.key==='Enter') saveEdit(); if(e.key==='Escape') cancelEdit()}} onBlur={saveEdit} onClick={e=>e.stopPropagation()} style={{padding:'4px 6px', fontSize:'var(--text-xs)', minWidth:120}} disabled={saving} />
                            ) : (
                              <span className="font-mono truncate" style={{color: r[c]==null?'var(--color-text-dim)':'var(--color-text-secondary)'}}>{fmt(r[c])}</span>
                            )}
                          </td>
                        )
                      })}</tr>
                    ))}
                  </tbody>
                </table>
              </div>
              <div className="flex items-center gap-2 px-3 py-2 mt-auto" style={{borderTop:'1px solid var(--color-border-subtle)', background:'var(--color-bg)'}}>
                <span style={{fontSize:'var(--text-xs)', color:'var(--color-text-muted)'}}>Hal {Math.floor(offset/limit)+1}/{Math.max(1,Math.ceil(total/limit))} • {total} baris</span>
                <div className="ml-auto flex items-center gap-1.5">
                  <button className="btn btn-ghost btn-sm" disabled={offset===0} onClick={()=>setOffset(o=>Math.max(0,o-limit))}><Icon name="chevronLeft" size={13}/> Prev</button>
                  <span className="font-mono" style={{fontSize:'var(--text-xs)', color:'var(--color-text-muted)', minWidth:64, textAlign:'center'}}>{offset+1}–{Math.min(offset+limit,total)} / {total}</span>
                  <button className="btn btn-ghost btn-sm" disabled={offset+limit>=total} onClick={()=>setOffset(o=>o+limit)}>Next <Icon name="chevronRight" size={13}/></button>
                </div>
              </div>
            </div>
          </div>
        </div>
      )}

      {sub==='schema' && (
        <div className="card p-5 flex flex-col gap-4">
          <SectionHeader label="Schema" action={<span style={{fontSize:'var(--text-xs)', color:'var(--color-text-muted)'}}>{tables.length} tabel • via /api/db/tables</span>} />
          <div className="grid gap-3" style={{gridTemplateColumns:'repeat(auto-fill, minmax(300px,1fr))'}}>
            {tables.map(t=>(
              <div key={t.name} className="rounded-lg p-3.5" style={{background:'var(--color-bg)', border:'1px solid var(--color-border)'}}>
                <div className="flex items-center gap-2 mb-2">
                  <Icon name="table" size={14} style={{color:'var(--color-accent)'} as any}/>
                  <span className="font-mono font-bold" style={{fontSize:'var(--text-sm)'}}>{t.name}</span>
                  <span className="rounded-full px-1.5 font-bold ml-auto" style={{background:'var(--color-border)', fontSize:10}}>{t.count}</span>
                </div>
                <div className="section-label mb-1">Kolom {t.pk?.length ? `(PK: ${t.pk.join(', ')})` : ''}</div>
                <div className="flex flex-wrap gap-1.5 mb-2">
                  {t.columns.map(c=>(
                    <span key={c} className="rounded px-1.5 py-0.5 font-mono" style={{fontSize:11, background: t.pk?.includes(c)?'var(--color-accent-ghost)':'var(--color-panel)', color: t.pk?.includes(c)?'var(--color-accent)':'var(--color-text-secondary)', border:`1px solid ${t.pk?.includes(c)?'var(--color-accent)':'var(--color-border)'}`}}>{c}{t.pk?.includes(c)?' ★':''}</span>
                  ))}
                </div>
                {t.indexes?.length ? (
                  <div>
                    <div className="section-label mb-1">Index</div>
                    <div className="flex flex-col gap-1">
                      {t.indexes.map(ix=>(
                        <div key={ix.name} className="font-mono" style={{fontSize:11, color:'var(--color-text-muted)'}}>{ix.name}{ix.unique?' (unique)':''}: {ix.columns.join(', ')||'—'}</div>
                      ))}
                    </div>
                  </div>
                ) : null}
              </div>
            ))}
          </div>
        </div>
      )}

      {sub==='sql' && (
        <div className="card p-5 flex flex-col gap-3">
          <SectionHeader label="SQL Editor (admin)" action={<span className="rounded px-1.5 py-0.5 font-mono" style={{fontSize:10, background:'var(--color-warning-ghost)', color:'var(--color-warning-fg)', border:'1px solid var(--color-warning)'}}>INSERT/UPDATE/DELETE memerlukan konfirmasi</span>} />
          <textarea className="input font-mono" value={sql} onChange={e=>setSql(e.target.value)} rows={4} spellCheck={false} placeholder="SELECT * FROM wma_entries LIMIT 20" style={{fontSize:'var(--text-sm)', lineHeight:'1.5'}} />
          <div className="flex items-center gap-2 flex-wrap">
            <button className="btn btn-primary btn-sm" onClick={runSql} disabled={sqlRunning}><Icon name="send" size={13}/> {sqlRunning?'Menjalankan…':'Run'}</button>
            <button className="btn btn-ghost btn-sm" onClick={()=>setSql('SELECT * FROM wma_entries LIMIT 20')}>Contoh: wma_entries</button>
            <button className="btn btn-ghost btn-sm" onClick={()=>setSql('SELECT name FROM sqlite_master WHERE type=\'table\'')}>List tables</button>
            <span style={{fontSize:10, color:'var(--color-text-dim)'}}>1 statement / klik • limit 100 baris • timeout 5s • transaction commit/rollback</span>
          </div>
          {sqlErr && <div className="flex items-center gap-2 px-3 py-2 rounded" style={{background:'var(--color-danger-ghost)', color:'var(--color-danger-fg)', fontSize:'var(--text-xs)'}}><Icon name="alertTriangle" size={13}/>{sqlErr}</div>}
          {(sqlCols.length>0 || sqlRowCount!==null) && (
            <div className="flex flex-col gap-2">
              <div className="flex items-center gap-2" style={{fontSize:'var(--text-xs)', color:'var(--color-text-muted)'}}>
                <span>rowCount: {sqlRowCount??'—'}</span>
                {sqlLastId ? <span>lastRowId: {sqlLastId}</span> : null}
                <span>cols: {sqlCols.join(', ')||'—'}</span>
                {sqlRows.length>=100 && <span style={{color:'var(--color-warning-fg)'}}>truncated 100</span>}
              </div>
              <div className="overflow-auto" style={{maxHeight:400, border:'1px solid var(--color-border)', borderRadius:8}}>
                <table className="data-table" style={{minWidth:560}}>
                  <thead><tr>{sqlCols.map(c=><th key={c}>{c}</th>)}{sqlCols.length===0&&<th>result</th>}</tr></thead>
                  <tbody>
                    {sqlRows.length===0 && <tr><td colSpan={Math.max(1,sqlCols.length)}><EmptyState icon="database" title="Tidak ada baris" description={sqlErr?'Perbaiki SQL dan Run lagi':'No rows'}/></td></tr>}
                    {sqlRows.map((r,i)=>(
                      <tr key={i}>{sqlCols.map(c=>(
                        <td key={c} title={String(r[c]??'')} style={{maxWidth:240, overflow:'hidden', textOverflow:'ellipsis', whiteSpace:'nowrap', fontSize:'var(--text-xs)'}}><span className="font-mono">{fmt(r[c])}</span></td>
                      ))}</tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  )
}

// ─── Manajemen User (admin only) ──────────────────────────────────────────
const UserManagementPanel = () => {
  const role = getAuthRole()
  const selfUser = getAuthUser()
  const [users, setUsers] = useState<{username:string;role:string;created_at:number;created_by:string}[]>([])
  const [loading, setLoading] = useState(false)
  const [err, setErr] = useState('')
  const [newUser, setNewUser] = useState('')
  const [newPass, setNewPass] = useState('')
  const [newRole, setNewRole] = useState<'admin'|'user'>('user')
  if (role !== 'admin') return null
  const base = (()=>{ try{ const h=location.hostname||'localhost'; return `http://${h}:8766` }catch{return 'http://localhost:8766'} })()
  const token = getAuthToken()
  const hdr: Record<string,string> = { 'Content-Type':'application/json', ...(token?{ Authorization: `Bearer ${token}` }: {}) }
  const fetchUsers = useCallback(async () => {
    setLoading(true); setErr('')
    try {
      const r = await fetch(`${base}/api/users`, { headers: hdr })
      const j = await r.json()
      if (!j.ok) throw new Error(j.msg||'gagal')
      setUsers(j.users||[])
    } catch(e:unknown){ setErr(e instanceof Error?e.message:String(e)) }
    finally{ setLoading(false) }
  }, [base, token])
  useEffect(()=>{ fetchUsers() }, [fetchUsers])
  const add = async () => {
    if (!newUser.trim()||!newPass) { pushToast('Username & password wajib','warning'); return }
    try{
      const r = await fetch(`${base}/api/users`, { method:'POST', headers: hdr, body: JSON.stringify({ username:newUser.trim(), password:newPass, role:newRole }) })
      const j = await r.json()
      if (!j.ok) throw new Error(j.msg)
      pushToast(`User ${newUser} dibuat`,'success'); setNewUser(''); setNewPass(''); fetchUsers()
    }catch(e:unknown){ pushToast(e instanceof Error?e.message:String(e),'danger') }
  }
  const del = async (u:string) => {
    if (u.toLowerCase()===selfUser.toLowerCase()) { pushToast('Tidak bisa hapus diri sendiri','warning'); return }
    if (!confirm(`Hapus user ${u}?`)) return
    try{
      let r = await fetch(`${base}/api/users/${encodeURIComponent(u)}`, { method:'DELETE', headers: hdr })
      if (r.status===404||r.status===405) {
        r = await fetch(`${base}/api/users/delete`, { method:'POST', headers: hdr, body: JSON.stringify({ username:u }) })
      }
      const j = await r.json()
      if (!j.ok) throw new Error(j.msg)
      pushToast(`User ${u} dihapus`,'success'); fetchUsers()
    }catch(e:unknown){ pushToast(e instanceof Error?e.message:String(e),'danger') }
  }
  const reset = async (u:string) => {
    const np = prompt(`Password baru untuk ${u} (min 6 char):`)
    if (!np) return
    try{
      const r = await fetch(`${base}/api/users/reset`, { method:'POST', headers: hdr, body: JSON.stringify({ username:u, password:np }) })
      const j = await r.json()
      if (!j.ok) throw new Error(j.msg)
      pushToast(`Password ${u} direset`,'success')
    }catch(e:unknown){ pushToast(e instanceof Error?e.message:String(e),'danger') }
  }
  return (
    <div className="card p-6 flex flex-col gap-4">
      <SectionHeader label="Manajemen User (admin)" action={<button className="btn btn-ghost btn-sm" onClick={fetchUsers}><Icon name="refresh" size={13}/> Refresh</button>} />
      {err && <div style={{color:'var(--color-danger-fg)', fontSize:'var(--text-xs)'}}>{err}</div>}
      <div className="grid gap-2" style={{gridTemplateColumns:'1fr 1fr 120px auto'}}>
        <input className="input" placeholder="username (3-32 a-z0-9._-)" value={newUser} onChange={e=>setNewUser(e.target.value)} />
        <input className="input" type="password" placeholder="password min 6" value={newPass} onChange={e=>setNewPass(e.target.value)} />
        <select className="input" value={newRole} onChange={e=>setNewRole(e.target.value as any)}>
          <option value="user">user</option><option value="admin">admin</option>
        </select>
        <button className="btn btn-primary btn-sm" onClick={add}>Tambah</button>
      </div>
      <div className="overflow-x-auto">
        <table className="data-table">
          <thead><tr><th>Username</th><th>Role</th><th>Dibuat</th><th>Aksi</th></tr></thead>
          <tbody>
            {loading && <tr><td colSpan={4} style={{color:'var(--color-text-muted)', fontSize:'var(--text-xs)'}}>Memuat…</td></tr>}
            {!loading && users.length===0 && <tr><td colSpan={4}><EmptyState icon="user" title="Belum ada user" description="Tambah user via form di atas"/></td></tr>}
            {users.map(u=>(
              <tr key={u.username}>
                <td><span className="font-mono" style={{color:'var(--color-accent-fg)'}}>{u.username}</span> {u.username===selfUser && <span style={{fontSize:10, background:'var(--color-accent-ghost)', color:'var(--color-accent)', padding:'2px 6px', borderRadius:4}}>YOU</span>}</td>
                <td><Badge label={u.role.toUpperCase()} status={u.role==='admin'?'violet':'muted'} /></td>
                <td style={{fontSize:'var(--text-xs)', color:'var(--color-text-muted)'}}>{u.created_by||'-'}</td>
                <td>
                  <div className="flex gap-1.5">
                    <button className="btn btn-ghost btn-sm" onClick={()=>reset(u.username)}><Icon name="refresh" size={12}/> Reset</button>
                    <button className="btn btn-ghost btn-sm" disabled={u.username===selfUser} onClick={()=>del(u.username)} style={{color:'var(--color-danger)'}}><Icon name="x" size={12}/> Hapus</button>
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}
let _hubUrlRefVal = ''
function hubUrlRef(){ return _hubUrlRefVal }

// ─── Page: Pengaturan ─────────────────────────────────────────────────────────

const PagePengaturan = ({ hubUrl, logCap, onApply }: {
  hubUrl: string
  logCap: number
  onApply: (url: string, cap: number) => void
}) => {
  const [local, setLocal]     = useState(hubUrl)
  const [bufSize, setBufSize] = useState(logCap)
  const [saved, setSaved]     = useState(false)

  const handleSave = () => {
    const cap = Math.min(10000, Math.max(100, bufSize || 2000))
    onApply(local.trim() || 'ws://localhost:8765', cap)
    setSaved(true)
    pushToast('Pengaturan disimpan', 'success')
    setTimeout(() => setSaved(false), 2000)
  }

  return (
    <div className="flex flex-col gap-5" style={{ maxWidth: 760 }}>
      <div className="card p-6 flex flex-col gap-4">
        <SectionHeader label="Koneksi Hub" />
        <div>
          <label className="block font-semibold mb-1.5" style={{ fontSize: 'var(--text-xs)', color: 'var(--color-text-secondary)' }}>
            WebSocket URL
          </label>
          <input className="input font-mono" value={local} onChange={e => setLocal(e.target.value)}
            placeholder="ws://localhost:8765" />
          <div className="mt-1" style={{ fontSize: 'var(--text-label)', color: 'var(--color-text-muted)' }}>
            Perubahan diterapkan saat Simpan diklik
          </div>
        </div>
        <div>
          <label className="block font-semibold mb-1.5" style={{ fontSize: 'var(--text-xs)', color: 'var(--color-text-secondary)' }}>
            Buffer Log (baris)
          </label>
          <input className="input font-mono" type="number" value={bufSize}
            onChange={e => setBufSize(+e.target.value)} min={100} max={10000} />
        </div>
      </div>

      <div className="card p-6" style={{ opacity: 0.6 }}>
        <SectionHeader label="WMA Polling" />
        <div className="flex items-center gap-2 rounded-lg px-3.5 py-2.5"
          style={{ background: 'var(--color-bg)', fontSize: 'var(--text-sm)', color: 'var(--color-text-secondary)' }}>
          <Icon name="info" size={16} className="shrink-0" />
          Interval polling WMA diatur di skrip Python (wma.py) dan tidak dapat diubah dari sini.
        </div>
      </div>

      <div className="flex gap-2">
        <button className={`btn btn-md ${saved ? 'btn-success' : 'btn-primary'}`} onClick={handleSave}>
          <Icon name="save" size={15} /> {saved ? 'Tersimpan!' : 'Simpan Pengaturan'}
        </button>
        <button className="btn btn-ghost btn-md" onClick={() => { setLocal(hubUrl); setBufSize(logCap) }}>
          Reset
        </button>
      </div>

      <UserManagementPanel />
    </div>
  )
}

// ─── App ──────────────────────────────────────────────────────────────────────

export default function App() {
  const [page,      setPage]      = useState<Page>('ringkasan')
  const [collapsed, setCollapsed] = useState(false)
  const [hubUrl,    setHubUrl]    = useState(() => {
    const fallback = defaultHubUrl()
    try {
      const saved = localStorage.getItem('nsh-hub-url')
      if (!saved) return fallback
      if (/localhost|127\.0\.0\.1/.test(saved)) {
        const host = location.hostname
        if (host && !/localhost|127\.0\.0\.1/.test(host)) return fallback
      }
      return saved
    } catch { return fallback }
  })
  const [logCap,    setLogCap]    = useState(() => {
    try { return parseInt(localStorage.getItem('nsh-log-cap') || '200', 10) || 200 }
    catch { return 200 }
  })
  const [mobileNav, setMobileNav] = useState(false)
  const [authChecked, setAuthChecked] = useState(false)
  const [isAuthed, setIsAuthed] = useState(false)
  const isLoginPath = (()=>{ try{ return location.pathname==='/login' || location.hash==='#/login' }catch{return false}})()

  useEffect(()=>{
    const tok = getAuthToken()
    if (!tok) { setAuthChecked(true); setIsAuthed(false); return }
    const base = (()=>{ try{ const h=location.hostname||'localhost'; return `http://${h}:8766` }catch{return 'http://localhost:8766'} })()
    fetch(`${base}/api/me`, { headers: { Authorization: `Bearer ${tok}` } })
      .then(r=>{ if(r.status===401) throw new Error('unauth'); return r.json() })
      .then(j=>{
        if (j.ok) setIsAuthed(true)
        else { clearAuth(); setIsAuthed(false) }
      }).catch(()=>{ clearAuth(); setIsAuthed(false) })
      .finally(()=> setAuthChecked(true))
  },[])

  // derive ws url with token
  const hubWsUrl = (()=>{ try{ const u=new URL(hubUrl); const tok=getAuthToken(); if(tok) u.searchParams.set('token', tok); return u.toString(); }catch{ const tok=getAuthToken(); if(tok) return hubUrl + (hubUrl.includes('?')?'&':'?') + `token=${encodeURIComponent(tok)}`; return hubUrl }})()
  const hub = useHub(hubWsUrl, logCap)
  const connStatus: ConnStatus =
    hub.conn === 'open' ? 'connected' : hub.conn === 'connecting' ? 'connecting' : 'disconnected'

  const hubPendingRef = useRef<Set<string>>(new Set())
  useEffect(() => {
    for (const r of hub.results ?? []) {
      if (!hubPendingRef.current.has(r.id)) continue
      hubPendingRef.current.delete(r.id)
      pushToast(r.ok ? `OK: ${r.action} — ${r.msg}` : `Gagal: ${r.action} — ${r.msg}`, r.ok ? 'success' : 'danger')
    }
  }, [hub.results])

  const doHubRestart = () => {
    if (!connected) {
      pushToast('Hub offline — tidak bisa restart', 'warning')
      return
    }
    if (!window.confirm('Restart HUB? Semua koneksi putus ±3 detik lalu tersambung otomatis. Modul supervisi ikut nyala lagi.')) return
    const id = hub.sendCommand('hub_restart')
    if (!id) {
      pushToast('Hub offline — perintah tidak dikirim', 'warning')
      return
    }
    hubPendingRef.current.add(id)
    pushToast('Perintah restart HUB dikirim…', 'accent')
  }

  const applySettings = (url: string, cap: number) => {
    setHubUrl(url)
    setLogCap(cap)
    try {
      localStorage.setItem('nsh-hub-url', url)
      localStorage.setItem('nsh-log-cap', String(cap))
    } catch { /* storage tidak tersedia */ }
  }

  const doLogout = async () => {
    const token = getAuthToken()
    try{
      const base = (()=>{ try{ const h=location.hostname||'localhost'; return `http://${h}:8766` }catch{return 'http://localhost:8766'} })()
      await fetch(`${base}/api/logout`, { method:'POST', headers: { 'Content-Type':'application/json', ...(token?{Authorization:`Bearer ${token}`}:{}) }, body: JSON.stringify({ token }) })
    }catch{}
    clearAuth()
    location.href = '/login'
  }

  const conn    = CONN_CFG[connStatus]
  const connected = connStatus === 'connected'
  const sidebarW  = collapsed ? 56 : 240

  // Guard: belum auth -> LoginPage
  if (!authChecked) {
    return <div className="flex items-center justify-center" style={{height:'100vh', background:'var(--color-bg)', color:'var(--color-text-muted)'}}>Memeriksa sesi…</div>
  }
  if (!isAuthed || isLoginPath) {
    return <><LoginPage onSuccess={()=>{ location.href='/'}} /><ToastContainer /></>
  }

  const SidebarInner = ({ mobile = false }: { mobile?: boolean }) => {
    const visibleNav = getAuthRole()==='admin' ? NAV : NAV.filter((n: typeof NAV[number])=>n.id!=='database')
    return (
    <div className="flex flex-col overflow-hidden h-full"
      style={{
        width: mobile ? 240 : sidebarW,
        background: 'var(--color-bg)',
        borderRight: '1px solid var(--color-border-subtle)',
        transition: 'width 0.25s ease-out',
        flexShrink: 0,
      }}>
      {/* Logo */}
      <div className="flex items-center gap-2.5 shrink-0 px-3.5 py-5"
        style={{
          borderBottom: '1px solid var(--color-border-subtle)',
          ...(collapsed && !mobile ? { justifyContent: 'center', paddingLeft: 0, paddingRight: 0 } : {}),
        }}>
        {(collapsed && !mobile) ? (
          <button className="btn btn-ghost btn-sm" title="Buka menu"
            onClick={() => setCollapsed(false)}>
            <Icon name="menu" size={18} />
          </button>
        ) : (
          <>
            <div className="flex items-center justify-center rounded-[8px] shrink-0"
              style={{ width: 28, height: 28, background: 'linear-gradient(135deg, var(--color-accent), var(--color-accent-d))' }}>
              <Icon name="printer" size={15} style={{ color: 'var(--color-bg)', strokeWidth: 2.5 } as any} />
            </div>
            <div className="overflow-hidden">
              <div className="font-extrabold leading-tight tracking-tight whitespace-nowrap"
                style={{ fontSize: 'var(--text-md)', color: 'var(--color-text)', letterSpacing: '-0.02em' }}>
                NSH HUB
              </div>
              <div className="section-label whitespace-nowrap">Print Monitor</div>
            </div>
            {!mobile && (
              <button className="btn btn-ghost btn-sm ml-auto p-1 shrink-0" title="Sembunyikan menu"
                onClick={() => setCollapsed(true)}>
                <Icon name="chevronLeft" size={14} />
              </button>
            )}
          </>
        )}
      </div>

      {/* Nav items - Database hanya admin */}
      <div className="flex-1 flex flex-col gap-0.5 px-2 py-2.5">
        {visibleNav.map(item => (
          <button key={item.id}
            className={`nav-item w-full border-none text-left ${page === item.id ? 'active' : ''}`}
            onClick={() => { setPage(item.id); setMobileNav(false) }}
            title={collapsed && !mobile ? item.label : undefined}>
            <Icon name={item.icon} size={18} className="shrink-0" />
            {(!collapsed || mobile) && (
              <span className="overflow-hidden text-ellipsis whitespace-nowrap">{item.label}</span>
            )}
          </button>
        ))}
      </div>

      {/* Connection footer */}
      <div className="flex items-center gap-2 shrink-0 px-3.5 py-3"
        style={{ borderTop: '1px solid var(--color-border-subtle)' }}>
        <StatusDot status={conn.status} live={conn.live} shake={connStatus === 'disconnected'} />
        {(!collapsed || mobile) && (
          <div className="overflow-hidden min-w-0">
            <div className="font-semibold whitespace-nowrap"
              style={{ fontSize: 'var(--text-xs)', color: 'var(--color-text-secondary)' }}>
              {conn.label}
            </div>
            <div className="font-mono overflow-hidden text-ellipsis whitespace-nowrap"
              style={{ fontSize: 10, color: 'var(--color-text-dim)' }}>
              {hubUrl}
            </div>
          </div>
        )}
      </div>
    </div>
    )
  }

  return (
    <div className="flex overflow-hidden" style={{ height: '100vh', background: 'var(--color-bg)' }}>

      {/* Desktop sidebar */}
      <div className="sidebar-desktop flex shrink-0">
        <SidebarInner />
      </div>

      {/* Mobile nav overlay */}
      {mobileNav && (
        <>
          <div onClick={() => setMobileNav(false)}
            className="fixed inset-0 z-[199]" style={{ background: '#00000070' }} />
          <div className="fixed inset-y-0 left-0 z-[200]">
            <SidebarInner mobile />
          </div>
        </>
      )}

      {/* Main column */}
      <div className="flex flex-col flex-1 overflow-hidden min-w-0">

        {/* Topbar */}
        <header className="flex items-center gap-3 shrink-0 px-6"
          style={{ height: 60, background: 'var(--color-bg)', borderBottom: '1px solid var(--color-border-subtle)' }}>
          {/* Mobile menu */}
          <button className="mobile-menu-btn btn btn-ghost btn-sm" style={{ display: 'none' }}
            onClick={() => setMobileNav(true)}>
            <Icon name="menu" size={18} />
          </button>

          <h1 className="font-bold tracking-tight shrink-0"
            style={{ fontSize: 'var(--text-xl)', color: 'var(--color-text)', letterSpacing: '-0.02em' }}>
            {PAGE_TITLE[page]}
          </h1>

          <div className="flex items-center gap-4 ml-auto">
            {/* HUB WebSocket + Log Writer (pindah dari Kesehatan Modul) */}
            <div className="hidden md:flex items-center gap-3">
              <span className="flex items-center gap-1.5" title="Kanal real-time dashboard">
                <StatusDot status={connected ? 'success' : 'danger'} live={connected} shake={!connected} />
                <span style={{ fontSize: 'var(--text-label)', color: 'var(--color-text-muted)' }}>HUB WebSocket</span>
              </span>
              <button className="btn btn-ghost btn-sm" title="Restart HUB" onClick={doHubRestart}
                style={{ color: 'var(--color-warning-fg)' }}>
                <Icon name="refresh" size={13} /> Restart HUB
              </button>
              <span className="flex items-center gap-1.5" title="Buffer log hub">
                <StatusDot status={connected ? 'success' : 'danger'} live={connected} shake={!connected} />
                <span style={{ fontSize: 'var(--text-label)', color: 'var(--color-text-muted)' }}>Log Writer</span>
              </span>
            </div>
            <LiveClock />
            <div className="flex items-center gap-2">
              <span className="hidden md:inline font-semibold" style={{fontSize:'var(--text-sm)', color:'var(--color-text)'}}>{getAuthUser()||'user'}</span>
              <span className="hidden md:inline rounded-full px-1.5 py-0.5 font-bold uppercase" style={{fontSize:10, background:getAuthRole()==='admin'?'var(--color-violet-ghost)':'var(--color-muted-ghost)', color:getAuthRole()==='admin'?'var(--color-violet-fg)':'var(--color-muted-fg)'}}>{getAuthRole()||'user'}</span>
              <div className="flex items-center justify-center rounded-full" style={{ width: 34, height: 34, background: 'linear-gradient(135deg, var(--color-accent), var(--color-violet))' }}>
                <Icon name="user" size={16} style={{ color: '#fff', strokeWidth: 2 } as any} />
              </div>
              <button className="btn btn-ghost btn-sm" onClick={doLogout} title="Logout"><Icon name="x" size={14}/> Logout</button>
            </div>
          </div>
        </header>

        {/* Page content */}
        <main className="flex-1 overflow-y-auto page-content" style={{ padding: 'var(--spacing-page)' }}>
          <div key={page} className="animate-fade-rise" style={{ minHeight: '100%' }}>
            {page === 'ringkasan'  && <PageRingkasan connected={connected} logs={hub.logs} entriesAll={hub.entriesAll} entries={hub.entries} statuses={hub.statuses} verified={hub.verified} modules={hub.modules} live={connected} sendCommand={hub.sendCommand} results={hub.results} kmaRoutes={hub.kma.routes} weSeen={hub.weSeen} wePrinted={hub.wePrinted} weVerified={hub.weVerified} weAuto={hub.weAuto} outputFiles={hub.outputFiles} onGo={(p) => setPage(p)} />}
            {page === 'nshlite'   && <PageNshLite connected={connected} sendCommand={hub.sendCommand} results={hub.results} outputFiles={hub.outputFiles} />}
            {page === 'kma'       && <PageKma connected={connected} routes={hub.kma.routes} printers={hub.kma.printers} rules={hub.kma.rules} stats={hub.kma.stats} sendCommand={hub.sendCommand} results={hub.results} />}
            {page === 'we'        && <PageWe live={connected} sendCommand={hub.sendCommand} results={hub.results} weSeen={hub.weSeen} wePrinted={hub.wePrinted} weVerified={hub.weVerified} weAuto={hub.weAuto} hubUrl={hubUrl} weRefresh={hub.weRefresh} />}
            {page === 'wma'       && <PageWma entriesAll={hub.entriesAll} statuses={hub.statuses} verified={hub.verified} live={connected} sendCommand={hub.sendCommand} results={hub.results} />}
            {page === 'kmma'      && <PageKmma entries={hub.entries} statuses={hub.statuses} details={hub.details} verified={hub.verified} live={connected} sendCommand={hub.sendCommand} results={hub.results} timer={hub.kmmaTimer} />}
            {page === 'log'       && <PageLog logs={hub.logs} live={connected} />}
            {page === 'pengaturan'&& <PagePengaturan hubUrl={hubUrl} logCap={logCap} onApply={applySettings} />}
            {page === 'database'  && <PageDatabase hubUrl={hubUrl} />}
          </div>
        </main>
      </div>

      <ToastContainer />
    </div>
  )
}
