import React from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { createMemoryRouter, RouterProvider, useLocation, useParams } from 'react-router-dom'
import { describe, expect, it } from 'vitest'
import EvolveRoutes from '../EvolveRoutes'

function PageParams() {
  const { ownerUserId, domainId, templateName, benchRunId } = useParams()
  const { pathname, search } = useLocation()
  return <output>{`${ownerUserId ?? ''}|${domainId ?? ''}|${templateName ?? ''}|${benchRunId ?? ''}|${pathname}${search}`}</output>
}

function createHost(entry: string) {
  return createMemoryRouter([
    { path: '/evolve/tools/tclog', element: <output>private-tclog</output> },
    { path: '/evolve/*', element: <EvolveRoutes><PageParams /></EvolveRoutes> },
  ], { initialEntries: [entry] })
}

describe('ClawEvolve routes below the host wildcard', () => {
  it.each([
    ['/evolve/bench/domains/owner-a/test_new', 'owner-a|test_new|||'],
    ['/evolve/bench/domains/owner-a/test_new/', 'owner-a|test_new|||'],
    ['/evolve/bench/domains/owner-a/test_new/templates/template-a?edit=1', 'owner-a|test_new|template-a||'],
    ['/evolve/bench/domains/owner-a/test_new/templates/template%20one', 'owner-a|test_new|template one||'],
    ['/evolve/bench/runs/BR-example', '|||BR-example|'],
    ['/evolve/bench/domains', '||||'],
    ['/evolve/bench/runs', '||||'],
    ['/evolve', '||||'],
    ['/evolve/tasks', '||||'],
    ['/evolve/new?type=repair', '||||'],
    ['/evolve/runs/EV-example', '||||'],
    ['/evolve/repair-runs/REPAIR-example', '||||'],
    ['/evolve/session-runs/SA-example', '||||'],
    ['/evolve/packs/PACK-example', '||||'],
  ])('preserves parameters and URL when opening %s directly', (url, params) => {
    const router = createHost(url)
    try {
      expect(renderToStaticMarkup(<RouterProvider router={router} />)).toBe(`<output>${params}${url}</output>`)
    } finally {
      router.dispose()
    }
  })

  it('updates parameters through domain, template, run and back navigation', async () => {
    const router = createHost('/evolve/bench/domains')
    const render = () => renderToStaticMarkup(<RouterProvider router={router} />)
    try {
      expect(render()).toContain('||||/evolve/bench/domains')
      await router.navigate('/evolve/bench/domains/owner-a/test_new')
      expect(render()).toContain('owner-a|test_new|||')
      await router.navigate('/evolve/bench/domains/owner-a/test_new/templates/template-a')
      expect(render()).toContain('owner-a|test_new|template-a||')
      await router.navigate('/evolve/bench/runs/BR-example')
      expect(render()).toContain('|||BR-example|')
      await router.navigate(-1)
      expect(render()).toContain('owner-a|test_new|template-a||')
    } finally {
      router.dispose()
    }
  })

  it('leaves the more specific private host route in control', () => {
    const router = createHost('/evolve/tools/tclog')
    try {
      expect(renderToStaticMarkup(<RouterProvider router={router} />)).toBe('<output>private-tclog</output>')
    } finally {
      router.dispose()
    }
  })
})
