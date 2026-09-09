import React, { type ReactNode } from 'react'
import { Route, Routes } from 'react-router-dom'

// Mount below /evolve/*. Named matches supply useParams() to the existing pages;
// choosing a page from location.pathname alone does not create route parameters.
export default function EvolveRoutes({ children }: { children: ReactNode }) {
  return (
    <Routes>
      <Route path="bench/domains" element={children} />
      <Route path="bench/domains/:ownerUserId/:domainId" element={children} />
      <Route path="bench/domains/:ownerUserId/:domainId/templates/:templateName" element={children} />
      <Route path="bench/runs" element={children} />
      <Route path="bench/runs/:benchRunId" element={children} />
      <Route path="*" element={children} />
    </Routes>
  )
}
