import { describe, expect, it } from 'vitest'
import { resolveEvolveBotPrefill as resolve } from '../../components/evolveBotPrefill'
const bot = { botId: 'bot-1', ownerId: 'owner-1', env: 'prod' }
const query = 'botId=bot-1&botEnv=prod'
describe('external Bot prefill', () => {
  it('selects the unique authorized Bot without changing its identity', () => {
    expect(resolve(query, [bot]).bot).toBe(bot)
  })
  it.each(['ownerId', 'entityId'])('accepts %s as the owner constraint', key => {
    expect(resolve(`${query}&${key}=owner-1`, [bot]).bot).toBe(bot)
    expect(resolve(`${query}&${key}=other`, [bot]).bot).toBeUndefined()
  })
  it('accepts equal aliases and rejects conflicting aliases', () => {
    expect(resolve(`${query}&ownerId=owner-1&entityId=owner-1`, [bot]).bot).toBe(bot)
    expect(resolve(`${query}&ownerId=owner-1&entityId=other`, [bot]).error).toContain('不一致')
  })
  it('does not fall back to a different environment or unauthorized target', () => {
    expect(resolve(query, [{ ...bot, env: 'pre' }]).bot).toBeUndefined()
    expect(resolve(query, []).error).toContain('权限')
  })
  it('requires an owner for default and preserves ambiguity', () => {
    expect(resolve('botId=default&botEnv=prod', []).error).toContain('必须')
    expect(resolve(query, [bot, { ...bot, ownerId: 'other' }]).error).toContain('多个')
    const fallback = { ...bot, botId: 'default' }
    expect(resolve('botId=default&botEnv=prod&entityId=owner-1', [fallback]).bot).toBe(fallback)
  })
  it('validates required values and trims inputs', () => {
    expect(resolve('botId=bot-1', [bot]).error).toContain('botEnv')
    expect(resolve('botId=+&botEnv=prod', [bot]).error).toContain('botId')
    expect(resolve(`${query}&ownerId=+owner-1+`, [bot]).bot).toBe(bot)
  })
})
