import { createContext, useContext, useEffect, useState, type ReactNode } from 'react'
import { api } from '../../api/client'
import { useClientUser } from '../../hooks/useClientUser'

export type EvolveHostCapabilities = { skillManagement: boolean; stageCustomization: boolean }
const unavailable: EvolveHostCapabilities = { skillManagement: false, stageCustomization: false }
const Context = createContext(unavailable)
export const useEvolveHostCapabilities = () => useContext(Context)

export function EvolveCapabilitiesProvider({ children }: { children: ReactNode }) {
  const { authState } = useClientUser()
  const [capabilities, setCapabilities] = useState(unavailable)
  useEffect(() => {
    if (authState !== 'ready') return
    let active = true
    void api.evolve.capabilities().then((value) => {
      if (active) setCapabilities(value)
    }).catch(() => { if (active) setCapabilities(unavailable) })
    return () => { active = false }
  }, [authState])
  return <Context.Provider value={capabilities}>{children}</Context.Provider>
}
