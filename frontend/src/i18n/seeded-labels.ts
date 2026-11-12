type Translate = (key: string, options?: Record<string, unknown>) => string

// Names PanWatch seeds into the database (server.py DATA_SOURCE_SEEDS and the
// default-account migration). They are stored data, so the API returns them in
// Chinese. A seeded row is shown with the active locale's label, keyed by its
// stable identifiers; a row the user renamed no longer matches and keeps its
// own name. If a seed name changes upstream the row falls back to the stored
// name rather than showing a stale translation.
const SEEDED_DATA_SOURCE_NAMES: Record<string, string> = {
  'quote.tencent': '腾讯行情',
  'quote.eastmoney': '东方财富行情',
  'quote.sina': 'Sina 行情',
  'quote.yfinance': 'YFinance 行情',
  'kline.tencent': '腾讯K线',
  'kline.eastmoney': '东方财富 K线',
  'kline.stooq': 'Stooq K线',
  'kline.yahoo': 'Yahoo K线',
  'news.xueqiu': '雪球资讯',
  'news.eastmoney_news': '东方财富资讯',
  'news.eastmoney': '东方财富公告',
  'flash_news.cls': '财联社快讯',
  'flash_news.sina': '新浪7x24快讯',
  'flash_news.eastmoney': '东方财富7x24快讯',
  'events.eastmoney': '东方财富事件日历',
  'fundamentals.tencent': '腾讯基本面',
  'fundamentals.eastmoney': '东方财富基本面',
  'capital_flow.eastmoney': '东方财富资金流',
  'capital_flow.sina': '新浪资金流',
  'dragon_tiger.eastmoney': '东财龙虎榜',
  'margin.eastmoney': '东财融资融券',
  'shareholders.eastmoney': '东财股东户数',
  'northbound.ths': '同花顺北向资金',
  'dividend.eastmoney': '东财分红',
  'chart.xueqiu': '雪球K线截图',
  'chart.eastmoney': '东方财富K线截图',
}

const SEEDED_DEFAULT_ACCOUNT_NAME = '默认账户'

/** Display name for a data source: localized while it still has its seeded name. */
export function localizeDataSourceName(type: string, provider: string, name: string, t: Translate): string {
  const key = `${type}.${provider}`
  const seeded = SEEDED_DATA_SOURCE_NAMES[key]
  if (seeded === undefined || name.trim() !== seeded) return name
  return t(`dataSources.sourceNames.${type}.${provider}`)
}

/** Display name for an account: localized while it is still the seeded default account. */
export function localizeAccountName(name: string, t: Translate): string {
  return name.trim() === SEEDED_DEFAULT_ACCOUNT_NAME ? t('stocksPage.messages.defaultAccountName') : name
}
