import { describe, expect, it } from 'vitest'
import { buildKlineSuggestion } from '@/lib/kline-scorer'
import { localizeAgentDescription, localizeAgentName } from '@/i18n/agent-labels'
import { mapLoggerName } from '@/lib/logger-map'
import { localizeAccountName, localizeDataSourceName } from '@/i18n/seeded-labels'
import { localizeTechnicalStatus } from '@panwatch/biz-ui/components/kline-indicators'
import { bizUi as enBizUi } from '@/i18n/locales/en-US/biz-ui'
import { configuration as enConfiguration } from '@/i18n/locales/en-US/configuration'
import { configuration as zhConfiguration } from '@/i18n/locales/zh-CN/configuration'

function translator(resource: unknown) {
  return (key: string) => {
    let value: unknown = resource
    for (const part of key.split('.')) value = (value as Record<string, unknown>)[part]
    return String(value)
  }
}

describe('dynamic interface labels', () => {
  it('builds local technical signals in the active language', () => {
    const tr = translator(enBizUi.kline)
    const result = buildKlineSuggestion({
      trend: '多头排列',
      macd_status: '金叉',
      macd_hist: 0.2,
      rsi_status: '偏强',
    }, false, tr)

    expect(result.action).toBe('buy')
    expect(result.action_label).toBe('Buy')
    expect(result.signal).toContain('Bullish MAs')
    expect(result.evidence.every(item => !/[\u4e00-\u9fff]/.test(item.text))).toBe(true)
  })

  it('localizes backend technical statuses before they enter English AI context', () => {
    const tr = translator(enBizUi.kline)
    expect(localizeTechnicalStatus('多头排列', tr)).toBe('Bullish alignment')
    expect(localizeTechnicalStatus('MACD 死叉', tr)).toBe('Death cross')
    expect(localizeTechnicalStatus('放量', tr)).toBe('High volume')
  })

  it('localizes known Agent catalog fields while preserving custom Agents', () => {
    const tr = translator(enConfiguration)
    expect(localizeAgentName('daily_report', '收盘复盘', tr)).toBe('Closing review')
    expect(localizeAgentDescription('daily_report', '旧说明', tr)).toContain('daily closing report')
    expect(localizeAgentName('custom_agent', 'My Agent', tr)).toBe('My Agent')
  })

  it('localizes logger names from stable module identifiers', () => {
    expect(mapLoggerName('src.agents.daily_report.worker', 'en-US')).toBe('Closing review')
    expect(mapLoggerName('src.core.scheduler', 'zh-CN')).toBe('调度器')
  })

  it('localizes seeded data source names while preserving renamed and custom sources', () => {
    const en = translator(enConfiguration)
    const zh = translator(zhConfiguration)
    expect(localizeDataSourceName('capital_flow', 'eastmoney', '东方财富资金流', en)).toBe('Eastmoney capital flow')
    expect(localizeDataSourceName('flash_news', 'sina', '新浪7x24快讯', en)).toBe('Sina 24/7 flash news')
    expect(localizeDataSourceName('capital_flow', 'eastmoney', '东方财富资金流', zh)).toBe('东方财富资金流')
    expect(localizeDataSourceName('capital_flow', 'eastmoney', 'My flow feed', en)).toBe('My flow feed')
    expect(localizeDataSourceName('quote', 'custom_vendor', '自定义行情', en)).toBe('自定义行情')
  })

  it('has an English and Chinese label for every seeded data source', () => {
    const en = enConfiguration.dataSources.sourceNames as Record<string, Record<string, string>>
    const zh = zhConfiguration.dataSources.sourceNames as Record<string, Record<string, string>>
    for (const [type, providers] of Object.entries(zh)) {
      for (const [provider, seededName] of Object.entries(providers)) {
        const english = localizeDataSourceName(type, provider, seededName, translator(enConfiguration))
        expect(english).toBe(en[type][provider])
        expect(/[\u4e00-\u9fff]/.test(english)).toBe(false)
      }
    }
  })

  it('localizes only the seeded default account name', () => {
    const en = translator(enConfiguration)
    expect(localizeAccountName('默认账户', en)).toBe('Default account')
    expect(localizeAccountName('Brokerage', en)).toBe('Brokerage')
  })
})
