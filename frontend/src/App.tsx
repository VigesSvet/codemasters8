import { BrowserRouter, Route, Routes } from 'react-router-dom'
import Shell from './components/Shell'
import { StoreProvider } from './lib/store'
import AdDetail from './pages/AdDetail'
import AdNew from './pages/AdNew'
import Ads from './pages/Ads'
import ComplexCard from './pages/ComplexCard'
import Complexes from './pages/Complexes'
import Dashboard from './pages/Dashboard'
import Developers from './pages/Developers'
import Login from './pages/Login'
import Method from './pages/Method'
import Queue from './pages/Queue'
import Reports from './pages/Reports'
import Residents from './pages/Residents'
import Sites from './pages/Sites'
import Sources from './pages/Sources'

export default function App() {
  return (
    <StoreProvider>
      <BrowserRouter>
        <Routes>
          <Route path="/login" element={<Login />} />
          <Route element={<Shell />}>
            <Route index element={<Dashboard />} />
            <Route path="complexes" element={<Complexes />} />
            <Route path="complexes/:id" element={<ComplexCard />} />
            <Route path="ads" element={<Ads />} />
            <Route path="ads/new" element={<AdNew />} />
            <Route path="ads/:id" element={<AdDetail />} />
            <Route path="queue" element={<Queue />} />
            <Route path="developers" element={<Developers />} />
            <Route path="sites" element={<Sites />} />
            <Route path="method" element={<Method />} />
            <Route path="sources" element={<Sources />} />
            <Route path="residents" element={<Residents />} />
            <Route path="reports" element={<Reports />} />
          </Route>
        </Routes>
      </BrowserRouter>
    </StoreProvider>
  )
}
