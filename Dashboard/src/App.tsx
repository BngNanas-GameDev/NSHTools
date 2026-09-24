import { useState, useEffect, useRef, useCallback } from 'react'
import { useHub, defaultHubUrl, type HubLogEntry, type WmaEntry, type NotaStatus, type ModuleState, type HubStep, type CommandResult, type SendCommandOpts, type KmaRoute, type KmaPrinter, type KmaRule, type KmaStats, type KmmaTimer, type OutputFile } from './hooks/useHub'
import { pushToast, onToast, type ToastItem } from './toast'

// ─── Shared live-data helpers ───────────────────────────────────────────────

function guessLevel(msg: string): StatusLevel {
  if (/gagal|error|fail|expired|!!|✕/i.test(msg)) return 'danger'
  if (/warn|skip|lewati|melebihi|tidak |belum |retry|⚠/i.test(msg)) return 'warning'
  return 'success'
}

// ─── Module folder bar (Lite: INPUT/OUTPUT/UDA, KMA: INPUT/RETURN/UDA) ──────
// Ambil via get_folders, simpan via set_folders (hub tulis lite.json/route.json).
// Hub membalas "restart modul agar berlaku" — restart via tombol power.

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

function PathBar({ fields, values, onChange, onSave, saving }: {
  fields: FolderField[]
  values: Record<string, string>
  onChange: (next: Record<string, string>) => void
  onSave: () => void
  saving: boolean
}) {
  const [open, setOpen] = useState(true)
  return (
    <div className="card px-5 py-4">
      <SectionHeader
        label="Path Folder"
        action={
          <div className="flex items-center gap-1.5">
            <button className="btn btn-primary btn-sm" onClick={onSave} disabled={saving}>
              <Icon name="save" size={13} /> {saving ? 'Menyimpan…' : 'Simpan Path'}
            </button>
            <button className="btn btn-ghost btn-sm" title={open ? 'Sembunyikan path' : 'Tampilkan path'}
              onClick={() => setOpen(o => !o)}>
              <Icon name="chevronDown" size={14}
                style={{ transform: open ? undefined : 'rotate(-90deg)', transition: 'transform 0.2s' } as any} />
            </button>
          </div>
        }
      />
      {open && (
      <div className="grid gap-2.5 three-col" style={{ gridTemplateColumns: `repeat(${fields.length > 3 ? 2 : fields.length}, 1fr)` }}>
        {fields.map(f => (
          <div key={f.key}>
            <div className="section-label mb-1">{f.label}</div>
            <input
              className="input font-mono"
              value={values[f.key] ?? ''}
              onChange={e => onChange({ ...values, [f.key]: e.target.value })}
              placeholder={`C:\\…`}
              spellCheck={false}
            />
          </div>
        ))}
      </div>
      )}
    </div>
  )
}

// ─── Types ────────────────────────────────────────────────────────────────────

type Page = 'ringkasan' | 'nshlite' | 'kma' | 'wma' | 'kmma' | 'log' | 'pengaturan'
type ConnStatus = 'connected' | 'connecting' | 'disconnected'
type StatusLevel = 'success' | 'warning' | 'danger' | 'violet' | 'accent' | 'muted'

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

const MODULE_HEALTH = [
  { name: 'NSH Lite',     modKey: 'lite', ctl: true,  desc: 'Imposisi & antrean berjalan normal' },
  { name: 'KMA Router',   modKey: 'kma',  ctl: true,  desc: 'Routing file ke hotfolder printer' },
  { name: 'WMA Polling',  modKey: 'wma',  ctl: true,  desc: 'Agregasi entri nota' },
  { name: 'KMMA Tracker', modKey: 'kmma', ctl: true,  desc: 'Otomasi step KonsepMedia' },
]

interface ModulTab { label: string; value: number | string; level: StatusLevel }

