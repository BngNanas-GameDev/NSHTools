import { useState } from 'react'

function hubHttpBase(): string {
  try {
    const h = location.hostname || 'localhost'
    return `http://${h}:8766`
  } catch { return 'http://localhost:8766' }
}

export default function LoginPage({ onSuccess }: { onSuccess?: () => void }) {
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [remember, setRemember] = useState(false)
  const [loading, setLoading] = useState(false)
  const [err, setErr] = useState('')

  const submit = async (e: React.FormEvent) => {
    e.preventDefault()
    setErr('')
    if (!username.trim() || !password) { setErr('Username & password wajib'); return }
    if (password.length < 6) { setErr('Password min 6 karakter'); return }
    setLoading(true)
    try {
      const base = hubHttpBase()
      const r = await fetch(`${base}/api/login`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        credentials: 'include',
        body: JSON.stringify({ username: username.trim(), password, remember })
      })
      const j = await r.json()
      if (!j.ok) throw new Error(j.msg || 'login gagal')
      try {
        localStorage.setItem('nsh_token', j.token)
        localStorage.setItem('nsh_role', j.role || 'user')
        localStorage.setItem('nsh_user', j.username || username.trim().toLowerCase())
        // cookie already set via Set-Cookie; also set JS fallback
        document.cookie = `nsh_token=${j.token}; Path=/; Max-Age=${remember ? 7*86400 : 2*86400}; SameSite=Lax`
      } catch {}
      if (j.must_change) {
        // force change banner: keep token but warn
        alert('Login berhasil — segera ganti password admin/admin123!')
      }
      if (onSuccess) onSuccess()
      else location.href = '/'
    } catch (ex: unknown) {
      setErr(ex instanceof Error ? ex.message : String(ex))
    } finally { setLoading(false) }
  }

  return (
    <div className="flex items-center justify-center" style={{ minHeight: '100vh', background: 'var(--color-bg)', padding: 16 }}>
      <form onSubmit={submit} className="card p-8 flex flex-col gap-4" style={{ width: 380, maxWidth: '100%' }}>
        <div className="flex items-center gap-3">
          <div className="flex items-center justify-center rounded-[8px]" style={{ width: 36, height: 36, background: 'linear-gradient(135deg, var(--color-accent), var(--color-violet))', color: '#fff' }}>NSH</div>
          <div>
            <div className="font-extrabold" style={{ fontSize: 'var(--text-lg)' }}>NSH HUB</div>
            <div className="section-label">Login Dashboard</div>
          </div>
        </div>
        {err && <div className="rounded-lg px-3 py-2" style={{ background: 'var(--color-danger-ghost)', color: 'var(--color-danger-fg)', fontSize: 'var(--text-sm)', border: '1px solid var(--color-danger)' }}>{err}</div>}
        <div>
          <label className="block font-semibold mb-1" style={{ fontSize: 'var(--text-xs)', color: 'var(--color-text-secondary)' }}>Username</label>
          <input className="input" value={username} onChange={e => setUsername(e.target.value)} placeholder="admin" autoFocus spellCheck={false} />
        </div>
        <div>
          <label className="block font-semibold mb-1" style={{ fontSize: 'var(--text-xs)', color: 'var(--color-text-secondary)' }}>Password</label>
          <input className="input" type="password" value={password} onChange={e => setPassword(e.target.value)} placeholder="••••••" />
        </div>
        <label className="flex items-center gap-2 cursor-pointer" style={{ fontSize: 'var(--text-sm)', color: 'var(--color-text-secondary)' }}>
          <input type="checkbox" checked={remember} onChange={e => setRemember(e.target.checked)} />
          Remember Me (7 hari)
        </label>
        <button className="btn btn-primary btn-md w-full justify-center" type="submit" disabled={loading}>
          {loading ? 'Memproses…' : 'Login'}
        </button>
        <div style={{ fontSize: 10, color: 'var(--color-text-dim)', textAlign: 'center' }}>Session 2 hari (tanpa centang) • httpOnly cookie + Bearer</div>
      </form>
    </div>
  )
}
