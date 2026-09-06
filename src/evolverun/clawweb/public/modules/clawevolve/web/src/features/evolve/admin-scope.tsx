import { createContext, useContext, useEffect, useMemo, useState, type ReactNode } from 'react'
import { api } from '../../api/client'
import { useClientUser } from '../../hooks/useClientUser'

type EvolveAdminScopeValue = {
  available: boolean
  enabled: boolean
  setEnabled: (enabled: boolean) => void
  ownerUserId: string
  setOwnerUserId: (ownerUserId: string) => void
  ownerUserIds: string[]
}

const defaultValue: EvolveAdminScopeValue = {
  available: false,
  enabled: false,
  setEnabled: () => undefined,
  ownerUserId: '',
  setOwnerUserId: () => undefined,
  ownerUserIds: [],
}

const EvolveAdminScopeContext = createContext<EvolveAdminScopeValue>(defaultValue)

export function EvolveAdminScopeProvider({ children }: { children: ReactNode }) {
  const { user } = useClientUser()
  const available = user?.isClawEvolveAdmin === true
  const [enabled, setEnabledState] = useState(false)
  const [ownerUserId, setOwnerUserId] = useState('')
  const [ownerUserIds, setOwnerUserIds] = useState<string[]>([])

  useEffect(() => {
    if (!available) {
      setEnabledState(false)
      setOwnerUserId('')
    }
  }, [available])

  useEffect(() => {
    if (!available || !enabled) return
    void api.evolve.adminOwners()
      .then((result) => setOwnerUserIds(result.ownerUserIds))
      .catch(() => setOwnerUserIds([]))
  }, [available, enabled])

  const value = useMemo<EvolveAdminScopeValue>(() => ({
    available,
    enabled: available && enabled,
    setEnabled: (next) => {
      setEnabledState(next)
      if (!next) setOwnerUserId('')
    },
    ownerUserId,
    setOwnerUserId,
    ownerUserIds,
  }), [available, enabled, ownerUserId, ownerUserIds])

  return <EvolveAdminScopeContext.Provider value={value}>{children}</EvolveAdminScopeContext.Provider>
}

export function useEvolveAdminScope(): EvolveAdminScopeValue {
  return useContext(EvolveAdminScopeContext)
}
