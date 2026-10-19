import { BrowserRouter, Navigate, Outlet, Route, Routes, useLocation } from 'react-router-dom'
import Shell from './components/Shell'
import { StoreProvider, useStore } from './lib/store'
import AdDetail from './pages/AdDetail'
import AdNew from './pages/AdNew'
import Ads from './pages/Ads'
import Changes from './pages/Changes'
import ComplexCard from './pages/ComplexCard'
import Check from './pages/Check'
import Complexes from './pages/Complexes'
import Dashboard from './pages/Dashboard'
import Landing from './pages/Landing'
import Developers from './pages/Developers'
import Discovered from './pages/Discovered'
import Field from './pages/Field'
import Ops from './pages/Ops'
import Login from './pages/Login'
import Method from './pages/Method'
import Queue from './pages/Queue'
import RegistryCard from './pages/RegistryCard'
import Reports from './pages/Reports'
import Sellers from './pages/Sellers'
import Residents from './pages/Residents'
import SignalCard from './pages/SignalCard'
import SignalPrint from './pages/SignalPrint'
import Signals from './pages/Signals'
import Sites from './pages/Sites'
import Sources from './pages/Sources'
import Tv from './pages/Tv'

/** Всё, кроме /login, лендинга (/about, а для гостя — и /) и публичной проверки ЖК (/check), — только после входа; иначе на вход с возвратом на текущий адрес.
 *  После выхода (здесь или в соседней вкладке) — на вход без возврата: страница была чужой сессии */
function RequireUser() {
  const { user, signedOut } = useStore()
  const loc = useLocation()
  // гость на главной видит лендинг, а не форму входа; остальные адреса — на вход с возвратом
  if (!user && loc.pathname === '/') return <Landing />
  if (!user) return <Navigate to={signedOut ? '/login' : `/login?next=${encodeURIComponent(loc.pathname + loc.search + loc.hash)}`} replace />
  return <Outlet />
}

export default function App() {
  return (
    <StoreProvider>
      <BrowserRouter>
        <Routes>
          <Route path="/login" element={<Login />} />
          <Route path="/about" element={<Landing />} />
          <Route path="/check" element={<Check />} />
          <Route element={<RequireUser />}>
            <Route path="/signals/:id/print" element={<SignalPrint />} />
            <Route path="/tv" element={<Tv />} />
            <Route path="/field" element={<Field />} />
            <Route element={<Shell />}>
              <Route index element={<Dashboard />} />
              <Route path="ops" element={<Ops />} />
              <Route path="complexes" element={<Complexes />} />
              <Route path="complexes/:id" element={<ComplexCard />} />
              <Route path="registry/:key" element={<RegistryCard />} />
              <Route path="ads" element={<Ads />} />
              <Route path="ads/new" element={<AdNew />} />
              <Route path="ads/:id" element={<AdDetail />} />
              <Route path="queue" element={<Queue />} />
              <Route path="signals" element={<Signals />} />
              <Route path="signals/:id" element={<SignalCard />} />
              <Route path="developers" element={<Developers />} />
              <Route path="discovered" element={<Discovered />} />
              <Route path="sites" element={<Sites />} />
              <Route path="method" element={<Method />} />
              <Route path="sources" element={<Sources />} />
              <Route path="residents" element={<Residents />} />
              <Route path="reports" element={<Reports />} />
              <Route path="sellers" element={<Sellers />} />
              <Route path="changes" element={<Changes />} />
            </Route>
          </Route>
        </Routes>
      </BrowserRouter>
    </StoreProvider>
  )
}
