export const config = {
  port: Number(process.env.PORT || 4001),
  maxScreenshotBytes: Number(process.env.MAX_SCREENSHOT_BYTES || 350000),
  navigationTimeoutMs: Number(process.env.NAVIGATION_TIMEOUT_MS || 30000),
  scanTimeoutMs: Number(process.env.SCAN_TIMEOUT_MS || 60000),
  axeTags: (process.env.AXE_TAGS || 'wcag2a,wcag2aa,wcag21a,wcag21aa,wcag22a,wcag22aa')
    .split(',')
    .map((tag) => tag.trim())
    .filter(Boolean)
};
