import { useState, useEffect, useRef } from 'react'

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
}

const Icon = ({ name, size = 18, className = '' }: { name: string; size?: number; className?: string }) => {
  const d = PATHS[name]
  if (!d) return null
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none"
      stroke="currentColor" strokeWidth={1.75} strokeLinecap="round" strokeLinejoin="round"
      className={`shrink-0 ${className}`}>
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
      background: STATUS_COLOR[status],
      boxShadow: live ? `0 0 0 3px ${STATUS_COLOR[status]}25` : undefined,
    }}
  />
)

const Badge = ({ label, status, size = 'sm' }: { label: string; status: StatusLevel; size?: 'xs' | 'sm' }) => {
  const c = BADGE_CFG[status]
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

type ToastItem = { id: number; message: string; type: StatusLevel }
let _toastId = 0
const _listeners: ((t: ToastItem) => void)[] = []
export function pushToast(message: string, type: StatusLevel = 'accent') {
  const t = { id: ++_toastId, message, type }
  _listeners.forEach(fn => fn(t))
}

const ToastContainer = () => {
  const [toasts, setToasts] = useState<(ToastItem & { out?: boolean })[]>([])
  useEffect(() => {
    const h = (t: ToastItem) => {
      setToasts(p => [...p, t])
      setTimeout(() => {
        setToasts(p => p.map(x => x.id === t.id ? { ...x, out: true } : x))
        setTimeout(() => setToasts(p => p.filter(x => x.id !== t.id)), 300)
      }, 4000)
    }
    _listeners.push(h)
    return () => { const i = _listeners.indexOf(h); if (i >= 0) _listeners.splice(i, 1) }
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

// ─── Drawer ───────────────────────────────────────────────────────────────────

const Drawer = ({ open, onClose, title, children, width = 480 }: {
  open: boolean; onClose: () => void; title: string; children: React.ReactNode; width?: number
}) => {
  if (!open) return null
  return (
    <>
      <div onClick={onClose} className="fixed inset-0 z-[100]"
        style={{ background: '#00000070', backdropFilter: 'blur(2px)' }} />
      <div className="animate-slide-in-right fixed right-0 top-0 bottom-0 flex flex-col overflow-hidden z-[101]"
        style={{
          width: `min(${width}px, 100vw)`,
          background: 'var(--color-panel)',
          borderLeft: '1px solid var(--color-border)',
        }}>
        {/* Header */}
        <div className="flex items-center justify-between shrink-0 px-6 py-[18px]"
          style={{ borderBottom: '1px solid var(--color-border)' }}>
          <span className="font-bold" style={{ fontSize: 'var(--text-lg)' }}>{title}</span>
          <button className="btn btn-ghost btn-sm" onClick={onClose}><Icon name="x" size={16} /></button>
        </div>
        {/* Body */}
        <div className="flex-1 overflow-y-auto p-6">{children}</div>
      </div>
    </>
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

const ACTIVITY_SEED = [
  { ts: '14:32:17', module: 'NSH Lite', msg: 'File N-240901-0023.pdf selesai diimposisi — 4 lembar', level: 'success' as StatusLevel },
  { ts: '14:31:52', module: 'KMA',      msg: 'Route berhasil: bahan ArtPaper300 → folder_1S/HP',     level: 'success' as StatusLevel },
  { ts: '14:30:44', module: 'WMA',      msg: 'Entri N-240901-0021 dikirim ke KMMA untuk produksi',    level: 'accent'  as StatusLevel },
  { ts: '14:28:11', module: 'KMMA',     msg: 'Step Laminating selesai — 500 lbr oleh Budi S.',        level: 'success' as StatusLevel },
  { ts: '14:26:03', module: 'KMA',      msg: 'SKIP: bahan Kertas Hvs tidak ada aturan routing',       level: 'violet'  as StatusLevel },
  { ts: '14:24:58', module: 'NSH Lite', msg: 'File N-240901-0019.pdf gagal — invalid page size',      level: 'danger'  as StatusLevel },
  { ts: '14:22:30', module: 'KMMA',     msg: 'Qty override disimpan: 480 → 500 lbr oleh admin',       level: 'warning' as StatusLevel },
  { ts: '14:20:15', module: 'WMA',      msg: 'Polling selesai — 12 entri baru ditemukan',             level: 'accent'  as StatusLevel },
]

const MODULE_HEALTH = [
  { name: 'NSH Lite',     status: 'success' as StatusLevel, lastRun: '14:32:17', desc: 'Imposisi & antrean berjalan normal' },
  { name: 'KMA Router',   status: 'success' as StatusLevel, lastRun: '14:31:52', desc: 'Routing aktif, semua printer online' },
  { name: 'WMA Polling',  status: 'warning' as StatusLevel, lastRun: '14:20:15', desc: 'Polling terlambat 2 menit dari jadwal' },
  { name: 'KMMA Tracker', status: 'success' as StatusLevel, lastRun: '14:28:11', desc: 'Semua step produksi tercatat' },
  { name: 'HUB WebSocket',status: 'success' as StatusLevel, lastRun: '14:32:18', desc: 'Koneksi stabil, latency 12ms' },
  { name: 'Log Writer',   status: 'success' as StatusLevel, lastRun: '14:32:18', desc: 'Buffer 847/2000 baris' },
]

const PageRingkasan = ({ connected }: { connected: boolean }) => {
  const [kpis, setKpis] = useState({ antrian: 34, selesai: 127, tertunda: 8, gagal: 2 })
  const [feed, setFeed] = useState(ACTIVITY_SEED)
  const feedRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!connected) return
    const t = setInterval(() => {
      const msgs = [
        { module: 'NSH Lite', msg: 'Imposisi selesai — berkas baru masuk antrean', level: 'success' as StatusLevel },
        { module: 'KMA',      msg: 'Router memproses 3 file sekaligus',             level: 'accent'  as StatusLevel },
        { module: 'WMA',      msg: 'Entri baru ditemukan oleh polling script',      level: 'accent'  as StatusLevel },
      ]
      const m = msgs[Math.floor(Math.random() * msgs.length)]
      const n = new Date()
      const ts = `${n.getHours().toString().padStart(2,'0')}:${n.getMinutes().toString().padStart(2,'0')}:${n.getSeconds().toString().padStart(2,'0')}`
      setFeed(p => [{ ts, module: m.module, msg: m.msg, level: m.level }, ...p.slice(0, 19)])
      setKpis(p => ({ ...p, antrian: p.antrian + (Math.random() > 0.5 ? 1 : 0), selesai: p.selesai + 1 }))
    }, 6000)
    return () => clearInterval(t)
  }, [connected])

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

      {/* KPI grid: 4 → 2 → 1 cols */}
      <div className="grid gap-4 kpi-grid" style={{ gridTemplateColumns: 'repeat(4, 1fr)' }}>
        <KpiCard label="Antrean Aktif"   value={kpis.antrian}  icon="layers"        status="accent"  delta="+3 dari kemarin" />
        <KpiCard label="Selesai Hari Ini" value={kpis.selesai} icon="check"         status="success" delta="↑ 14% vs kemarin" />
        <KpiCard label="Menunggu Aksi"   value={kpis.tertunda}  icon="clock"         status="warning" delta="2 mendesak" />
        <KpiCard label="Gagal / Error"   value={kpis.gagal}     icon="alertTriangle" status="danger"  delta="Butuh perhatian" />
      </div>

      {/* Activity feed + module health */}
      <div className="grid gap-4 two-col" style={{ gridTemplateColumns: '1fr 340px' }}>
        {/* Activity feed */}
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
          <div ref={feedRef} className="overflow-y-auto" style={{ maxHeight: 360 }}>
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

        {/* Module health */}
        <div className="card p-5 flex flex-col gap-3">
          <SectionHeader label="Kesehatan Modul" />
          {MODULE_HEALTH.map((m, i) => (
            <div key={i} className="flex items-start gap-2.5 rounded-lg px-2.5 py-2"
              style={{ background: 'var(--color-bg)' }}>
              <StatusDot status={m.status} live={m.status === 'success'} />
              <div className="flex-1 min-w-0">
                <div className="font-semibold" style={{ fontSize: 'var(--text-sm)', color: 'var(--color-text)' }}>{m.name}</div>
                <div style={{ fontSize: 'var(--text-label)', color: 'var(--color-text-muted)' }}>{m.desc}</div>
              </div>
              <span className="font-mono shrink-0" style={{ fontSize: 10, color: 'var(--color-text-dim)' }}>{m.lastRun}</span>
            </div>
          ))}
        </div>
      </div>
    </div>
  )
}

// ─── Page: NSH Lite ───────────────────────────────────────────────────────────

const NSH_DATA = [
  { id: 'N-240901-0034', nota: 'NT-2024-0034', bahan: 'ArtPaper150', ukuran: 'A3+', qty: 500,  mesin: 'HP .157', status: 'pending' as StatusLevel, statusLabel: 'PENDING' },
  { id: 'N-240901-0033', nota: 'NT-2024-0033', bahan: 'ArtPaper300', ukuran: 'A4',  qty: 200,  mesin: 'HP .158', status: 'success' as StatusLevel, statusLabel: 'SELESAI' },
  { id: 'N-240901-0032', nota: 'NT-2024-0032', bahan: 'Ivory230',    ukuran: 'A3',  qty: 1000, mesin: 'HP .157', status: 'accent'  as StatusLevel, statusLabel: 'PROSES'  },
  { id: 'N-240901-0031', nota: 'NT-2024-0031', bahan: 'Hvs80',       ukuran: 'A4',  qty: 300,  mesin: '—',       status: 'danger'  as StatusLevel, statusLabel: 'GAGAL'   },
  { id: 'N-240901-0030', nota: 'NT-2024-0030', bahan: 'ArtPaper150', ukuran: 'A3+', qty: 750,  mesin: 'HP .157', status: 'success' as StatusLevel, statusLabel: 'SELESAI' },
  { id: 'N-240901-0029', nota: 'NT-2024-0029', bahan: 'Hvs80',       ukuran: 'A4',  qty: 250,  mesin: 'HP .158', status: 'violet'  as StatusLevel, statusLabel: 'SKIP'    },
  { id: 'N-240901-0028', nota: 'NT-2024-0028', bahan: 'ArtPaper300', ukuran: 'A3',  qty: 400,  mesin: 'HP .157', status: 'success' as StatusLevel, statusLabel: 'SELESAI' },
  { id: 'N-240901-0027', nota: 'NT-2024-0027', bahan: 'Ivory230',    ukuran: 'A3+', qty: 600,  mesin: 'HP .157', status: 'pending' as StatusLevel, statusLabel: 'PENDING' },
]

const ImposisiGrid = () => (
  <div className="grid gap-1 rounded-lg p-3" style={{ gridTemplateColumns: 'repeat(4, 1fr)', background: 'var(--color-bg)' }}>
    {Array.from({ length: 16 }).map((_, i) => (
      <div key={i} className="flex items-center justify-center rounded-sm"
        style={{
          aspectRatio: '1/1.414',
          background: i % 5 === 0 ? 'var(--color-accent-ghost)' : 'var(--color-panel)',
          border: `1px solid ${i % 5 === 0 ? 'var(--color-accent-d)' : 'var(--color-border)'}`,
        }}>
        <span className="font-mono" style={{ fontSize: 8, color: 'var(--color-text-dim)' }}>{i + 1}</span>
      </div>
    ))}
  </div>
)

const PageNshLite = () => {
  const [filter, setFilter]   = useState('semua')
  const [search, setSearch]   = useState('')
  const [sortCol, setSortCol] = useState('id')
  const [sortDir, setSortDir] = useState<'asc'|'desc'>('desc')
  const [page, setPage]       = useState(1)
  const [drawer, setDrawer]   = useState<typeof NSH_DATA[0] | null>(null)
  const PER = 5

  const FILTERS = [
    { key: 'semua',   label: 'Semua',   count: NSH_DATA.length },
    { key: 'pending', label: 'Pending', count: NSH_DATA.filter(r => r.status === 'pending').length },
    { key: 'success', label: 'Selesai', count: NSH_DATA.filter(r => r.status === 'success').length },
    { key: 'accent',  label: 'Proses',  count: NSH_DATA.filter(r => r.status === 'accent').length },
    { key: 'danger',  label: 'Gagal',   count: NSH_DATA.filter(r => r.status === 'danger').length },
    { key: 'violet',  label: 'Skip',    count: NSH_DATA.filter(r => r.status === 'violet').length },
  ]

  const filtered = NSH_DATA
    .filter(r => filter === 'semua' || r.status === filter)
    .filter(r => !search || r.nota.toLowerCase().includes(search.toLowerCase()) || r.bahan.toLowerCase().includes(search.toLowerCase()))
    .sort((a, b) => {
      const dir = sortDir === 'asc' ? 1 : -1
      if (sortCol === 'qty') return (a.qty - b.qty) * dir
      const av = String(a[sortCol as keyof typeof a] ?? '')
      const bv = String(b[sortCol as keyof typeof b] ?? '')
      return av.localeCompare(bv) * dir
    })

  const rows = filtered.slice((page - 1) * PER, page * PER)
  const totalPages = Math.ceil(filtered.length / PER)

  const handleSort = (col: string) => {
    if (sortCol === col) setSortDir(d => d === 'asc' ? 'desc' : 'asc')
    else { setSortCol(col); setSortDir('asc') }
  }

  const SortArrow = ({ col }: { col: string }) => (
    <span style={{ marginLeft: 4, opacity: sortCol === col ? 1 : 0.3 }}>
      {sortCol === col && sortDir === 'desc' ? '↓' : '↑'}
    </span>
  )

  return (
    <div className="flex flex-col gap-4">
      {/* Filters + search */}
      <div className="flex items-center gap-2 flex-wrap">
        {FILTERS.map(f => (
          <button key={f.key} className="chip"
            onClick={() => { setFilter(f.key); setPage(1) }}
            style={{
              background: filter === f.key ? 'var(--color-nav-active)' : 'var(--color-panel)',
              color:      filter === f.key ? 'var(--color-accent)'     : 'var(--color-text-secondary)',
              border: `1px solid ${filter === f.key ? 'var(--color-accent-ghost)' : 'var(--color-border)'}`,
            }}>
            {f.label}
            <span className="rounded-full px-1.5 font-bold"
              style={{ background: 'var(--color-border)', fontSize: 10 }}>{f.count}</span>
          </button>
        ))}
        <div className="relative ml-auto">
          <Icon name="search" size={14} className="absolute left-2.5 top-1/2 -translate-y-1/2"
            style={{ color: 'var(--color-text-muted)' } as any} />
          <input className="input pl-8" placeholder="Cari nota / bahan…" value={search}
            onChange={e => { setSearch(e.target.value); setPage(1) }} style={{ width: 220 }} />
        </div>
      </div>

      {/* Table card */}
      <div className="card overflow-hidden">
        <div className="overflow-x-auto">
          <table className="data-table">
            <thead>
              <tr>
                {[['id','File / Nota'],['bahan','Bahan'],['ukuran','Ukuran'],['qty','Qty'],['mesin','Mesin'],['status','Status']].map(([col, lbl]) => (
                  <th key={col} onClick={() => handleSort(col)}>{lbl}<SortArrow col={col} /></th>
                ))}
                <th>Aksi</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((row, i) => (
                <tr key={row.id} className="animate-fade-rise" style={{ animationDelay: `${i * 40}ms` }}>
                  <td>
                    <div className="font-mono" style={{ fontSize: 'var(--text-xs)', color: 'var(--color-accent)' }}>{row.id}</div>
                    <div style={{ fontSize: 'var(--text-label)', color: 'var(--color-text-muted)' }}>{row.nota}</div>
                  </td>
                  <td><CategoryBadge label={row.bahan} /></td>
                  <td><span className="font-mono" style={{ fontSize: 'var(--text-xs)' }}>{row.ukuran}</span></td>
                  <td><span className="font-mono font-semibold" style={{ fontSize: 'var(--text-sm)' }}>{row.qty.toLocaleString()}</span></td>
                  <td style={{ fontSize: 'var(--text-xs)', color: 'var(--color-text-secondary)' }}>{row.mesin}</td>
                  <td><Badge label={row.statusLabel} status={row.status} /></td>
                  <td>
                    <div className="flex gap-1.5">
                      <button className="btn btn-ghost btn-sm" onClick={() => setDrawer(row)}><Icon name="eye" size={14} /></button>
                      {row.status === 'pending' && <>
                        <button className="btn btn-primary btn-sm" onClick={() => pushToast(`Memproses ${row.nota}…`, 'accent')}>Proses</button>
                        <button className="btn btn-danger btn-sm"  onClick={() => pushToast(`Dibatalkan: ${row.nota}`, 'danger')}>Batal</button>
                      </>}
                    </div>
                  </td>
                </tr>
              ))}
              {rows.length === 0 && (
                <tr><td colSpan={7}>
                  <EmptyState icon="layers" title="Tidak ada data" description="Tidak ada entri yang cocok dengan filter saat ini" />
                </td></tr>
              )}
            </tbody>
          </table>
        </div>

        {/* Pagination */}
        {totalPages > 1 && (
          <div className="flex items-center justify-between px-4 py-3"
            style={{ borderTop: '1px solid var(--color-border)' }}>
            <span style={{ fontSize: 'var(--text-xs)', color: 'var(--color-text-muted)' }}>
              {(page - 1) * PER + 1}–{Math.min(page * PER, filtered.length)} dari {filtered.length}
            </span>
            <div className="flex gap-1">
              {Array.from({ length: totalPages }).map((_, i) => (
                <button key={i} onClick={() => setPage(i + 1)}
                  className="btn btn-sm rounded-md"
                  style={{
                    minWidth: 32,
                    background: page === i + 1 ? 'var(--color-nav-active)' : 'var(--color-panel)',
                    color:      page === i + 1 ? 'var(--color-accent)'     : 'var(--color-text-secondary)',
                    border: `1px solid ${page === i + 1 ? 'var(--color-accent-ghost)' : 'var(--color-border)'}`,
                  }}>{i + 1}</button>
              ))}
            </div>
          </div>
        )}
      </div>

      {/* Detail drawer */}
      <Drawer open={!!drawer} onClose={() => setDrawer(null)} title={`Detail: ${drawer?.nota}`}>
        {drawer && (
          <div className="flex flex-col gap-5">
            <div className="grid grid-cols-2 gap-3">
              {[['Nota ID', drawer.nota],['Bahan', drawer.bahan],['Ukuran', drawer.ukuran],['Qty', String(drawer.qty)],['Mesin', drawer.mesin]].map(([k, v]) => (
                <div key={k} className="rounded-lg px-3.5 py-2.5" style={{ background: 'var(--color-bg)' }}>
                  <div className="section-label mb-1">{k}</div>
                  <div className="font-mono font-semibold" style={{ fontSize: 'var(--text-base)' }}>{v}</div>
                </div>
              ))}
              <div className="rounded-lg px-3.5 py-2.5" style={{ background: 'var(--color-bg)' }}>
                <div className="section-label mb-1">Status</div>
                <Badge label={drawer.statusLabel} status={drawer.status} />
              </div>
            </div>

            <div>
              <SectionHeader label="Preview PDF" />
              <div className="flex flex-col items-center justify-center gap-2 rounded-lg"
                style={{ height: 180, border: '1px dashed var(--color-border)', background: 'var(--color-bg)', color: 'var(--color-text-dim)', fontSize: 'var(--text-xs)' }}>
                <Icon name="download" size={32} />
                <span>Preview PDF tidak tersedia</span>
              </div>
            </div>

            <div>
              <SectionHeader label="Grid Imposisi" />
              <ImposisiGrid />
            </div>

            {drawer.status === 'pending' && (
              <div className="flex gap-2">
                <button className="btn btn-primary btn-md flex-1"
                  onClick={() => { pushToast(`Memproses ${drawer.nota}`, 'accent'); setDrawer(null) }}>
                  <Icon name="activity" size={14} /> Proses
                </button>
                <button className="btn btn-danger btn-md"
                  onClick={() => { pushToast(`Dibatalkan: ${drawer.nota}`, 'danger'); setDrawer(null) }}>
                  <Icon name="x" size={14} /> Batalkan
                </button>
              </div>
            )}
          </div>
        )}
      </Drawer>
    </div>
  )
}

// ─── Page: KMA ────────────────────────────────────────────────────────────────

const KMA_FILES = [
  { name: 'N-240901-0034_ArtPaper150.pdf', bahan: 'ArtPaper150', hotfolder: 'folder_1S/HP', time: '14:32:10', status: 'success' as StatusLevel, label: 'SELESAI', msg: 'Dikirim ke HP .157 via folder_1S' },
  { name: 'N-240901-0033_ArtPaper300.pdf', bahan: 'ArtPaper300', hotfolder: 'folder_2S/HP', time: '14:31:50', status: 'success' as StatusLevel, label: 'SELESAI', msg: 'Dikirim ke HP .158 via folder_2S' },
  { name: 'N-240901-0032_Hvs80.pdf',       bahan: 'Hvs80',       hotfolder: '—',            time: '14:30:40', status: 'violet'  as StatusLevel, label: 'SKIP',    msg: 'Tidak ada aturan untuk bahan Hvs80' },
  { name: 'N-240901-0031_Ivory230.pdf',    bahan: 'Ivory230',    hotfolder: 'folder_1S/HP', time: '14:28:00', status: 'success' as StatusLevel, label: 'SELESAI', msg: 'Dikirim ke HP .157 via folder_1S' },
  { name: 'N-240901-0030_Duplex280.pdf',   bahan: 'Duplex280',   hotfolder: '—',            time: '14:25:11', status: 'danger'  as StatusLevel, label: 'GAGAL',   msg: 'Timeout saat menyalin ke hotfolder' },
]
const ROUTING_RULES = [
  { bahan: 'ArtPaper150', folder: 'folder_1S/HP' },
  { bahan: 'ArtPaper300', folder: 'folder_2S/HP' },
  { bahan: 'Ivory230',    folder: 'folder_1S/HP' },
  { bahan: 'Duplex280',   folder: 'folder_2S/HP' },
]
const PRINTERS = [
  { ip: '.157', name: 'UTAMA',    status: 'success' as StatusLevel, label: 'SIAP', active: true,  ok: 1247, ret: 23, done: 1189 },
  { ip: '.158', name: 'CADANGAN', status: 'warning' as StatusLevel, label: 'SIAP', active: false, ok: 832,  ret: 41, done: 791  },
]

const PageKma = ({ connected }: { connected: boolean }) => {
  if (!connected) return (
    <EmptyState icon="wifiOff" title="Hub Offline" description="Jalankan route.py --hub untuk mengaktifkan modul KMA Router" />
  )

  return (
    <div className="flex flex-col gap-4">
      {/* File list */}
      <div className="card overflow-hidden">
        <div className="px-5 py-3.5" style={{ borderBottom: '1px solid var(--color-border)' }}>
          <SectionHeader label="Daftar File Router" />
        </div>
        <div className="overflow-x-auto">
          <table className="data-table">
            <thead>
              <tr>
                <th>Nama File</th><th>Bahan</th><th>Hotfolder</th><th>Waktu</th><th>Status</th><th>Pesan</th>
              </tr>
            </thead>
            <tbody>
              {KMA_FILES.map((f, i) => (
                <tr key={i}>
                  <td><span className="font-mono" style={{ fontSize: 'var(--text-xs)', color: 'var(--color-accent-fg)' }}>{f.name}</span></td>
                  <td><CategoryBadge label={f.bahan} /></td>
                  <td><span className="font-mono" style={{ fontSize: 'var(--text-xs)', color: 'var(--color-text-secondary)' }}>{f.hotfolder}</span></td>
                  <td><span className="font-mono" style={{ fontSize: 'var(--text-xs)', color: 'var(--color-text-muted)' }}>{f.time}</span></td>
                  <td><Badge label={f.label} status={f.status} /></td>
                  <td style={{ fontSize: 'var(--text-xs)', color: 'var(--color-text-muted)', maxWidth: 260 }}>{f.msg}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      {/* Rules + printers */}
      <div className="grid grid-cols-2 gap-4 two-col">
        {/* Rules */}
        <div className="card p-5">
          <SectionHeader label="Aturan Routing" />
          <div className="flex flex-col gap-1.5">
            {ROUTING_RULES.map((r, i) => (
              <div key={i} className="flex items-center gap-2.5 rounded-lg px-3 py-2"
                style={{ background: 'var(--color-bg)' }}>
                <CategoryBadge label={r.bahan} />
                <Icon name="chevronRight" size={14} style={{ color: 'var(--color-text-dim)' } as any} />
                <span className="font-mono" style={{ fontSize: 'var(--text-xs)', color: 'var(--color-accent)' }}>{r.folder}</span>
              </div>
            ))}
          </div>
        </div>

        {/* Printers */}
        <div className="card p-5">
          <SectionHeader label="Status Printer" />
          <div className="flex flex-col gap-2.5">
            {PRINTERS.map((p, i) => (
              <div key={i} className={`card card-accent-${p.status} px-4 py-3.5`}>
                <div className="flex items-center justify-between mb-2">
                  <div className="flex items-center gap-2">
                    <Icon name="printer" size={16} style={{ color: 'var(--color-text-secondary)' } as any} />
                    <span className="font-bold" style={{ fontSize: 'var(--text-base)' }}>HP {p.ip} {p.name}</span>
                    {p.active && (
                      <span className="rounded font-bold"
                        style={{ fontSize: 10, background: 'var(--color-success-ghost)', color: 'var(--color-success-fg)', padding: '2px 6px' }}>
                        AKTIF
                      </span>
                    )}
                  </div>
                  <Badge label={p.label} status={p.status} />
                </div>
                <div className="flex gap-4">
                  {[['OK', p.ok, 'var(--color-success)'], ['RET', p.ret, 'var(--color-warning)'], ['DONE', p.done, 'var(--color-accent)']].map(([k, v, c]) => (
                    <div key={k as string}>
                      <div className="section-label mb-0.5">{k}</div>
                      <span className="font-mono font-bold" style={{ fontSize: 'var(--text-num-sm)', color: c as string }}>
                        {Number(v).toLocaleString()}
                      </span>
                    </div>
                  ))}
                </div>
              </div>
            ))}
          </div>
        </div>
      </div>
    </div>
  )
}

// ─── Page: WMA ────────────────────────────────────────────────────────────────

const WMA_ENTRIES = [
  { id: 'W-001', nota: 'NT-2024-0034', bahan: 'ArtPaper150', kategori: 'Offset',    status: 'pending' as StatusLevel, statusLabel: 'PENDING' },
  { id: 'W-002', nota: 'NT-2024-0033', bahan: 'ArtPaper300', kategori: 'Digital',   status: 'success' as StatusLevel, statusLabel: 'SELESAI' },
  { id: 'W-003', nota: 'NT-2024-0032', bahan: 'Ivory230',    kategori: 'Finishing', status: 'pending' as StatusLevel, statusLabel: 'PENDING' },
  { id: 'W-004', nota: 'NT-2024-0031', bahan: 'Hvs80',       kategori: 'Digital',   status: 'warning' as StatusLevel, statusLabel: 'REVIEW'  },
  { id: 'W-005', nota: 'NT-2024-0030', bahan: 'Duplex280',   kategori: 'Finishing', status: 'pending' as StatusLevel, statusLabel: 'PENDING' },
  { id: 'W-006', nota: 'NT-2024-0029', bahan: 'ArtPaper150', kategori: 'Offset',    status: 'success' as StatusLevel, statusLabel: 'SELESAI' },
]

const PageWma = () => {
  const [selected, setSelected] = useState<Set<string>>(new Set())
  const toggle = (id: string) => setSelected(p => { const n = new Set(p); n.has(id) ? n.delete(id) : n.add(id); return n })

  return (
    <div className="flex flex-col gap-4">
      <div className="grid gap-3 three-col" style={{ gridTemplateColumns: 'repeat(3, 1fr)' }}>
        {WMA_ENTRIES.map((e, i) => {
          const sel = selected.has(e.id)
          return (
            <div key={e.id} onClick={() => toggle(e.id)}
              className="card px-[18px] py-4 cursor-pointer transition-all duration-150"
              style={{
                border: `1px solid ${sel ? 'var(--color-accent)' : 'var(--color-border)'}`,
                background: sel ? 'var(--color-nav-active)' : 'var(--color-panel)',
                animationDelay: `${i * 50}ms`,
              }}>
              <div className="flex items-start justify-between mb-2.5">
                <div className="flex flex-col gap-1.5">
                  <div className="font-mono" style={{ fontSize: 'var(--text-xs)', color: 'var(--color-accent)' }}>{e.nota}</div>
                  <CategoryBadge label={e.bahan} />
                </div>
                <div className="flex flex-col items-end gap-1.5">
                  <Badge label={e.statusLabel} status={e.status} />
                  <span className="rounded font-semibold"
                    style={{ fontSize: 10, background: 'var(--color-panel)', color: 'var(--color-text-secondary)', padding: '2px 6px', border: '1px solid var(--color-border)' }}>
                    {e.kategori}
                  </span>
                </div>
              </div>
              {sel && (
                <div className="flex items-center gap-1.5 font-semibold"
                  style={{ fontSize: 'var(--text-xs)', color: 'var(--color-accent)' }}>
                  <Icon name="check" size={14} /> Dipilih
                </div>
              )}
            </div>
          )
        })}
      </div>

      {/* Bulk bar */}
      {selected.size > 0 && (
        <div className="animate-slide-in-top fixed bottom-6 left-1/2 -translate-x-1/2 flex items-center gap-4 rounded-xl px-5 py-3 z-50"
          style={{
            background: 'var(--color-panel)',
            border: '1px solid var(--color-accent-ghost)',
            boxShadow: 'var(--shadow-panel)',
          }}>
          <span style={{ fontSize: 'var(--text-sm)', color: 'var(--color-text-secondary)' }}>
            <strong style={{ color: 'var(--color-text)' }}>{selected.size}</strong> entri dipilih
          </span>
          <button className="btn btn-primary btn-md"
            onClick={() => { pushToast(`${selected.size} entri dikirim ke KMMA`, 'success'); setSelected(new Set()) }}>
            <Icon name="send" size={14} /> Kirim ke KMMA
          </button>
          <button className="btn btn-ghost btn-sm" onClick={() => setSelected(new Set())}><Icon name="x" size={14} /></button>
        </div>
      )}
    </div>
  )
}

// ─── Page: KMMA ───────────────────────────────────────────────────────────────

const NOTAS = ['NT-2024-0034', 'NT-2024-0033', 'NT-2024-0032', 'NT-2024-0031']

type Step = { name: string; machine: string; material: string; sdm: string; qty: number; status: 'done'|'active'|'pending'|'error'; warn: string | null }
const NOTA_DATA: Record<string, { bahan: string; qty: number; operator: string; status: StatusLevel; statusLabel: string; steps: Step[] }> = {
  'NT-2024-0034': { bahan: 'ArtPaper150', qty: 500, operator: 'Budi Santoso',   status: 'accent', statusLabel: 'PROSES',
    steps: [
      { name: 'Digital A3+', machine: 'HP .157',     material: 'ArtPaper150', sdm: 'Andi R.', qty: 500, status: 'done',   warn: null },
      { name: 'Laminating',  machine: 'Laminator #2',material: 'Film Matt',   sdm: 'Siti M.', qty: 500, status: 'active', warn: 'Kecepatan dikurangi 10% karena suhu tinggi' },
      { name: 'Kisscut',     machine: 'Kisscut #1',  material: '—',           sdm: 'Dedi K.', qty: 500, status: 'pending',warn: null },
    ] },
  'NT-2024-0033': { bahan: 'ArtPaper300', qty: 200, operator: 'Rina Wulandari', status: 'success', statusLabel: 'SELESAI',
    steps: [
      { name: 'Digital A3+', machine: 'HP .158',    material: 'ArtPaper300',  sdm: 'Andi R.', qty: 200, status: 'done', warn: null },
      { name: 'Laminating',  machine: 'Laminator #1',material: 'Film Glossy', sdm: 'Siti M.', qty: 200, status: 'done', warn: null },
      { name: 'Diecut',      machine: 'Diecut #2',  material: '—',            sdm: 'Dedi K.', qty: 200, status: 'done', warn: null },
    ] },
  'NT-2024-0032': { bahan: 'Ivory230', qty: 1000, operator: 'Agus Prasetyo', status: 'danger', statusLabel: 'ERROR',
    steps: [
      { name: 'Digital A3+', machine: 'HP .157',     material: 'Ivory230',  sdm: 'Andi R.', qty: 1000, status: 'done',  warn: null },
      { name: 'Laminating',  machine: 'Laminator #2',material: 'Film Matt', sdm: 'Siti M.', qty: 1000, status: 'error', warn: 'Film habis di tengah proses — perlu penggantian manual' },
      { name: 'Kisscut',     machine: 'Kisscut #1',  material: '—',         sdm: '—',        qty: 1000, status: 'pending',warn: null },
    ] },
  'NT-2024-0031': { bahan: 'Hvs80', qty: 300, operator: 'Dewi Lestari', status: 'pending', statusLabel: 'PENDING',
    steps: [
      { name: 'Digital A4', machine: 'HP .158', material: 'Hvs80', sdm: '—', qty: 300, status: 'pending', warn: null },
      { name: 'Finishing',  machine: '—',       material: '—',     sdm: '—', qty: 300, status: 'pending', warn: null },
    ] },
}

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

const PageKmma = () => {
  const [nota, setNota]           = useState(NOTAS[0])
  const [overrides, setOverrides] = useState<Record<string, number>>({})
  const data = NOTA_DATA[nota]

  return (
    <div className="flex flex-col gap-4">
      {/* Nota picker */}
      <div className="flex gap-2 flex-wrap">
        {NOTAS.map(n => (
          <button key={n} className="chip font-mono"
            onClick={() => setNota(n)}
            style={{
              background: nota === n ? 'var(--color-nav-active)' : 'var(--color-panel)',
              color:      nota === n ? 'var(--color-accent)'     : 'var(--color-text-secondary)',
              border: `1px solid ${nota === n ? 'var(--color-accent-ghost)' : 'var(--color-border)'}`,
            }}>{n}</button>
        ))}
      </div>

      {/* Header strip */}
      <div className="card px-5 py-4 flex items-center gap-6 flex-wrap">
        {[['Nota', nota], ['Bahan', data.bahan], ['Qty', data.qty.toLocaleString()], ['Operator', data.operator]].map(([k, v]) => (
          <div key={k as string}>
            <div className="section-label mb-0.5">{k}</div>
            <div className="font-mono font-semibold" style={{ fontSize: 'var(--text-base)', color: 'var(--color-text)' }}>{String(v)}</div>
          </div>
        ))}
        <div className="ml-auto"><Badge label={data.statusLabel} status={data.status} /></div>
      </div>

      {/* Timeline */}
      <div className="card px-6 py-5">
        <SectionHeader label="Alur Produksi" />
        <div className="flex flex-col">
          {data.steps.map((step, i) => {
            const isLast = i === data.steps.length - 1
            const pct = step.status === 'done' ? '100%' : step.status === 'active' ? '50%' : '0%'
            const key = `${nota}-${i}`
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
                            onClick={() => pushToast(`Qty disimpan: ${qty}`, 'success')}>
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
                        onClick={() => pushToast('Langkah disimpan ulang', 'success')}>
                        <Icon name="refresh" size={13} /> Simpan Ulang
                      </button>
                      <button className="btn btn-secondary btn-sm"
                        onClick={() => pushToast('Langkah dilewati permanen', 'violet')}>
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
    </div>
  )
}

// ─── Page: Log ────────────────────────────────────────────────────────────────

const LOG_LINES = [
  { ts: '14:32:18.241', level: 'INFO',  src: 'HUB',  msg: 'WebSocket heartbeat OK — latency 12ms' },
  { ts: '14:32:17.012', level: 'INFO',  src: 'NSH',  msg: 'Imposisi selesai: N-240901-0034.pdf → 4 halaman' },
  { ts: '14:32:10.883', level: 'WARN',  src: 'KMA',  msg: 'File N-240901-0032_Hvs80.pdf tidak memiliki aturan routing — SKIP' },
  { ts: '14:31:52.441', level: 'INFO',  src: 'KMA',  msg: 'Route OK: ArtPaper300 → folder_2S/HP — berhasil disalin' },
  { ts: '14:30:44.009', level: 'INFO',  src: 'WMA',  msg: 'Entri NT-2024-0032 dikirim ke KMMA oleh operator admin' },
  { ts: '14:28:11.773', level: 'INFO',  src: 'KMMA', msg: 'Step Laminating selesai — 500 lbr — operator: Budi S.' },
  { ts: '14:26:03.555', level: 'ERROR', src: 'NSH',  msg: 'Gagal memproses N-240901-0031.pdf — invalid page size' },
  { ts: '14:25:11.002', level: 'ERROR', src: 'KMA',  msg: 'Timeout menyalin Duplex280.pdf ke hotfolder — retry 3/3 gagal' },
  { ts: '14:24:58.121', level: 'WARN',  src: 'KMMA', msg: 'Qty override: 480 → 500 lbr disimpan oleh user:admin' },
  { ts: '14:22:30.889', level: 'INFO',  src: 'KMA',  msg: 'Route OK: ArtPaper150 → folder_1S/HP' },
  { ts: '14:21:10.006', level: 'INFO',  src: 'HUB',  msg: 'Client baru terhubung: 192.168.1.45 (browser)' },
  { ts: '14:20:15.334', level: 'INFO',  src: 'WMA',  msg: 'Polling selesai — 12 entri baru, 3 diperbarui' },
]

const PageLog = () => {
  const [tab, setTab]           = useState<'WMA'|'KMMA'|'HUB'>('HUB')
  const [filter, setFilter]     = useState('')
  const [autoScroll, setAutoScroll] = useState(true)
  const logRef = useRef<HTMLDivElement>(null)

  const lines = LOG_LINES.filter(l =>
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
        {(['WMA','KMMA','HUB'] as const).map(t => (
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

const PagePengaturan = ({ wsUrl, setWsUrl }: { wsUrl: string; setWsUrl: (u: string) => void }) => {
  const [local, setLocal]     = useState(wsUrl)
  const [bufSize, setBufSize] = useState(2000)
  const [saved, setSaved]     = useState(false)

  const handleSave = () => {
    setWsUrl(local)
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
        <button className="btn btn-ghost btn-md" onClick={() => { setLocal(wsUrl); setBufSize(2000) }}>
          Reset
        </button>
      </div>
    </div>
  )
}

// ─── Sidebar ──────────────────────────────────────────────────────────────────

const NAV: { id: Page; icon: string; label: string }[] = [
  { id: 'ringkasan',  icon: 'dashboard', label: 'Ringkasan'   },
  { id: 'nshlite',   icon: 'layers',    label: 'NSH Lite'    },
  { id: 'kma',       icon: 'router',    label: 'KMA'         },
  { id: 'wma',       icon: 'workflow',  label: 'WMA'         },
  { id: 'kmma',      icon: 'package',   label: 'KMMA'        },
  { id: 'log',       icon: 'terminal',  label: 'Log'         },
  { id: 'pengaturan',icon: 'settings',  label: 'Pengaturan'  },
]

const PAGE_TITLE: Record<Page, string> = {
  ringkasan: 'Ringkasan', nshlite: 'NSH Lite', kma: 'KMA Router',
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
  const [connStatus,setConnStatus]= useState<ConnStatus>('connected')
  const [wsUrl,     setWsUrl]     = useState('ws://localhost:8765')
  const [search,    setSearch]    = useState('')
  const [mobileNav, setMobileNav] = useState(false)

  /* Simulate occasional connection blips */
  useEffect(() => {
    const t = setInterval(() => {
      setConnStatus(prev => {
        if (prev === 'connected'    && Math.random() < 0.05) return 'connecting'
        if (prev === 'connecting')  return Math.random() > 0.3 ? 'connected' : 'disconnected'
        if (prev === 'disconnected')return Math.random() > 0.5 ? 'connecting' : 'disconnected'
        return 'connected'
      })
    }, 5000)
    return () => clearInterval(t)
  }, [])

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
        style={{ borderBottom: '1px solid var(--color-border-subtle)' }}>
        <div className="flex items-center justify-center rounded-[8px] shrink-0"
          style={{ width: 28, height: 28, background: 'linear-gradient(135deg, var(--color-accent), var(--color-accent-d))' }}>
          <Icon name="printer" size={15} style={{ color: 'var(--color-bg)', strokeWidth: 2.5 } as any} />
        </div>
        {(!collapsed || mobile) && (
          <div className="overflow-hidden">
            <div className="font-extrabold leading-tight tracking-tight whitespace-nowrap"
              style={{ fontSize: 'var(--text-md)', color: 'var(--color-text)', letterSpacing: '-0.02em' }}>
              NSH HUB
            </div>
            <div className="section-label whitespace-nowrap">Print Monitor</div>
          </div>
        )}
        {!mobile && (
          <button className="btn btn-ghost btn-sm ml-auto p-1 shrink-0"
            onClick={() => setCollapsed(c => !c)}>
            <Icon name={collapsed ? 'chevronRight' : 'chevronLeft'} size={14} />
          </button>
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
              {wsUrl}
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

          {/* Search */}
          <div className="relative flex-1 max-w-sm ml-2">
            <Icon name="search" size={14} className="absolute left-2.5 top-1/2 -translate-y-1/2"
              style={{ color: 'var(--color-text-muted)' } as any} />
            <input className="input pl-8"
              placeholder="Cari nota, file, bahan…"
              value={search} onChange={e => setSearch(e.target.value)}
              style={{ height: 36, fontSize: 'var(--text-sm)' }} />
          </div>

          <div className="flex items-center gap-4 ml-auto">
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
            {page === 'ringkasan'  && <PageRingkasan connected={connected} />}
            {page === 'nshlite'   && <PageNshLite />}
            {page === 'kma'       && <PageKma connected={connected} />}
            {page === 'wma'       && <PageWma />}
            {page === 'kmma'      && <PageKmma />}
            {page === 'log'       && <PageLog />}
            {page === 'pengaturan'&& <PagePengaturan wsUrl={wsUrl} setWsUrl={setWsUrl} />}
          </div>
        </main>
      </div>

      <ToastContainer />
    </div>
  )
}