const DASH_TABS: ModulTab[] = [
  { label: 'Antrian Aktif', value: '—', level: 'accent' },
  { label: 'Selesai', value: '—', level: 'success' },
  { label: 'Menunggu Aksi', value: '—', level: 'warning' },
  { label: 'Gagal', value: '—', level: 'danger' },
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

const PageRingkasan = ({ connected, logs, entriesAll, statuses, modules, live, sendCommand, results, kmaRoutes }: {
  connected: boolean
  logs: HubLogEntry[]
  entriesAll: WmaEntry[]
  statuses: Record<string, NotaStatus>
  modules: Record<string, ModuleState>
  live: boolean
  sendCommand: (action: string, opts?: SendCommandOpts) => string | null
  results: CommandResult[]
  kmaRoutes: KmaRoute[]
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
  const feed = logs.slice(-20).reverse().map((l, i) => ({
    ts: l.ts, module: l.module, msg: l.msg, level: guessLevel(l.msg), key: `live-${i}-${l.ts}`,
  }))

  // 4 tab pipeline WMA/KMMA dari tampilan SEHARIAN (.entries_all + status),
  // bukan antrean live — agar tracker tetap hidup walau antrean kosong
  const okAll = entriesAll.filter(e => statuses[e.nota]?.status === 'ok').length
  const errAll = entriesAll.filter(e => statuses[e.nota]?.status === 'error').length
  const manAll = entriesAll.filter(e => statuses[e.nota]?.status === 'manual').length
  const pipeTabs: ModulTab[] = [
    { label: 'Antrian Aktif', value: entriesAll.length - okAll - errAll - manAll, level: 'accent' },
    { label: 'Selesai', value: okAll, level: 'success' },
    { label: 'Menunggu Aksi', value: manAll, level: 'warning' },
    { label: 'Gagal', value: errAll, level: 'danger' },
  ]
  // 4 tab KMA dari daftar file router (PENDING/SELESAI/SKIP/GAGAL)
  const kmaLv = (kmaRoutes ?? []).map(r => kmaStatus(r.status).label)
  const kmaTabs: ModulTab[] = [
    { label: 'Antrian Aktif', value: kmaLv.filter(l => l === 'PENDING').length, level: 'accent' },
    { label: 'Selesai', value: kmaLv.filter(l => l === 'SELESAI' || l === 'RICOH').length, level: 'success' },
    { label: 'Menunggu Aksi', value: kmaLv.filter(l => l === 'SKIP').length, level: 'warning' },
    { label: 'Gagal', value: kmaLv.filter(l => l === 'GAGAL').length, level: 'danger' },
  ]
  const tabsFor = (modKey: string | null): ModulTab[] => {
    if (modKey === 'kma') return kmaTabs
    if (modKey === 'wma' || modKey === 'kmma') return pipeTabs
    return DASH_TABS // lite: belum ada backend
  }

  const health = MODULE_HEALTH.map(m => {
    const mod = m.modKey ? modules[m.modKey] : undefined
    if (!mod) return { ...m, status: 'muted' as StatusLevel, lastRun: '—', desc: 'Menunggu koneksi hub' }
    return { ...m, status: (mod.connected ? 'success' : 'danger') as StatusLevel, lastRun: mod.ts || '—' }
  })
  const feedRef = useRef<HTMLDivElement>(null)

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

      {/* Blok Modul: 4 kartu, tiap kartu 4 tab + tombol power */}
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
              <div className="grid grid-cols-2 gap-1.5">
                {tabsFor(m.modKey).map((t, j) => (
                  <div key={j} className="rounded-md px-2 py-1.5" style={{ background: 'var(--color-panel)' }}>
                    <div style={{ fontSize: 10, color: 'var(--color-text-muted)' }}>{t.label}</div>
                    <div className="font-bold" style={{ fontSize: 'var(--text-md)', color: STATUS_COLOR[t.level] }}>{t.value}</div>
                  </div>
                ))}
              </div>
            </div>
          ))}
        </div>
      </div>

      {/* Aktivitas Live (full width, di bawah blok Modul) */}
      <div className="card overflow-hidden flex flex-col">
        <div className="flex items-center justify-between shrink-0 px-5 py-4"
          style={{ borderBottom: '1px solid var(--color-border)' }}>
          <span className="section-label">Aktivitas Live</span>
          <div className="flex items-center gap-1.5">
            <StatusDot status={connected ? 'success' : 'danger'} live={connected} shake={!connected} />
            <span style={{ fontSize: 'var(--text-label)', color: 'var(--color-text-muted)' }}>
              {connected ? 'Live' : 'Offline'}
            </span>
          </div>
        </div>
        <div ref={feedRef} className="overflow-y-auto" style={{ maxHeight: 420 }}>
          {feed.map((item, i) => (
            <div key={i} className={`flex items-start gap-3 px-5 py-2.5 ${i === 0 ? 'animate-slide-in-top' : ''}`}
              style={{ borderBottom: '1px solid var(--color-border-subtle)' }}>
              <span className="font-mono shrink-0 pt-0.5"
                style={{ fontSize: 'var(--text-label)', color: 'var(--color-text-muted)' }}>{item.ts}</span>
              <div className="rounded-full shrink-0 mt-[7px]"
                style={{ width: 4, height: 4, background: STATUS_COLOR[item.level] }} />
              <span className="shrink-0 pt-0.5" style={{ fontSize: 'var(--text-xs)', color: 'var(--color-text-muted)', minWidth: 72 }}>
                {item.module}
              </span>
              <span style={{ fontSize: 'var(--text-sm)', color: '#cbd5e1' }}>{item.msg}</span>
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
function OutputWatcher({ connected, sendCommand, results, files, title = 'Hasil Impose', subtitle }: {
  connected: boolean
  sendCommand: (action: string, opts?: SendCommandOpts) => string | null
  results: CommandResult[]
  files: OutputFile[]
  title?: string
  subtitle?: string
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
          <label className="flex items-center gap-2 cursor-pointer"
            style={{ fontSize: 'var(--text-xs)', color: 'var(--color-text-muted)' }}>
            <input type="checkbox" checked={sel.size === files.length && files.length > 0} onChange={toggleAll} />
            Pilih semua
          </label>
        )}
        {files.map(f => (
          <label key={f.name} className="flex items-center gap-2 rounded-lg px-2.5 py-2 cursor-pointer"
            style={{ background: sel.has(f.name) ? 'var(--color-nav-active)' : 'var(--color-bg)' }}>
            <input type="checkbox" checked={sel.has(f.name)} onChange={() => toggle(f.name)} />
            <span className="font-mono truncate flex-1" title={f.name} style={{ fontSize: 'var(--text-xs)' }}>{f.name}</span>
            <span className="font-mono shrink-0" style={{ fontSize: 10, color: 'var(--color-text-dim)' }}>{fmtSize(f.size)}</span>
          </label>
        ))}
        {files.length === 0 && !loading && (
          <div style={{ fontSize: 'var(--text-xs)', color: 'var(--color-text-dim)' }}>
            {connected ? 'Output kosong' : 'Hub offline'}
          </div>
        )}
      </div>
      <div className="grid grid-cols-2 gap-2">
        <button className="btn btn-primary btn-sm" disabled={sel.size === 0} onClick={() => doMove('develop')}>
          <Icon name="send" size={13} /> DEVELOP ({sel.size})
        </button>
        <button className="btn btn-secondary btn-sm" disabled={sel.size === 0} onClick={() => doMove('ricoh')}>
          <Icon name="send" size={13} /> RICOH ({sel.size})
        </button>
      </div>
      <div>
        <div className="section-label mb-1.5">Operator</div>
        <div className="flex gap-1.5 flex-wrap">
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
      </div>
      <div style={{ fontSize: 10, color: 'var(--color-text-dim)' }}>ke folder DEVELOP / RICOH (setelan KMA) • tercatat + Selesai di .54</div>
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
      {/* Path folder */}
      <PathBar
        fields={[
          { key: 'input_folder', label: 'INPUT' },
          { key: 'output_folder', label: 'OUTPUT' },
          { key: 'uda_folder', label: 'UDA' },
        ]}
        values={folders.values}
        onChange={folders.setValues}
        onSave={() => folders.save(folders.values)}
        saving={folders.saving}
      />
      {/* Hasil Impose: file OUTPUT siap kirim */}
      <OutputWatcher connected={connected} sendCommand={sendCommand} results={results} files={outputFiles}
        title="Hasil Impose" subtitle="File di OUTPUT siap kirim ke DEVELOP / RICOH" />
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

  const files = routes.map((r, i) => ({ ...r, ...kmaStatus(r.status), key: `${r.file}||${r.ts}||${i}` }))

  return (
    <div className="flex flex-col gap-4">
      <PathBar
        fields={[
          { key: 'input_folder', label: 'INPUT' },
          { key: 'return_folder', label: 'RETURN' },
          { key: 'uda_folder', label: 'UDA' },
          { key: 'ricoh_folder', label: 'RICOH (watcher, kosong=mati)' },
        ]}
        values={folders.values}
        onChange={folders.setValues}
        onSave={() => folders.save(folders.values)}
        saving={folders.saving}
      />
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

      {/* Status printer */}
      <div className="card p-5">
        <SectionHeader label="Status Printer" />
          <div className="flex flex-col gap-2.5">
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
                    {p.active && (
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

  const doVerify = (nota: string) => {
    if (!live) {
      pushToast('Hub offline — perintah tidak dikirim', 'warning')
      return
    }
    const id = sendCommand('verify', { nota })
    if (!id) {
      pushToast('Hub offline — perintah tidak dikirim', 'warning')
      return
    }
    pendingRef.current.add(id)
  }

  const isVerified = (nota: string) => verified.includes(nota)

  // Kiri: belum diproses KMMA (Antrian) | Tengah: sudah diproses/gagal/manual/retry, belum verified (Menunggu Aksi) | Kanan: verified (Selesai)
  const left = entriesAll.filter(e => !statuses[e.nota] && !isVerified(e.nota))
  const mid = entriesAll.filter(e => {
    const s = statuses[e.nota]?.status
    return (s === 'ok' || s === 'error' || s === 'manual' || s === 'retry') && !isVerified(e.nota)
  })
  const right = entriesAll.filter(e => isVerified(e.nota))

  const wmaReason = (nota: string): string | null => {
    const m = statuses[nota]?.msg ?? ''
    if (/tidak ada di km/i.test(m)) return 'Nota tidak ada di KM'
    if (/bahan tidak sesuai/i.test(m)) return 'Bahan Tidak Sesuai'
    if (/jumlah tidak sesuai/i.test(m)) return 'QTY tidak sesuai'
    if (/tunggu 3 mnt|retry/i.test(m)) return 'Retry otomatis 3 mnt'
    return null
  }

  const row = (e: WmaEntry, showVerify: boolean) => {
    const reason = wmaReason(e.nota)
    const tag = [e.sides ? e.sides.toUpperCase() : '', e.laminating ? e.laminating.toUpperCase() : ''].filter(Boolean).join('/')
    return (
    <div key={`${e.nota}||${e.bahan ?? ''}||${e.sides ?? ''}||${e.laminating ?? ''}`} className="rounded-lg px-3.5 py-2.5" style={{ background: 'var(--color-bg)' }}>
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
              <button className="btn btn-primary btn-sm" onClick={() => doVerify(e.nota)}>
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
      {rack('Selesai', 'Sudah diverifikasi', 'success', right, false, 'Belum ada yang selesai')}
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

const PageKmma = ({ entries, statuses, details, live, sendCommand, results, timer }: {
  entries: WmaEntry[]
  statuses: Record<string, NotaStatus>
  details: Record<string, HubStep[]>
  live: boolean
  sendCommand: (action: string, opts?: SendCommandOpts) => string | null
  results: CommandResult[]
  timer: KmmaTimer | null
}) => {
  const notas = [...new Set(entries.map(e => e.nota))]
  const [openNota, setOpenNota]   = useState<string | null>(null)
  const [overrides, setOverrides] = useState<Record<string, number>>({})
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

  const open = openNota && notas.includes(openNota) ? openNota : (notas[0] ?? null)

  const racks = notas.map(n => {
    const rows = entries.filter(e => e.nota === n)
    const first = rows[0]
    const st = statuses[n]
    const real = details[n]
    const stName = st?.status
    return {
      nota: n,
      customer: rows.map(r => r.customer).find(Boolean) || '—',
      bahan: [...new Set(rows.map(r => r.bahan ?? '-'))].join(', '),
      qty: rows.reduce((a, r) => a + (r.total_qty ?? 0), 0),
      operator: rows.map(r => r.operator).find(Boolean) || '—',
      status: (stName === 'ok' ? 'success' : stName === 'manual' ? 'warning' : stName === 'error' ? 'danger' : 'accent') as StatusLevel,
      statusLabel: stName === 'ok' ? 'SELESAI' : stName === 'manual' ? 'MANUAL' : stName === 'error' ? 'ERROR' : 'ANTRI',
      steps: real?.length ? real.map(kmmaStepFromHub) : kmmaStepsHeuristic(first, st),
    }
  })

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
      {racks.map(r => {
        const isOpen = open === r.nota
        return (
        <div key={r.nota} className="card overflow-hidden">
          {/* Rak nota: Pelanggan | NOTA | Bahan | qty | Operator */}
          <button onClick={() => setOpenNota(isOpen ? null : r.nota)}
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
                const key = `${r.nota}-${i}`
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
      })}
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
    <div className="flex flex-col gap-5" style={{ maxWidth: 560 }}>
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
    </div>
  )
}

// ─── Sidebar ──────────────────────────────────────────────────────────────────

const NAV: { id: Page; icon: string; label: string }[] = [
  { id: 'ringkasan',  icon: 'dashboard', label: 'HOME'        },
  { id: 'nshlite',   icon: 'layers',    label: 'NSH Lite'    },
  { id: 'kma',       icon: 'router',    label: 'KMA'         },
  { id: 'wma',       icon: 'workflow',  label: 'WMA'         },
  { id: 'kmma',      icon: 'package',   label: 'KMMA'        },
  { id: 'log',       icon: 'terminal',  label: 'Log'         },
  { id: 'pengaturan',icon: 'settings',  label: 'Pengaturan'  },
]

const PAGE_TITLE: Record<Page, string> = {
  ringkasan: 'HOME', nshlite: 'NSH Lite', kma: 'KMA Router',
  wma: 'WMA', kmma: 'KMMA', log: 'Log Monitor', pengaturan: 'Pengaturan',
}

const CONN_CFG: Record<ConnStatus, { status: StatusLevel; label: string; live: boolean }> = {
  connected:    { status: 'success', label: 'Terhubung',    live: true  },
  connecting:   { status: 'warning', label: 'Menghubungkan',live: false },
  disconnected: { status: 'danger',  label: 'Terputus',     live: false },
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
      // localStorage per-origin: simpanan localhost salah saat halaman dibuka via IP LAN
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

  const hub = useHub(hubUrl, logCap)
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

  const conn    = CONN_CFG[connStatus]
  const connected = connStatus === 'connected'
  const sidebarW  = collapsed ? 56 : 240

  const SidebarInner = ({ mobile = false }: { mobile?: boolean }) => (
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

      {/* Nav items */}
      <div className="flex-1 flex flex-col gap-0.5 px-2 py-2.5">
        {NAV.map(item => (
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
            {/* Avatar */}
            <div className="flex items-center justify-center rounded-full cursor-pointer"
              style={{ width: 34, height: 34, background: 'linear-gradient(135deg, var(--color-accent), var(--color-violet))' }}>
              <Icon name="user" size={16} style={{ color: '#fff', strokeWidth: 2 } as any} />
            </div>
          </div>
        </header>

        {/* Page content */}
        <main className="flex-1 overflow-y-auto page-content" style={{ padding: 'var(--spacing-page)' }}>
          <div key={page} className="animate-fade-rise" style={{ minHeight: '100%' }}>
            {page === 'ringkasan'  && <PageRingkasan connected={connected} logs={hub.logs} entriesAll={hub.entriesAll} statuses={hub.statuses} modules={hub.modules} live={connected} sendCommand={hub.sendCommand} results={hub.results} kmaRoutes={hub.kma.routes} />}
            {page === 'nshlite'   && <PageNshLite connected={connected} sendCommand={hub.sendCommand} results={hub.results} outputFiles={hub.outputFiles} />}
            {page === 'kma'       && <PageKma connected={connected} routes={hub.kma.routes} printers={hub.kma.printers} rules={hub.kma.rules} stats={hub.kma.stats} sendCommand={hub.sendCommand} results={hub.results} />}
            {page === 'wma'       && <PageWma entriesAll={hub.entriesAll} statuses={hub.statuses} verified={hub.verified} live={connected} sendCommand={hub.sendCommand} results={hub.results} />}
            {page === 'kmma'      && <PageKmma entries={hub.entries} statuses={hub.statuses} details={hub.details} live={connected} sendCommand={hub.sendCommand} results={hub.results} timer={hub.kmmaTimer} />}
            {page === 'log'       && <PageLog logs={hub.logs} live={connected} />}
            {page === 'pengaturan'&& <PagePengaturan hubUrl={hubUrl} logCap={logCap} onApply={applySettings} />}
          </div>
        </main>
      </div>

      <ToastContainer />
    </div>
  )
}
